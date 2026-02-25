#!/usr/bin/env python3
"""
silence_trimmer_web.py - Web service for trimming silence in audio files.

Run with: python silence_trimmer_web.py [--port PORT]
Then open http://localhost:5000 in your browser.

Requirements: pip install flask pydub
System: ffmpeg must be installed
"""

import argparse
import glob
import os
import sys
import threading
import uuid
import time
import tempfile
import shutil
from pathlib import Path

from flask import Flask, request, jsonify, send_file, send_from_directory
from pydub import AudioSegment
from pydub.silence import detect_nonsilent

app = Flask(__name__)

# Temp directory for processing
UPLOAD_DIR = tempfile.mkdtemp(prefix="silence_trimmer_")
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB
CLEANUP_MAX_AGE = 3600  # 1 hour in seconds

app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE


def _cleanup_old_files():
    """Background thread that deletes output files older than CLEANUP_MAX_AGE."""
    while True:
        time.sleep(300)  # check every 5 minutes
        try:
            now = time.time()
            for path in glob.glob(os.path.join(UPLOAD_DIR, "*_output.*")):
                if now - os.path.getmtime(path) > CLEANUP_MAX_AGE:
                    os.remove(path)
        except Exception:
            pass


_cleanup_thread = threading.Thread(target=_cleanup_old_files, daemon=True)
_cleanup_thread.start()


def format_time(ms):
    total_seconds = ms / 1000.0
    minutes = int(total_seconds // 60)
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:06.3f}"


def trim_silence(audio, max_silence_ms, threshold_dbfs, min_silence_len):
    nonsilent_ranges = detect_nonsilent(
        audio,
        min_silence_len=min_silence_len,
        silence_thresh=threshold_dbfs,
    )

    segments_info = []

    if not nonsilent_ranges:
        trimmed = audio[:max_silence_ms] if len(audio) > max_silence_ms else audio
        return trimmed, {
            "original_duration_ms": len(audio),
            "trimmed_duration_ms": len(trimmed),
            "silence_segments_found": 1,
            "silence_segments_trimmed": 1 if len(audio) > max_silence_ms else 0,
            "segments": [{"start": 0, "end": len(audio), "duration": len(audio), "trimmed": len(audio) > max_silence_ms}],
        }

    result = AudioSegment.empty()
    silence_segments_found = 0
    silence_segments_trimmed = 0

    # Leading silence
    if nonsilent_ranges[0][0] > 0:
        leading_ms = nonsilent_ranges[0][0]
        silence_segments_found += 1
        trimmed = leading_ms > max_silence_ms
        if trimmed:
            silence_segments_trimmed += 1
            result += audio[:max_silence_ms]
        else:
            result += audio[:leading_ms]
        segments_info.append({"start": 0, "end": leading_ms, "duration": leading_ms, "trimmed": trimmed, "type": "leading"})

    for i, (start, end) in enumerate(nonsilent_ranges):
        result += audio[start:end]

        if i < len(nonsilent_ranges) - 1:
            next_start = nonsilent_ranges[i + 1][0]
            gap_ms = next_start - end
            silence_segments_found += 1
            trimmed = gap_ms > max_silence_ms

            if trimmed:
                silence_segments_trimmed += 1
                result += audio[end:end + max_silence_ms]
            else:
                result += audio[end:next_start]

            segments_info.append({"start": end, "end": next_start, "duration": gap_ms, "trimmed": trimmed, "type": "gap"})

    # Trailing silence
    last_end = nonsilent_ranges[-1][1]
    if last_end < len(audio):
        trailing_ms = len(audio) - last_end
        silence_segments_found += 1
        trimmed = trailing_ms > max_silence_ms
        if trimmed:
            silence_segments_trimmed += 1
            result += audio[last_end:last_end + max_silence_ms]
        else:
            result += audio[last_end:]
        segments_info.append({"start": last_end, "end": len(audio), "duration": trailing_ms, "trimmed": trimmed, "type": "trailing"})

    return result, {
        "original_duration_ms": len(audio),
        "trimmed_duration_ms": len(result),
        "silence_segments_found": silence_segments_found,
        "silence_segments_trimmed": silence_segments_trimmed,
        "segments": segments_info,
    }


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/process", methods=["POST"])
def process_audio():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    # Parse options
    threshold = float(request.form.get("threshold", -40))
    max_silence = int(request.form.get("max_silence", 1000))
    min_silence_len = int(request.form.get("min_silence_len", 100))
    output_format = request.form.get("format", "").strip()

    # Determine formats
    input_ext = Path(file.filename).suffix.lstrip(".")
    if not output_format:
        output_format = input_ext or "wav"

    # Save uploaded file
    job_id = str(uuid.uuid4())[:8]
    input_path = os.path.join(UPLOAD_DIR, f"{job_id}_input.{input_ext}")
    output_filename = f"{Path(file.filename).stem}_trimmed.{output_format}"
    output_path = os.path.join(UPLOAD_DIR, f"{job_id}_output.{output_format}")

    try:
        file.save(input_path)

        # Load
        audio = AudioSegment.from_file(input_path)
        info = {
            "sample_rate": audio.frame_rate,
            "channels": audio.channels,
            "original_filename": file.filename,
        }

        # Process
        result, stats = trim_silence(audio, max_silence, threshold, min_silence_len)
        stats.update(info)

        # Export
        bitrate = request.form.get("bitrate", "").strip() or None
        export_kwargs = {}
        if bitrate:
            export_kwargs["bitrate"] = bitrate

        result.export(output_path, format=output_format, **export_kwargs)
        stats["output_size_bytes"] = os.path.getsize(output_path)
        stats["job_id"] = job_id
        stats["output_filename"] = output_filename
        stats["output_format"] = output_format

        # Cleanup input
        os.remove(input_path)

        return jsonify(stats)

    except Exception as e:
        # Cleanup on error
        for p in [input_path, output_path]:
            if os.path.exists(p):
                os.remove(p)
        return jsonify({"error": str(e)}), 500


@app.route("/api/download/<job_id>/<filename>")
def download(job_id, filename):
    output_format = Path(filename).suffix.lstrip(".")
    output_path = os.path.join(UPLOAD_DIR, f"{job_id}_output.{output_format}")

    if not os.path.exists(output_path):
        return jsonify({"error": "File not found or expired"}), 404

    return send_file(output_path, as_attachment=True, download_name=filename)


def main():
    parser = argparse.ArgumentParser(description="Silence Trimmer Web Service")
    parser.add_argument("-p", "--port", type=int, default=5000, help="Port to run on (default: 5000)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--debug", action="store_true", help="Run in debug mode")
    args = parser.parse_args()

    print(f"\n  Silence Trimmer Web Service")
    print(f"  Open http://localhost:{args.port} in your browser\n")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
