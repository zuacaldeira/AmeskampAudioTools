#!/usr/bin/env python3
"""
audio_events.py - Detect sound events in audio using PANNs CNN14 (AudioSet 527 classes).

Covers animals (dog bark, cat meow, bird), weather (rain, thunder, wind),
speech/conversation, music, vehicles, alarms, machinery, gunshot/gunfire,
glass breaking, and several hundred other sound categories from Google's
AudioSet ontology.

Usage examples:
    python audio_events.py clip.wav
    python audio_events.py clip.wav --detect "dog bark,rain,gunshot"
    python audio_events.py clip.wav --timeline --json
    python audio_events.py clip.wav --top 20
"""

import argparse
import json
import os
import sys

import numpy as np

PANNS_SAMPLE_RATE = 32000
DEFAULT_THRESHOLD = 0.1
_TAGGER = None
_DETECTOR = None
_LABELS = None


def load_labels():
    global _LABELS
    if _LABELS is None:
        from panns_inference.config import labels
        _LABELS = list(labels)
    return _LABELS


def _get_tagger():
    global _TAGGER
    if _TAGGER is None:
        from panns_inference import AudioTagging
        _TAGGER = AudioTagging(checkpoint_path=None, device="cpu")
    return _TAGGER


def _get_detector():
    global _DETECTOR
    if _DETECTOR is None:
        from panns_inference import SoundEventDetection
        _DETECTOR = SoundEventDetection(checkpoint_path=None, device="cpu")
    return _DETECTOR


def load_audio(path, sr=PANNS_SAMPLE_RATE):
    """Load audio as mono 32 kHz float32 (PANNs' expected input rate)."""
    import librosa
    y, _ = librosa.load(path, sr=sr, mono=True)
    return y.astype(np.float32)


def tag_clip(audio):
    """Return a (527,) probability vector for the whole clip."""
    clip = audio[np.newaxis, :]
    clipwise, _ = _get_tagger().inference(clip)
    return np.asarray(clipwise[0])


def top_events(probs, labels, n=10):
    idx = np.argsort(-probs)[:n]
    return [{"label": labels[i], "confidence": round(float(probs[i]), 4)} for i in idx]


def detect_terms(probs, labels, terms, threshold=DEFAULT_THRESHOLD):
    """For each query term, return matching AudioSet labels ranked by confidence.

    A query matches a label case-insensitively as a substring. "dog bark" also
    gets split into tokens so it'll find labels containing both "dog" AND "bark"
    even when they don't appear adjacently in the label name.
    """
    lower_labels = [label.lower() for label in labels]
    results = []
    for raw in terms:
        term = raw.strip().lower()
        if not term:
            continue
        tokens = term.split()
        matched_idx = []
        for i, label in enumerate(lower_labels):
            if term in label or all(tok in label for tok in tokens):
                matched_idx.append(i)
        if not matched_idx:
            results.append({"query": raw, "matches": [], "detected": False})
            continue
        matched_idx.sort(key=lambda i: -probs[i])
        best = matched_idx[0]
        results.append({
            "query": raw,
            "matches": [
                {"label": labels[i], "confidence": round(float(probs[i]), 4)}
                for i in matched_idx[:5]
            ],
            "detected": bool(probs[best] >= threshold),
            "best_label": labels[best],
            "best_confidence": round(float(probs[best]), 4),
            "threshold": threshold,
        })
    return results


def event_timeline(audio, labels, top_n=6):
    """Per-second max confidence for the top-N classes (from frame-level SED)."""
    clip = audio[np.newaxis, :]
    framewise = _get_detector().inference(clip)
    # SED returns (batch, time_frames, classes)
    framewise = np.asarray(framewise[0])
    total_frames, n_classes = framewise.shape
    duration_s = len(audio) / PANNS_SAMPLE_RATE
    if duration_s <= 0 or total_frames == 0:
        return {"seconds": 0, "events": []}
    frames_per_second = max(int(round(total_frames / duration_s)), 1)
    n_seconds = max(int(np.ceil(duration_s)), 1)
    per_second = np.zeros((n_seconds, n_classes), dtype=np.float32)
    for s in range(n_seconds):
        start = s * frames_per_second
        end = min((s + 1) * frames_per_second, total_frames)
        if start >= total_frames:
            break
        per_second[s] = framewise[start:end].max(axis=0)

    peak = per_second.max(axis=0)
    top_idx = np.argsort(-peak)[:top_n]
    events = []
    for i in top_idx:
        events.append({
            "label": labels[int(i)],
            "peak_confidence": round(float(peak[i]), 4),
            "per_second": [round(float(per_second[s, i]), 4) for s in range(n_seconds)],
        })
    return {"seconds": n_seconds, "events": events}


