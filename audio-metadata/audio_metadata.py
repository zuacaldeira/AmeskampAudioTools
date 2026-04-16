#!/usr/bin/env python3
"""
audio_metadata.py - Extract metadata (tags, stream info, cover art) from audio files.

Reads tags and technical stream information (duration, bitrate, sample rate,
channels, bit depth) from audio files across common formats: MP3, FLAC, WAV,
OGG/Vorbis, Opus, M4A/MP4, AIFF, WMA, APE, Musepack.

Usage examples:
    python audio_metadata.py song.mp3
    python audio_metadata.py song.flac --json
    python audio_metadata.py song.mp3 --cover cover.jpg
    python audio_metadata.py song.m4a --raw
"""

import argparse
import base64
import json
import os
import sys

from mutagen import File as MutagenFile
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus


# Common tag field names mutagen's EasyID3/EasyMP4/Vorbis expose.
EASY_TAG_KEYS = [
    "title",
    "artist",
    "albumartist",
    "album",
    "date",
    "genre",
    "tracknumber",
    "discnumber",
    "composer",
    "performer",
    "copyright",
    "isrc",
    "bpm",
    "comment",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract metadata from audio files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s song.mp3
      Print human-readable metadata for song.mp3.

  %(prog)s song.flac --json
      Print metadata as JSON (good for piping into jq).

  %(prog)s album.mp3 --cover cover.jpg
      Extract embedded cover art to cover.jpg.

  %(prog)s track.m4a --raw
      Include raw format-specific tag keys in addition to normalized ones.
""",
    )
    parser.add_argument("input", help="Input audio file path.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output metadata as JSON instead of human-readable text.",
    )
    parser.add_argument(
        "--cover",
        metavar="PATH",
        help="Extract embedded cover art (if any) to the given file path.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Include raw format-specific tag keys in the output.",
    )
    return parser.parse_args()


def format_duration(seconds):
    if seconds is None:
        return None
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_size(bytes_):
    if bytes_ < 1024:
        return f"{bytes_} B"
    if bytes_ < 1024 * 1024:
        return f"{bytes_ / 1024:.1f} KB"
    return f"{bytes_ / (1024 * 1024):.2f} MB"


def _first(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _stringify(value):
    value = _first(value)
    if value is None:
        return None
    return str(value).strip() or None


def _decode_bytes(data, max_len=200):
    """Decode a bytes payload for display. Tries UTF-16 LE / UTF-8, falls back to hex."""
    if not isinstance(data, (bytes, bytearray)):
        return str(data)
    # Heuristic: alternating-null pattern -> UTF-16 LE
    odd_bytes = data[1::2]
    if odd_bytes and odd_bytes.count(0) / len(odd_bytes) > 0.8:
        try:
            s = data.decode("utf-16-le").rstrip("\x00").strip()
            if s and all(c.isprintable() or c.isspace() for c in s):
                return s if len(s) <= max_len else s[:max_len] + "…"
        except Exception:
            pass
    try:
        s = data.decode("utf-8").rstrip("\x00").strip()
        if s and all(c.isprintable() or c.isspace() for c in s):
            return s if len(s) <= max_len else s[:max_len] + "…"
    except Exception:
        pass
    preview = data[:16].hex()
    suffix = "…" if len(data) > 16 else ""
    return f"<{len(data)} bytes: {preview}{suffix}>"


def extract_tags(audio):
    """Return a dict of normalized common tags across formats."""
    tags = {}

    # Easy interfaces (ID3, MP4, Vorbis) expose flat string dicts.
    easy = getattr(audio, "tags", None)
    if easy is None:
        return tags

    # Try the easy mapping first.
    try:
        as_dict = dict(easy)
    except Exception:
        as_dict = {}

    for key in EASY_TAG_KEYS:
        if key in as_dict:
            tags[key] = _stringify(as_dict[key])

    # ID3 non-easy: map common frame IDs.
    if isinstance(easy, ID3) or easy.__class__.__name__ == "ID3":
        id3_map = {
            "TIT2": "title",
            "TPE1": "artist",
            "TPE2": "albumartist",
            "TALB": "album",
            "TDRC": "date",
            "TYER": "date",
            "TCON": "genre",
            "TRCK": "tracknumber",
            "TPOS": "discnumber",
            "TCOM": "composer",
            "TCOP": "copyright",
            "TSRC": "isrc",
            "TBPM": "bpm",
            "TPUB": "publisher",
            "TENC": "encoded_by",
            "TSSE": "encoder",
            "TEXT": "lyricist",
            "TLAN": "language",
            "TMED": "media",
            "TMOO": "mood",
            "TOAL": "original_album",
            "TOPE": "original_artist",
            "TOFN": "original_filename",
            "TORY": "original_year",
            "TKEY": "initial_key",
        }
        for frame_id, name in id3_map.items():
            if frame_id in easy and name not in tags:
                tags[name] = _stringify(easy[frame_id].text)
        # Comments (COMM:*)
        comments = [f.text for f in easy.getall("COMM")]
        if comments and "comment" not in tags:
            flat = [str(_first(c)) for c in comments if c]
            if flat:
                tags["comment"] = "; ".join(flat)

    # MP4/M4A atom keys.
    if isinstance(audio, MP4):
        mp4_map = {
            "\xa9nam": "title",
            "\xa9ART": "artist",
            "aART": "albumartist",
            "\xa9alb": "album",
            "\xa9day": "date",
            "\xa9gen": "genre",
            "\xa9wrt": "composer",
            "cprt": "copyright",
            "\xa9cmt": "comment",
        }
        for atom, name in mp4_map.items():
            if atom in easy and name not in tags:
                tags[name] = _stringify(easy[atom])
        if "trkn" in easy and "tracknumber" not in tags:
            trkn = _first(easy["trkn"])
            if trkn:
                tags["tracknumber"] = f"{trkn[0]}/{trkn[1]}" if trkn[1] else str(trkn[0])
        if "disk" in easy and "discnumber" not in tags:
            disk = _first(easy["disk"])
            if disk:
                tags["discnumber"] = f"{disk[0]}/{disk[1]}" if disk[1] else str(disk[0])

    return {k: v for k, v in tags.items() if v}


def extract_extended_tags(audio):
    """Proprietary / format-specific frames beyond the common tag set.

    Covers ID3 TXXX (user-defined text), PRIV (private), GEOB (embedded objects),
    UFID (unique file IDs), WXXX (user URL), plus MP4 freeform atoms (``----:*``)
    used by Apple/iTunes for custom metadata. Values are decoded best-effort;
    binary blobs are rendered as hex previews.
    """
    out = {}
    tags = getattr(audio, "tags", None)
    if tags is None:
        return out

    if hasattr(tags, "getall"):
        for frame in tags.getall("TXXX"):
            desc = frame.desc or ""
            key = f"TXXX:{desc}" if desc else "TXXX"
            out[key] = "; ".join(str(t) for t in frame.text) or ""
        for frame in tags.getall("PRIV"):
            key = f"PRIV:{frame.owner}"
            out[key] = _decode_bytes(frame.data)
        for frame in tags.getall("GEOB"):
            desc = frame.desc or "(no desc)"
            parts = []
            if frame.mime:
                parts.append(frame.mime)
            if frame.filename:
                parts.append(f"filename={frame.filename}")
            parts.append(f"{len(frame.data)} bytes")
            out[f"GEOB:{desc}"] = ", ".join(parts)
        for frame in tags.getall("UFID"):
            out[f"UFID:{frame.owner}"] = _decode_bytes(frame.data)
        for frame in tags.getall("WXXX"):
            desc = frame.desc or ""
            key = f"WXXX:{desc}" if desc else "WXXX"
            out[key] = getattr(frame, "url", "") or str(frame)
        for frame in tags.getall("WOAR"):
            out["WOAR"] = getattr(frame, "url", "") or str(frame)

    # MP4 freeform atoms (Apple ``----:mean:name`` used by iTunes/DAWs for custom tags).
    if isinstance(audio, MP4):
        try:
            for key, value in dict(tags).items():
                if key.startswith("----"):
                    val = _first(value)
                    if isinstance(val, (bytes, bytearray)):
                        out[key] = _decode_bytes(val)
                    else:
                        out[key] = str(val)
        except Exception:
            pass

    return out


def extract_stream_info(audio, file_size):
    """Technical info from the audio.info object, varying by format."""
    info = audio.info
    result = {
        "duration_seconds": getattr(info, "length", None),
        "bitrate_bps": getattr(info, "bitrate", None),
        "sample_rate_hz": getattr(info, "sample_rate", None),
        "channels": getattr(info, "channels", None),
        "bits_per_sample": getattr(info, "bits_per_sample", None),
        "codec": audio.__class__.__name__,
    }
    # Derive average bitrate when missing (e.g. raw WAV).
    if not result["bitrate_bps"] and result["duration_seconds"]:
        result["bitrate_bps"] = int(file_size * 8 / result["duration_seconds"])
    return {k: v for k, v in result.items() if v is not None}


def extract_cover(audio):
    """Return (image_bytes, mime) for embedded cover art, or (None, None)."""
    # FLAC: Picture objects in .pictures
    if isinstance(audio, FLAC) and audio.pictures:
        pic = audio.pictures[0]
        return pic.data, pic.mime or "image/jpeg"

    tags = audio.tags
    if tags is None:
        return None, None

    # ID3 / MP3: APIC frame
    apic = None
    if hasattr(tags, "getall"):
        frames = tags.getall("APIC")
        if frames:
            apic = frames[0]
    if apic is not None:
        return apic.data, getattr(apic, "mime", None) or "image/jpeg"

    # MP4 / M4A: covr atom
    if isinstance(audio, MP4) and "covr" in tags:
        covr = tags["covr"][0]
        # mutagen uses MP4Cover with imageformat 13 (JPEG) or 14 (PNG)
        fmt = getattr(covr, "imageformat", None)
        mime = "image/png" if fmt == 14 else "image/jpeg"
        return bytes(covr), mime

    # Vorbis/Opus: METADATA_BLOCK_PICTURE (base64-encoded Picture block)
    if isinstance(audio, (OggVorbis, OggOpus)):
        pics = tags.get("metadata_block_picture", [])
        if pics:
            try:
                data = base64.b64decode(pics[0])
                pic = Picture(data)
                return pic.data, pic.mime or "image/jpeg"
            except Exception:
                return None, None

    return None, None


def build_metadata(path):
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")

    audio = MutagenFile(path)
    if audio is None:
        raise ValueError(f"Unsupported or unreadable audio format: {path}")

    file_size = os.path.getsize(path)

    cover_bytes, cover_mime = extract_cover(audio)
    cover = None
    if cover_bytes:
        cover = {
            "mime": cover_mime,
            "size_bytes": len(cover_bytes),
        }

    return {
        "file": {
            "path": os.path.abspath(path),
            "name": os.path.basename(path),
            "size_bytes": file_size,
        },
        "stream": extract_stream_info(audio, file_size),
        "tags": extract_tags(audio),
        "extended_tags": extract_extended_tags(audio),
        "cover": cover,
        "_audio": audio,
        "_cover_bytes": cover_bytes,
        "_cover_mime": cover_mime,
    }


def print_human(meta, include_raw=False):
    f = meta["file"]
    print(f"File:  {f['name']}")
    print(f"  Path: {f['path']}")
    print(f"  Size: {format_size(f['size_bytes'])}")

    s = meta["stream"]
    print("\nStream:")
    if "codec" in s:
        print(f"  Codec:         {s['codec']}")
    if "duration_seconds" in s:
        print(f"  Duration:      {format_duration(s['duration_seconds'])}  ({s['duration_seconds']:.2f}s)")
    if "bitrate_bps" in s:
        print(f"  Bitrate:       {s['bitrate_bps'] // 1000} kbps")
    if "sample_rate_hz" in s:
        print(f"  Sample rate:   {s['sample_rate_hz']} Hz")
    if "channels" in s:
        ch = s["channels"]
        label = {1: "mono", 2: "stereo"}.get(ch, f"{ch} ch")
        print(f"  Channels:      {ch} ({label})")
    if "bits_per_sample" in s:
        print(f"  Bit depth:     {s['bits_per_sample']} bit")

    tags = meta["tags"]
    print("\nTags:")
    if not tags:
        print("  (no tags)")
    else:
        width = max(len(k) for k in tags.keys())
        for key in sorted(tags.keys()):
            print(f"  {key.ljust(width)}  {tags[key]}")

    ext = meta.get("extended_tags") or {}
    if ext:
        print("\nExtended tags:")
        width = max(len(k) for k in ext.keys())
        for key in sorted(ext.keys()):
            print(f"  {key.ljust(width)}  {ext[key]}")

    if meta["cover"]:
        c = meta["cover"]
        print(f"\nCover: {c['mime']}, {format_size(c['size_bytes'])}")
    else:
        print("\nCover: (none)")

    if include_raw:
        raw_tags = meta["_audio"].tags
        print("\nRaw tags:")
        if raw_tags is None:
            print("  (none)")
        else:
            try:
                for key, value in dict(raw_tags).items():
                    print(f"  {key}: {value!r}")
            except Exception:
                print(f"  {raw_tags!r}")


def main():
    args = parse_args()

    try:
        meta = build_metadata(args.input)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.cover:
        if not meta["_cover_bytes"]:
            print("Error: no embedded cover art found.", file=sys.stderr)
            sys.exit(2)
        with open(args.cover, "wb") as fh:
            fh.write(meta["_cover_bytes"])
        print(f"Cover extracted: {args.cover} ({format_size(len(meta['_cover_bytes']))}, {meta['_cover_mime']})")
        if not args.json:
            return

    if args.json:
        public = {k: v for k, v in meta.items() if not k.startswith("_")}
        print(json.dumps(public, indent=2, ensure_ascii=False))
    else:
        print_human(meta, include_raw=args.raw)


if __name__ == "__main__":
    main()
