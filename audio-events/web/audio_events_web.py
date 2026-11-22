#!/usr/bin/env python3
"""
audio_events_web.py - Web service wrapping the audio-events CLI.

Uploads an audio clip and runs PANNs CNN14 AudioSet tagging on it, returning
the top events with confidences, optional substring-detection queries, and
an optional per-second timeline (frame-level SED).

Run with: python audio_events_web.py [--port PORT]
Then open http://localhost:5004 in your browser.
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

from audio_events import analyze, DEFAULT_THRESHOLD  # noqa: E402

app = Flask(__name__)

UPLOAD_DIR = tempfile.mkdtemp(prefix="audio_events_")
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB
CLEANUP_MAX_AGE = 3600  # 1 hour
DEFAULT_TOP = 12

app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE


def _cleanup_old_files():
    while True:
        time.sleep(300)
        try:
            now = time.time()
            for path in glob.glob(os.path.join(UPLOAD_DIR, "*")):
                if now - os.path.getmtime(path) > CLEANUP_MAX_AGE:
                    os.remove(path)
        except Exception:
            pass


threading.Thread(target=_cleanup_old_files, daemon=True).start()


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/events", methods=["POST"])
def events():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    try:
        top = int(request.form.get("top", DEFAULT_TOP))
    except ValueError:
        top = DEFAULT_TOP
    top = max(1, min(top, 50))

    try:
        threshold = float(request.form.get("threshold", DEFAULT_THRESHOLD))
    except ValueError:
        threshold = DEFAULT_THRESHOLD

    detect_raw = (request.form.get("detect") or "").strip()
    detect_list = [t.strip() for t in detect_raw.split(",") if t.strip()] or None

    timeline = request.form.get("timeline", "").lower() in ("1", "true", "yes", "on")

    job_id = str(uuid.uuid4())[:8]
    ext = os.path.splitext(file.filename)[1]
    input_path = os.path.join(UPLOAD_DIR, f"{job_id}_input{ext}")

    try:
        file.save(input_path)
        result = analyze(
            input_path,
            top=top,
            detect_terms_list=detect_list,
            with_timeline=timeline,
            threshold=threshold,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(input_path):
            try:
                os.remove(input_path)
            except OSError:
                pass

    # Use the uploaded filename for display.
    result["file"] = file.filename
    return jsonify(result)


def main():
    parser = argparse.ArgumentParser(description="Audio Events Web Service")
    parser.add_argument("-p", "--port", type=int, default=5004, help="Port (default: 5004)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    args = parser.parse_args()

    print(f"\n  Audio Events Web Service")
    print(f"  Open http://localhost:{args.port} in your browser\n")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
