#!/usr/bin/env python3
"""
audio_similar_web.py - Web service for sounds-alike matching via CLAP embeddings.

Read-only: queries against a pre-built catalog. The catalog is populated
out-of-band via the CLI (`audio_similar.py index <dir>`); the web service
just embeds the uploaded query and ranks catalog entries by cosine.

Configuration:
  AUDIO_SIMILAR_CATALOG  Catalog directory (default: /catalog inside Docker).

Run:
  python audio_similar_web.py [--port PORT]
"""

import argparse
import glob
import os
import sys
import threading
import time
import uuid
import tempfile

from flask import Flask, request, jsonify, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_similar import Catalog, _rank_matches, DB_NAME  # noqa: E402

app = Flask(__name__)

CATALOG_DIR = os.environ.get("AUDIO_SIMILAR_CATALOG", "/catalog")
UPLOAD_DIR = tempfile.mkdtemp(prefix="audio_similar_")
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB
CLEANUP_MAX_AGE = 3600  # 1 hour
DEFAULT_TOP = 10

app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE


def _cleanup_old_files():
    while True:
        time.sleep(300)
        try:
            now = time.time()
            for p in glob.glob(os.path.join(UPLOAD_DIR, "*")):
                if now - os.path.getmtime(p) > CLEANUP_MAX_AGE:
                    os.remove(p)
        except Exception:
            pass


threading.Thread(target=_cleanup_old_files, daemon=True).start()


def _catalog_present():
    return os.path.isfile(os.path.join(CATALOG_DIR, DB_NAME))


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/status")
def status():
    """Catalog summary — used by the UI to render the 'catalog state' panel."""
    if not _catalog_present():
        return jsonify({
            "ready": False,
            "catalog_path": os.path.abspath(CATALOG_DIR),
            "message": "Catalog is empty or missing. Run "
                       "`audio_similar.py index <dir> --catalog "
                       f"{CATALOG_DIR}` to populate it.",
        })
    try:
        cat = Catalog(CATALOG_DIR)
        return jsonify({
            "ready": True,
            "catalog_path": os.path.abspath(CATALOG_DIR),
            "track_count": cat.count(),
            "model": cat.model_name,
            "dim": cat.dim,
        })
    except Exception as e:
        return jsonify({"ready": False, "error": str(e)}), 500


@app.route("/api/query", methods=["POST"])
def query():
    if not _catalog_present():
        return jsonify({
            "error": "Catalog is empty. Populate it with `audio_similar.py "
                     f"index <dir> --catalog {CATALOG_DIR}` first."
        }), 503

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["file"]
    if f.filename == "":
        return jsonify({"error": "No file selected"}), 400

    try:
        top = int(request.form.get("top", DEFAULT_TOP))
    except ValueError:
        top = DEFAULT_TOP
    top = max(1, min(top, 50))

    job_id = str(uuid.uuid4())[:8]
    ext = os.path.splitext(f.filename)[1]
    input_path = os.path.join(UPLOAD_DIR, f"{job_id}_input{ext}")

    try:
        f.save(input_path)
        from embed import embed
        cat = Catalog(CATALOG_DIR)
        if cat.count() == 0:
            return jsonify({"error": "Catalog is empty."}), 503

        qvec = embed(input_path)
        if qvec.shape[0] != cat.dim:
            return jsonify({
                "error": f"Embedding dim ({qvec.shape[0]}) doesn't match catalog ({cat.dim})."
            }), 500
        pairs = _rank_matches(cat, qvec, top)

        matches = [
            {
                "score": round(float(score), 4),
                "name": os.path.basename(row["path"]),
                "path": row["path"],
                "title": row["title"],
                "artist": row["artist"],
                "album": row["album"],
                "duration_seconds": row["duration_seconds"],
            }
            for row, score in pairs
        ]
        return jsonify({
            "query": {"name": f.filename},
            "catalog": {"track_count": cat.count(), "model": cat.model_name},
            "matches": matches,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(input_path):
            try:
                os.remove(input_path)
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(description="Audio Similar Web Service")
    parser.add_argument("-p", "--port", type=int, default=5003, help="Port (default: 5003)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    args = parser.parse_args()

    print(f"\n  Audio Similar Web Service")
    print(f"  Catalog: {os.path.abspath(CATALOG_DIR)}  "
          f"({'present' if _catalog_present() else 'NOT FOUND'})")
    print(f"  Open http://localhost:{args.port} in your browser\n")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
