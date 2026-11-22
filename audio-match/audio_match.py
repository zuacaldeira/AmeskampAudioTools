#!/usr/bin/env python3
"""
audio_match.py - Identify a recording by fingerprinting it and querying AcoustID.

Generates a Chromaprint acoustic fingerprint from the input audio file and looks
it up against the AcoustID database, returning MusicBrainz recording metadata
(title, artist, releases) for the best matches. Useful for identifying covers,
samples, and "what song is this?" lookups against open music databases.

Requires an AcoustID API key (free, register at https://acoustid.org/new-application).
Pass via --api-key or ACOUSTID_API_KEY env var.

Usage examples:
    python audio_match.py song.mp3
    python audio_match.py song.flac --json
    python audio_match.py song.mp3 --limit 5
"""

import argparse
import json
import os
import sys

import acoustid


DEFAULT_META = "recordings releases"
DEFAULT_LIMIT = 3
MUSICBRAINZ_RECORDING_URL = "https://musicbrainz.org/recording/{rid}"
MUSICBRAINZ_RELEASE_URL = "https://musicbrainz.org/release/{rel}"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Identify audio against the AcoustID / MusicBrainz database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s song.mp3
      Print best matches as human-readable text.

  %(prog)s song.flac --json
      Print full results as JSON (good for piping into jq).

  %(prog)s clip.wav --limit 10
      Show up to 10 candidate matches instead of the default 3.

  ACOUSTID_API_KEY=xxxx %(prog)s song.mp3
      Read the API key from the environment instead of --api-key.
""",
    )
    parser.add_argument("input", help="Input audio file path.")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("ACOUSTID_API_KEY"),
        help="AcoustID application key (or set ACOUSTID_API_KEY env var).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON instead of human-readable text.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum number of matches to show (default: {DEFAULT_LIMIT}).",
    )
    return parser.parse_args()


def _date_str(date_obj):
    """Format an AcoustID release date dict ({year, month, day}) as ISO-ish string."""
    if not isinstance(date_obj, dict):
        return None
    year = date_obj.get("year")
    if not year:
        return None
    parts = [f"{year:04d}"]
    month = date_obj.get("month")
    if month:
        parts.append(f"{month:02d}")
        day = date_obj.get("day")
        if day:
            parts.append(f"{day:02d}")
    return "-".join(parts)


def _normalize_recording(score, recording):
    """Flatten one AcoustID recording payload into a stable output shape."""
    rid = recording.get("id")
    artists = [a.get("name") for a in recording.get("artists", []) if a.get("name")]

    releases = []
    for rel in recording.get("releases", []) or []:
        releases.append({
            "id": rel.get("id"),
            "title": rel.get("title"),
            "date": _date_str(rel.get("date")),
            "country": rel.get("country"),
            "track_count": rel.get("track_count"),
            "release_group_id": (rel.get("releasegroup") or {}).get("id"),
            "url": MUSICBRAINZ_RELEASE_URL.format(rel=rel["id"]) if rel.get("id") else None,
        })

    return {
        "score": round(float(score), 4),
        "recording_id": rid,
        "title": recording.get("title"),
        "artists": artists,
        "artist": ", ".join(artists) if artists else None,
        "duration_seconds": recording.get("duration"),
        "musicbrainz_url": MUSICBRAINZ_RECORDING_URL.format(rid=rid) if rid else None,
        "releases": releases,
    }


def fingerprint(path):
    """Return (duration_seconds, fingerprint_string) for the given audio file."""
    return acoustid.fingerprint_file(path)


def lookup(api_key, path, limit=DEFAULT_LIMIT, meta=DEFAULT_META):
    """Fingerprint ``path`` and query AcoustID. Returns the structured result dict.

    Raises:
        FileNotFoundError: file does not exist.
        acoustid.NoBackendError: neither fpcalc nor chromaprint+audioread available.
        acoustid.FingerprintGenerationError: fingerprinting failed.
        acoustid.WebServiceError: API call failed (bad key, rate limit, etc.).
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")

    duration, fp = fingerprint(path)
    raw = acoustid.lookup(api_key, fp, duration, meta=meta)

    # pyacoustid 1.3.x does not raise on API-level errors — it just returns the JSON.
    # Surface them as WebServiceError so callers can handle uniformly.
    if raw.get("status") != "ok":
        err = (raw.get("error") or {}).get("message") or "unknown error"
        raise acoustid.WebServiceError(f"AcoustID API error: {err}")

    matches = []
    for result in raw.get("results", []) or []:
        score = result.get("score", 0.0)
        recordings = result.get("recordings") or []
        if not recordings:
            matches.append({
                "score": round(float(score), 4),
                "recording_id": None,
                "title": None,
                "artists": [],
                "artist": None,
                "duration_seconds": None,
                "musicbrainz_url": None,
                "releases": [],
                "acoustid_id": result.get("id"),
            })
            continue
        for rec in recordings:
            entry = _normalize_recording(score, rec)
            entry["acoustid_id"] = result.get("id")
            matches.append(entry)

    matches.sort(key=lambda m: m["score"], reverse=True)
    if limit:
        matches = matches[:limit]

    return {
        "file": {
            "path": os.path.abspath(path),
            "name": os.path.basename(path),
            "size_bytes": os.path.getsize(path),
        },
        "fingerprint": {
            "duration_seconds": duration,
            "length": len(fp),
        },
        "match_count": len(matches),
        "matches": matches,
        "api_status": raw.get("status"),
    }


def print_human(result):
    f = result["file"]
    fp = result["fingerprint"]
    print(f"File:  {f['name']}")
    print(f"  Path:        {f['path']}")
    print(f"  Fingerprint: {fp['length']} chars, audio duration {fp['duration_seconds']:.2f}s")
    print()
    matches = result["matches"]
    if not matches:
        print("No matches found.")
        return
    print(f"Matches ({len(matches)}):")
    for i, m in enumerate(matches, 1):
        score_pct = f"{m['score'] * 100:.1f}%"
        title = m.get("title") or "(unknown title)"
        artist = m.get("artist") or "(unknown artist)"
        print(f"\n  [{i}] {score_pct}  {artist} — {title}")
        if m.get("duration_seconds"):
            print(f"      Duration: {m['duration_seconds']}s")
        if m.get("musicbrainz_url"):
            print(f"      MusicBrainz: {m['musicbrainz_url']}")
        releases = m.get("releases") or []
        if releases:
            print(f"      Releases ({len(releases)}):")
            for r in releases[:5]:
                bits = [r.get("title") or "(untitled)"]
                if r.get("date"):
                    bits.append(r["date"])
                if r.get("country"):
                    bits.append(r["country"])
                print(f"        - {'  '.join(bits)}")
            if len(releases) > 5:
                print(f"        ...and {len(releases) - 5} more")


def main():
    args = parse_args()
    if not args.api_key:
        print(
            "Error: AcoustID API key required. Pass --api-key or set ACOUSTID_API_KEY.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        result = lookup(args.api_key, args.input, limit=args.limit)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except acoustid.NoBackendError:
        print(
            "Error: No fingerprinting backend available. Install libchromaprint-tools "
            "(provides fpcalc) or the chromaprint Python package.",
            file=sys.stderr,
        )
        sys.exit(3)
    except acoustid.FingerprintGenerationError as e:
        print(f"Error: Could not fingerprint file: {e}", file=sys.stderr)
        sys.exit(4)
    except acoustid.WebServiceError as e:
        print(f"Error: AcoustID lookup failed: {e}", file=sys.stderr)
        sys.exit(5)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print_human(result)


if __name__ == "__main__":
    main()
