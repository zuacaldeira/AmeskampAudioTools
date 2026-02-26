#!/usr/bin/env python3
"""
silence_trimmer.py - Trim long silence in audio files down to a max duration.

Detects silent segments in an audio file and trims any silence longer than
a specified duration (default: 1 second) down to that duration. All non-silent
audio is preserved intact.

Supports any format that ffmpeg supports (WAV, MP3, FLAC, OGG, AAC, etc.).

Usage examples:
    python silence_trimmer.py input.wav
    python silence_trimmer.py input.mp3 -o trimmed.mp3
    python silence_trimmer.py input.flac -t -40 -m 500 -f wav
    python silence_trimmer.py input.wav --max-silence 1500 --threshold -35
"""

import argparse
import os
import sys
import time

from pydub import AudioSegment
from pydub.silence import detect_nonsilent


def parse_args():
    parser = argparse.ArgumentParser(
        description="Trim silence in audio files down to a maximum duration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s recording.wav
      Trim silence in recording.wav to 1s max, output to recording_trimmed.wav

  %(prog)s podcast.mp3 -o clean_podcast.mp3
      Trim silence and save to a specific output file.

  %(prog)s lecture.flac -t -35 -m 500
      Use -35 dBFS silence threshold and trim silence to 500ms max.

  %(prog)s concert.wav -f mp3 --bitrate 192k
      Trim silence and export as MP3 at 192kbps.

  %(prog)s song.ogg --min-silence-len 300
      Only trim silence segments that are at least 300ms long.

  %(prog)s interview.wav --dry-run
      Preview what would happen without writing a file.
""",
    )

    parser.add_argument(
        "input",
        help="Input audio file path (any format supported by ffmpeg).",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output file path. Default: <input>_trimmed.<ext>",
    )
    parser.add_argument(
        "-t", "--threshold",
        type=float,
        default=-40.0,
        help="Silence threshold in dBFS. Audio below this level is considered "
             "silence. Lower values = more permissive. (default: -40)",
    )
    parser.add_argument(
        "-m", "--max-silence",
        type=int,
        default=1000,
        help="Maximum silence duration in milliseconds. Silence longer than "
             "this will be trimmed to this length. (default: 1000)",
    )
    parser.add_argument(
        "--min-silence-len",
        type=int,
        default=100,
        help="Minimum length (ms) of a quiet segment to be considered silence. "
             "(default: 100)",
    )
    parser.add_argument(
        "-f", "--format",
        help="Output format (e.g., wav, mp3, flac, ogg). Default: same as input.",
    )
    parser.add_argument(
        "--bitrate",
        help="Output bitrate for lossy formats (e.g., 128k, 192k, 320k).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze the file and report what would change, but don't write output.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print detailed information about detected segments.",
    )

    return parser.parse_args()


def format_time(ms):
    """Format milliseconds as MM:SS.mmm"""
    total_seconds = ms / 1000.0
    minutes = int(total_seconds // 60)
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:06.3f}"


def trim_silence(audio, max_silence_ms, threshold_dbfs, min_silence_len, verbose=False):
    """
    Trim silence in an audio segment so that no silent gap exceeds max_silence_ms.

    Returns the processed AudioSegment and stats dict.
    """
    # Detect non-silent chunks: list of [start_ms, end_ms]
    nonsilent_ranges = detect_nonsilent(
        audio,
        min_silence_len=min_silence_len,
        silence_thresh=threshold_dbfs,
    )

    if not nonsilent_ranges:
        # Entire file is silence
        if verbose:
            print("  Entire file is silence.")
        trimmed = audio[:max_silence_ms] if len(audio) > max_silence_ms else audio
        return trimmed, {
            "original_duration_ms": len(audio),
            "trimmed_duration_ms": len(trimmed),
            "silence_segments_found": 1,
            "silence_segments_trimmed": 1 if len(audio) > max_silence_ms else 0,
        }

    result = AudioSegment.empty()
    silence_segments_found = 0
    silence_segments_trimmed = 0

    # Handle leading silence (before first non-silent chunk)
    if nonsilent_ranges[0][0] > 0:
        leading_silence_ms = nonsilent_ranges[0][0]
        silence_segments_found += 1
        if leading_silence_ms > max_silence_ms:
            silence_segments_trimmed += 1
            if verbose:
                print(f"  Leading silence: {format_time(0)} - {format_time(leading_silence_ms)} "
                      f"({leading_silence_ms}ms) -> trimmed to {max_silence_ms}ms")
            result += audio[:max_silence_ms]
        else:
            if verbose:
                print(f"  Leading silence: {format_time(0)} - {format_time(leading_silence_ms)} "
                      f"({leading_silence_ms}ms) -> kept")
            result += audio[:leading_silence_ms]

    for i, (start, end) in enumerate(nonsilent_ranges):
        # Add the non-silent chunk
        result += audio[start:end]

        # Add silence gap between this chunk and the next
        if i < len(nonsilent_ranges) - 1:
            next_start = nonsilent_ranges[i + 1][0]
            gap_ms = next_start - end
            silence_segments_found += 1

            if gap_ms > max_silence_ms:
                silence_segments_trimmed += 1
                if verbose:
                    print(f"  Silence gap: {format_time(end)} - {format_time(next_start)} "
                          f"({gap_ms}ms) -> trimmed to {max_silence_ms}ms")
                # Use actual silence from the audio (preserving any background noise character)
                result += audio[end:end + max_silence_ms]
            else:
                if verbose and gap_ms > 0:
                    print(f"  Silence gap: {format_time(end)} - {format_time(next_start)} "
                          f"({gap_ms}ms) -> kept")
                result += audio[end:next_start]

    # Handle trailing silence (after last non-silent chunk)
    last_end = nonsilent_ranges[-1][1]
    if last_end < len(audio):
        trailing_silence_ms = len(audio) - last_end
        silence_segments_found += 1
        if trailing_silence_ms > max_silence_ms:
            silence_segments_trimmed += 1
            if verbose:
                print(f"  Trailing silence: {format_time(last_end)} - {format_time(len(audio))} "
                      f"({trailing_silence_ms}ms) -> trimmed to {max_silence_ms}ms")
            result += audio[last_end:last_end + max_silence_ms]
        else:
            if verbose:
                print(f"  Trailing silence: {format_time(last_end)} - {format_time(len(audio))} "
                      f"({trailing_silence_ms}ms) -> kept")
            result += audio[last_end:]

    stats = {
        "original_duration_ms": len(audio),
        "trimmed_duration_ms": len(result),
        "silence_segments_found": silence_segments_found,
        "silence_segments_trimmed": silence_segments_trimmed,
    }

    return result, stats


def main():
    args = parse_args()

    # Validate input file
    if not os.path.isfile(args.input):
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    # Determine output format and path
    input_base, input_ext = os.path.splitext(args.input)
    input_ext = input_ext.lstrip(".")

    out_format = args.format or input_ext or "wav"

    if args.output:
        output_path = args.output
    else:
        output_path = f"{input_base}_trimmed.{out_format}"

    # Load audio
    print(f"Loading: {args.input}")
    start_time = time.time()

    try:
        audio = AudioSegment.from_file(args.input)
    except Exception as e:
        print(f"Error loading audio file: {e}", file=sys.stderr)
        sys.exit(1)

    load_time = time.time() - start_time
    print(f"  Duration: {format_time(len(audio))}  |  "
          f"Sample rate: {audio.frame_rate}Hz  |  "
          f"Channels: {audio.channels}  |  "
          f"Loaded in {load_time:.1f}s")

    # Process
    print(f"\nAnalyzing silence (threshold: {args.threshold} dBFS, "
          f"min silence length: {args.min_silence_len}ms)...")
    process_start = time.time()

    result, stats = trim_silence(
        audio,
        max_silence_ms=args.max_silence,
        threshold_dbfs=args.threshold,
        min_silence_len=args.min_silence_len,
        verbose=args.verbose,
    )

    process_time = time.time() - process_start

    # Report
    saved_ms = stats["original_duration_ms"] - stats["trimmed_duration_ms"]
    saved_pct = (saved_ms / stats["original_duration_ms"] * 100) if stats["original_duration_ms"] > 0 else 0

    print(f"\nResults:")
    print(f"  Original duration:     {format_time(stats['original_duration_ms'])}")
    print(f"  Trimmed duration:      {format_time(stats['trimmed_duration_ms'])}")
    print(f"  Time saved:            {format_time(saved_ms)} ({saved_pct:.1f}%)")
    print(f"  Silence segments:      {stats['silence_segments_found']} found, "
          f"{stats['silence_segments_trimmed']} trimmed")
    print(f"  Processing time:       {process_time:.1f}s")

    if args.dry_run:
        print(f"\n  [DRY RUN] No output file written.")
        return

    # Export
    print(f"\nExporting: {output_path} (format: {out_format})")
    export_params = {}
    if args.bitrate:
        export_params["bitrate"] = args.bitrate

    try:
        result.export(output_path, format=out_format, **export_params)
    except Exception as e:
        print(f"Error exporting audio: {e}", file=sys.stderr)
        sys.exit(1)

    output_size = os.path.getsize(output_path)
    print(f"  Output size: {output_size / (1024 * 1024):.1f} MB")
    print("Done!")


if __name__ == "__main__":
    main()
