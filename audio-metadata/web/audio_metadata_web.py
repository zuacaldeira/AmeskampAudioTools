#!/usr/bin/env python3
"""
audio_metadata_web.py - Web service for extracting audio metadata.

Run with: python audio_metadata_web.py [--port PORT]
Then open http://localhost:5001 in your browser.

Requirements: pip install flask mutagen
"""

import argparse
import base64
import glob
import io
import os
import sys
import threading
import time
import uuid
import tempfile

from flask import Flask, request, jsonify, send_file, send_from_directory

# Make the sibling CLI module importable regardless of how we're launched.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_metadata import analyze_audio, build_metadata  # noqa: E402

app = Flask(__name__)

UPLOAD_DIR = tempfile.mkdtemp(prefix="audio_metadata_")
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB
CLEANUP_MAX_AGE = 3600  # 1 hour

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


@app.route("/api/metadata", methods=["POST"])
def metadata():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    job_id = str(uuid.uuid4())[:8]
    ext = os.path.splitext(file.filename)[1]
    input_path = os.path.join(UPLOAD_DIR, f"{job_id}_input{ext}")

    try:
        file.save(input_path)
        meta = build_metadata(input_path)
        meta["analysis"] = analyze_audio(input_path)
    except ValueError as e:
        if os.path.exists(input_path):
            os.remove(input_path)
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        if os.path.exists(input_path):
            os.remove(input_path)
        return jsonify({"error": str(e)}), 500

    cover_bytes = meta.pop("_cover_bytes", None)
    cover_mime = meta.pop("_cover_mime", None)
    meta.pop("_audio", None)

    # Use the uploaded filename rather than the temp path for display.
    meta["file"]["name"] = file.filename
    meta["file"].pop("path", None)

    if cover_bytes:
        meta["cover"]["data_url"] = (
            f"data:{cover_mime};base64,{base64.b64encode(cover_bytes).decode('ascii')}"
        )

    # Clean up input file now that we've read everything we need from it.
    try:
        os.remove(input_path)
    except OSError:
        pass

    return jsonify(meta)


def main():
    parser = argparse.ArgumentParser(description="Audio Metadata Web Service")
    parser.add_argument("-p", "--port", type=int, default=5001, help="Port (default: 5001)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    args = parser.parse_args()

    print(f"\n  Audio Metadata Web Service")
    print(f"  Open http://localhost:{args.port} in your browser\n")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
