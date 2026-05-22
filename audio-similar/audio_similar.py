#!/usr/bin/env python3
"""
audio_similar.py - Find sounds-alike matches via CLAP embeddings.

Two subcommands:

  index <dir>     Walk a directory of audio files, compute a CLAP embedding
                  for each, and store everything in an on-disk catalog.

  query <song>    Embed a query file and rank the catalog by cosine
                  similarity. (See audio_similar query --help.)

The catalog lives in a single directory: a SQLite file with track metadata
and a memmapped float32 file with the embedding vectors. The two stay in
sync via per-track offsets.

Usage examples:
    audio_similar.py index ~/Music --catalog ./catalog
    audio_similar.py index ~/Music --catalog ./catalog --force
"""

import argparse
import os
import sqlite3
import sys
import time

import numpy as np


AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".ogg", ".opus",
    ".m4a", ".aac", ".aiff", ".aif", ".wma", ".ape", ".mpc",
}
DEFAULT_CATALOG = "./catalog"
DB_NAME = "index.db"
VECTORS_NAME = "vectors.npy"


# ---------------------------------------------------------------------------
# Catalog storage
# ---------------------------------------------------------------------------

class Catalog:
    """SQLite metadata + a contiguous float32 vectors file, one row per track."""

    def __init__(self, root, dim=None, model_name=None):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)
        self.db_path = os.path.join(self.root, DB_NAME)
        self.vec_path = os.path.join(self.root, VECTORS_NAME)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        # Reconcile dim/model with any existing metadata.
        existing_dim = self._meta_get("dim")
        existing_model = self._meta_get("model_name")
        if existing_dim is not None:
            self.dim = int(existing_dim)
            self.model_name = existing_model
            if dim is not None and dim != self.dim:
                raise ValueError(
                    f"Catalog at {self.root} was built with dim={self.dim} "
                    f"but the current model returns dim={dim}. Use a fresh catalog."
                )
            if model_name is not None and existing_model and existing_model != model_name:
                # Different model but same dim — allow, but warn.
                print(
                    f"  WARNING: catalog model is {existing_model}, current is {model_name}. "
                    f"Embeddings may not be directly comparable.",
                    file=sys.stderr,
                )
        else:
            if dim is None:
                raise ValueError("New catalog requires --dim (set automatically when indexing).")
            self.dim = int(dim)
            self.model_name = model_name
            self._meta_set("dim", str(self.dim))
            if model_name:
                self._meta_set("model_name", model_name)

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS tracks (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                path             TEXT    NOT NULL UNIQUE,
                mtime            REAL    NOT NULL,
                size_bytes       INTEGER,
                title            TEXT,
                artist           TEXT,
                album            TEXT,
                duration_seconds REAL,
                vector_offset    INTEGER NOT NULL UNIQUE,
                indexed_at       REAL    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        self.conn.commit()

    def _meta_get(self, key):
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _meta_set(self, key, value):
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_track(self, path):
        return self.conn.execute(
            "SELECT * FROM tracks WHERE path = ?", (path,)
        ).fetchone()

    def count(self):
        return self.conn.execute("SELECT COUNT(*) AS n FROM tracks").fetchone()["n"]

    def all_tracks(self):
        return list(self.conn.execute(
            "SELECT * FROM tracks ORDER BY vector_offset"
        ))

    def _next_offset(self):
        row = self.conn.execute("SELECT MAX(vector_offset) AS m FROM tracks").fetchone()
        return 0 if row["m"] is None else row["m"] + 1

    def write_vector(self, offset, vec):
        """Write a single vector at the given offset (in-place, append-safe)."""
        vec = np.ascontiguousarray(vec, dtype=np.float32)
        if vec.shape != (self.dim,):
            raise ValueError(f"Expected vector of shape ({self.dim},), got {vec.shape}")
        bytes_per_vec = self.dim * 4
        with open(self.vec_path, "ab+") as f:
            current = os.path.getsize(self.vec_path)
            target = (offset + 1) * bytes_per_vec
            if current < target:
                # Pad with zeros up to the target offset (shouldn't happen in
                # append-only paths but keeps the file always-sized).
                f.write(b"\x00" * (target - current))
        with open(self.vec_path, "r+b") as f:
            f.seek(offset * bytes_per_vec)
            f.write(vec.tobytes())

    def upsert_track(self, path, mtime, size_bytes, tags, vec):
        """Insert or update a track + its vector. Returns the row's offset."""
        existing = self.get_track(path)
        if existing is None:
            offset = self._next_offset()
            self.conn.execute(
                """INSERT INTO tracks
                   (path, mtime, size_bytes, title, artist, album, duration_seconds,
                    vector_offset, indexed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (path, mtime, size_bytes,
                 tags.get("title"), tags.get("artist"), tags.get("album"),
                 tags.get("duration_seconds"),
                 offset, time.time()),
            )
        else:
            offset = existing["vector_offset"]
            self.conn.execute(
                """UPDATE tracks
                   SET mtime = ?, size_bytes = ?, title = ?, artist = ?, album = ?,
                       duration_seconds = ?, indexed_at = ?
                   WHERE path = ?""",
                (mtime, size_bytes,
                 tags.get("title"), tags.get("artist"), tags.get("album"),
                 tags.get("duration_seconds"),
                 time.time(), path),
            )
        self.write_vector(offset, vec)
        self.conn.commit()
        return offset

    def vectors(self):
        """Return an (N, dim) memmap of all stored vectors. Read-only."""
        n = self.count()
        if n == 0 or not os.path.exists(self.vec_path):
            return np.empty((0, self.dim), dtype=np.float32)
        return np.memmap(self.vec_path, dtype=np.float32, mode="r", shape=(n, self.dim))


# ---------------------------------------------------------------------------
# Tag extraction (lightweight; falls back to filename if mutagen can't read)
# ---------------------------------------------------------------------------

def read_tags(path):
    """Best-effort tag/duration extraction via mutagen. Returns {} on failure."""
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        return {}
    try:
        m = MutagenFile(path, easy=True)
    except Exception:
        return {}
    if m is None:
        return {}
    tags = {}
    raw = getattr(m, "tags", None) or {}
    for key in ("title", "artist", "album"):
        val = raw.get(key)
        if isinstance(val, (list, tuple)):
            val = val[0] if val else None
        if val:
            tags[key] = str(val).strip() or None
    info = getattr(m, "info", None)
    if info is not None and getattr(info, "length", None):
        tags["duration_seconds"] = float(info.length)
    return tags


def iter_audio_files(root):
    """Yield absolute paths to audio files under ``root`` (recursive)."""
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                yield os.path.abspath(os.path.join(dirpath, name))


# ---------------------------------------------------------------------------
# `index` subcommand
# ---------------------------------------------------------------------------

def cmd_index(args):
    if not os.path.isdir(args.directory):
        print(f"Error: not a directory: {args.directory}", file=sys.stderr)
        return 1

    # Lazy-import the embedder so `audio_similar.py query --help` doesn't load CLAP.
    from embed import embed, DEFAULT_MODEL

    print(f"Catalog: {os.path.abspath(args.catalog)}")
    print(f"Source:  {os.path.abspath(args.directory)}")
    print(f"Model:   {DEFAULT_MODEL}")
    print()

    files = sorted(iter_audio_files(args.directory))
    if not files:
        print("No audio files found.")
        return 0
    print(f"Found {len(files)} audio file(s). Indexing...")

    catalog = None
    n_added = n_skipped = n_failed = n_updated = 0
    t0 = time.time()

    for i, path in enumerate(files, 1):
        try:
            mtime = os.path.getmtime(path)
            size_bytes = os.path.getsize(path)
        except OSError as e:
            print(f"  [{i}/{len(files)}] {os.path.basename(path)}: {e}", file=sys.stderr)
            n_failed += 1
            continue

        # Lazy-init catalog now that we know the embedding dim from the first track.
        if catalog is None:
            try:
                vec = embed(path)
            except Exception as e:
                print(f"  [{i}/{len(files)}] {os.path.basename(path)}: embed failed: {e}", file=sys.stderr)
                n_failed += 1
                continue
            catalog = Catalog(args.catalog, dim=vec.shape[0], model_name=DEFAULT_MODEL)
            # Now check skip-if-unchanged for this very first file too.
            existing = catalog.get_track(path)
            if existing and not args.force and existing["mtime"] == mtime:
                n_skipped += 1
                print(f"  [{i}/{len(files)}] {os.path.basename(path)}  (skip — unchanged)")
                continue
            tags = read_tags(path)
            catalog.upsert_track(path, mtime, size_bytes, tags, vec)
            print(f"  [{i}/{len(files)}] {os.path.basename(path)}  added")
            n_added += 1
            continue

        existing = catalog.get_track(path)
        if existing and not args.force and existing["mtime"] == mtime:
            n_skipped += 1
            if args.verbose:
                print(f"  [{i}/{len(files)}] {os.path.basename(path)}  (skip — unchanged)")
            continue

        try:
            vec = embed(path)
        except Exception as e:
            print(f"  [{i}/{len(files)}] {os.path.basename(path)}: embed failed: {e}", file=sys.stderr)
            n_failed += 1
            continue

        tags = read_tags(path)
        catalog.upsert_track(path, mtime, size_bytes, tags, vec)
        action = "updated" if existing else "added"
        if action == "updated":
            n_updated += 1
        else:
            n_added += 1
        print(f"  [{i}/{len(files)}] {os.path.basename(path)}  {action}")

    elapsed = time.time() - t0
    total = catalog.count() if catalog else 0
    print()
    print(f"Done in {elapsed:.1f}s. "
          f"added={n_added} updated={n_updated} skipped={n_skipped} failed={n_failed}. "
          f"Catalog now has {total} track(s).")
    return 0 if n_failed == 0 else 2


# ---------------------------------------------------------------------------
# Stub for `query` (implemented in the next task)
# ---------------------------------------------------------------------------

def cmd_query(args):
    print("Error: 'query' is not implemented yet — coming in the next commit.",
          file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description="Find sounds-alike matches via CLAP embeddings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    idx = sub.add_parser("index", help="Build/update the catalog from a directory.")
    idx.add_argument("directory", help="Directory of audio files to embed (recursive).")
    idx.add_argument("--catalog", default=DEFAULT_CATALOG,
                     help=f"Catalog directory (default: {DEFAULT_CATALOG}).")
    idx.add_argument("--force", action="store_true",
                     help="Re-embed even if path+mtime is unchanged.")
    idx.add_argument("-v", "--verbose", action="store_true",
                     help="Log skipped tracks too.")
    idx.set_defaults(func=cmd_index)

    qry = sub.add_parser("query", help="Find similar tracks for a query file (in next commit).")
    qry.add_argument("song")
    qry.add_argument("--catalog", default=DEFAULT_CATALOG)
    qry.set_defaults(func=cmd_query)

    return parser


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    args = build_parser().parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