def analyze(path, top=10, detect_terms_list=None, with_timeline=False, threshold=DEFAULT_THRESHOLD):
    audio = load_audio(path)
    labels = load_labels()
    probs = tag_clip(audio)

    result = {
        "file": os.path.basename(path),
        "duration_s": round(len(audio) / PANNS_SAMPLE_RATE, 2),
        "top_events": top_events(probs, labels, top),
    }
    if detect_terms_list:
        result["detections"] = detect_terms(probs, labels, detect_terms_list, threshold)
    if with_timeline:
        result["timeline"] = event_timeline(audio, labels)
    return result


def parse_args():
    parser = argparse.ArgumentParser(
        description="Detect sound events in audio (AudioSet 527-class tagging via PANNs).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s clip.wav
      Print the top-10 AudioSet labels detected in the clip.

  %(prog)s clip.wav --detect "dog bark,rain,gunshot"
      Check specifically for dog-bark, rain, and gunshot-like sounds.

  %(prog)s clip.wav --timeline
      Also show where in the clip each top event occurs (per-second max).

  %(prog)s clip.wav --top 20 --json
      JSON output with the top-20 classes.
""",
    )
    parser.add_argument("input", help="Input audio file.")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of human-readable text.")
    parser.add_argument("--top", type=int, default=10, help="Number of top classes to report (default: 10).")
    parser.add_argument("--detect", help="Comma-separated search terms to check for (e.g. 'dog bark,rain').")
    parser.add_argument("--timeline", action="store_true", help="Include a per-second event timeline (runs the SED model).")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"Confidence threshold for --detect (default: {DEFAULT_THRESHOLD}).")
    return parser.parse_args()


def _bar(confidence, width=30):
    n_full = int(round(confidence * width))
    return "\u2588" * n_full + "\u2591" * (width - n_full)


def _heatmap_line(per_second):
    """Render per-second confidences as a text heatmap using block characters."""
    glyphs = [" ", "\u2591", "\u2592", "\u2593", "\u2588"]
    out = []
    for c in per_second:
        if c >= 0.75:
            out.append(glyphs[4])
        elif c >= 0.5:
            out.append(glyphs[3])
        elif c >= 0.25:
            out.append(glyphs[2])
        elif c >= 0.1:
            out.append(glyphs[1])
        else:
            out.append(glyphs[0])
    return "".join(out)


def print_human(result):
    print(f"\nFile: {result['file']}  ({result['duration_s']}s)")

    print("\nTop events:")
    max_label = max(len(e["label"]) for e in result["top_events"])
    for event in result["top_events"]:
        label = event["label"].ljust(min(max_label, 44))
        print(f"  {label}  {event['confidence']:.3f}  {_bar(event['confidence'])}")

    if "detections" in result:
        print("\nDetections:")
        for d in result["detections"]:
            if not d["matches"]:
                print(f"  [{d['query']}] no matching AudioSet labels")
                continue
            status = "DETECTED" if d["detected"] else "below threshold"
            print(f"  [{d['query']}] {status} — best: {d['best_label']} ({d['best_confidence']:.3f})")
            for m in d["matches"][:3]:
                print(f"      {m['label']}: {m['confidence']:.3f}")

    if "timeline" in result:
        tl = result["timeline"]
        print(f"\nTimeline  ({tl['seconds']}s, per-second max confidence)")
        for event in tl["events"]:
            print(f"  {event['label']}  peak {event['peak_confidence']:.3f}")
            print(f"    |{_heatmap_line(event['per_second'])}|")


def main():
    args = parse_args()
    if not os.path.isfile(args.input):
        print(f"Error: {args.input} not found.", file=sys.stderr)
        sys.exit(1)

    detect_list = None
    if args.detect:
        detect_list = [t.strip() for t in args.detect.split(",") if t.strip()]

    if not args.json:
        print(f"Loading {args.input} and running AudioSet tagger (CPU)...", file=sys.stderr)

    result = analyze(
        args.input,
        top=args.top,
        detect_terms_list=detect_list,
        with_timeline=args.timeline,
        threshold=args.threshold,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_human(result)


if __name__ == "__main__":
    main()
