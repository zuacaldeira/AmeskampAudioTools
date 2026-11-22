#!/usr/bin/env python3
"""
audio_discover.py - Find related tracks by chaining audio identification
with ListenBrainz's collaborative-filtering similarity.

Pipeline:
    audio file
      -> Chromaprint fingerprint (libchromaprint)
      -> AcoustID lookup (needs an AcoustID API key)
      -> MusicBrainz recording MBID
      -> ListenBrainz `similar-recordings` (behavior-based similarity)
      -> ranked list of related tracks

Different flavor of similarity from audio-similar: this one is
"people-who-listened-to-X-also-listened-to-Y", not "sounds-like-X".
Only works for recordings that (a) are in AcoustID/MusicBrainz and (b)
have enough listener data on ListenBrainz to produce similars.

Requires AcoustID API key — pass --api-key or set ACOUSTID_API_KEY.

Usage examples:
    audio_discover.py song.mp3
    audio_discover.py song.flac --json --top 25
"""

import argparse
import json
import os
import sys

import acoustid
import requests


LISTENBRAINZ_SIMILAR_URL = "https://labs.api.listenbrainz.org/similar-recordings/json"
# Broadest-coverage default algorithm from the labs enum (~7500 days of
# listening data, 50-result cap, contribution-5 weighting).
DEFAULT_ALGORITHM = (
    "session_based_days_7500_session_300_contribution_5_threshold_15_limit_50_skip_30"
)
MUSICBRAINZ_RECORDING_URL = "https://musicbrainz.org/recording/{mbid}"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Discover related tracks via AcoustID + ListenBrainz.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s song.mp3
      Print top-10 related tracks (behavior-based similarity).

  %(prog)s song.flac --json --top 25
      JSON output with up to 25 recommendations.
""",
    )
    parser.add_argument("input", help="Input audio file.")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("ACOUSTID_API_KEY"),
        help="AcoustID application key (or set ACOUSTID_API_KEY env var).",
    )
    parser.add_argument("--top", type=int, default=10,
                        help="Max number of related tracks to return (default: 10).")
    parser.add_argument("--algorithm", default=DEFAULT_ALGORITHM,
                        help="ListenBrainz similarity algorithm.")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON instead of human-readable text.")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Identification (Chromaprint + AcoustID)
# ---------------------------------------------------------------------------

def identify(api_key, path):
    """Fingerprint + AcoustID lookup. Returns the best identified recording
    as ``{"mbid", "title", "artist", "score"}`` or ``None`` if no AcoustID
    match has a MusicBrainz recording ID."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")
    duration, fp = acoustid.fingerprint_file(path)
    raw = acoustid.lookup(api_key, fp, duration, meta="recordings")
    if raw.get("status") != "ok":
        msg = (raw.get("error") or {}).get("message") or "unknown error"
        raise acoustid.WebServiceError(f"AcoustID API error: {msg}")

    # Walk results in score order — already sorted desc by AcoustID.
    for result in raw.get("results", []) or []:
        for rec in result.get("recordings") or []:
            mbid = rec.get("id")
            if not mbid:
                continue
            artists = [
                a.get("name") for a in rec.get("artists") or [] if a.get("name")
            ]
            return {
                "mbid": mbid,
                "title": rec.get("title"),
                "artist": ", ".join(artists) if artists else None,
                "duration_seconds": rec.get("duration"),
                "match_score": round(float(result.get("score", 0.0)), 4),
                "musicbrainz_url": MUSICBRAINZ_RECORDING_URL.format(mbid=mbid),
            }
    return None


# ---------------------------------------------------------------------------
# Similar-tracks lookup (ListenBrainz labs)
# ---------------------------------------------------------------------------

def find_similar(mbid, algorithm=DEFAULT_ALGORITHM, top=10, timeout=20):
    """POST to ListenBrainz labs similar-recordings and return a normalized list.

    Niche tracks with little listener data may return an empty list — that's
    a real "no recommendations available" outcome, not an error.
    """
    payload = [{"recording_mbids": [mbid], "algorithm": algorithm}]
    resp = requests.post(LISTENBRAINZ_SIMILAR_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json() or []
    # API returns sorted by descending score.
    out = []
    for r in data[:top]:
        rec_mbid = r.get("recording_mbid")
        out.append({
            "mbid": rec_mbid,
            "title": r.get("recording_name"),
            "artist": r.get("artist_credit_name"),
            "album": r.get("release_name"),
            "release_mbid": r.get("release_mbid"),
            "score": int(r.get("score") or 0),
            "musicbrainz_url": (
                MUSICBRAINZ_RECORDING_URL.format(mbid=rec_mbid) if rec_mbid else None
            ),
        })
    return out


def discover(api_key, path, top=10, algorithm=DEFAULT_ALGORITHM):
    """Full pipeline: identify ``path`` then fetch related recordings.

    Returns ``{"identified", "related", "algorithm"}``. ``identified`` is None
    when no AcoustID match was found; ``related`` is then also empty.
    """
    identified = identify(api_key, path)
    related = []
    if identified:
        related = find_similar(identified["mbid"], algorithm=algorithm, top=top)
    return {
        "file": {
            "path": os.path.abspath(path),
            "name": os.path.basename(path),
        },
        "identified": identified,
        "related": related,
        "algorithm": algorithm,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_human(result):
    f = result["file"]
    print(f"File: {f['name']}")
    print(f"  Path: {f['path']}")
    print()

    ident = result.get("identified")
    if not ident:
        print("Identification: no AcoustID match — can't fetch related tracks.")
        return
    print(f"Identified: {ident['artist'] or '(unknown)'} — {ident['title'] or '(untitled)'}")
    print(f"  MBID:        {ident['mbid']}")
    print(f"  Match score: {ident['match_score']:.2%}")
    print(f"  MusicBrainz: {ident['musicbrainz_url']}")
    print()

    related = result.get("related") or []
    if not related:
        print("Related: none — ListenBrainz has no similarity data for this recording.")
        print("         (Niche/rare tracks often hit this; popular catalog matches usually return many.)")
        return
    print(f"Related tracks ({len(related)}):")
    for i, r in enumerate(related, 1):
        print(f"  [{i:2d}]  {(r['artist'] or '(unknown)'):<35}  {r['title'] or '(untitled)'}")
        if r.get("album"):
            print(f"        on {r['album']}  (score {r['score']})")
        if r.get("musicbrainz_url"):
            print(f"        {r['musicbrainz_url']}")


def main():
    args = parse_args()
    if not args.api_key:
        print("Error: AcoustID API key required. Pass --api-key or set ACOUSTID_API_KEY.",
              file=sys.stderr)
        sys.exit(2)

    try:
        result = discover(args.api_key, args.input, top=args.top, algorithm=args.algorithm)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr); sys.exit(1)
    except acoustid.NoBackendError:
        print("Error: no Chromaprint backend (install libchromaprint-tools for fpcalc, "
              "or the chromaprint Python package).", file=sys.stderr); sys.exit(3)
    except acoustid.FingerprintGenerationError as e:
        print(f"Error: fingerprint failed: {e}", file=sys.stderr); sys.exit(4)
    except acoustid.WebServiceError as e:
        print(f"Error: AcoustID lookup failed: {e}", file=sys.stderr); sys.exit(5)
    except requests.RequestException as e:
        print(f"Error: ListenBrainz request failed: {e}", file=sys.stderr); sys.exit(6)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print_human(result)


if __name__ == "__main__":
    main()
