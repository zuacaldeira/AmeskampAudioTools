#!/usr/bin/env python3
"""
audio_discover_web.py - Web service wrapping audio_discover.discover().

Run with: python audio_discover_web.py [--port PORT]
Then open http://localhost:5005 in your browser.

Requires the ACOUSTID_API_KEY environment variable.
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

import acoustid
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_discover import discover, DEFAULT_ALGORITHM  # noqa: E402

app = Flask(__name__)

UPLOAD_DIR = tempfile.mkdtemp(prefix="audio_discover_")
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB
CLEANUP_MAX_AGE = 3600  # 1 hour
DEFAULT_TOP = 12

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


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/discover", methods=["POST"])
def discover_route():
    api_key = os.environ.get("ACOUSTID_API_KEY")
    if not api_key:
        return jsonify({
            "error": "Server is missing ACOUSTID_API_KEY. Register a free key at "
                     "https://acoustid.org/new-application and set the env var."
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
        result = discover(api_key, input_path, top=top, algorithm=DEFAULT_ALGORITHM)
    except acoustid.NoBackendError:
        return jsonify({"error": "Server has no Chromaprint backend."}), 500
    except acoustid.FingerprintGenerationError as e:
        return jsonify({"error": f"Could not fingerprint file: {e}"}), 400
    except acoustid.WebServiceError as e:
        return jsonify({"error": str(e)}), 502
    except requests.RequestException as e:
        return jsonify({"error": f"ListenBrainz request failed: {e}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(input_path):
            try:
                os.remove(input_path)
            except OSError:
                pass

    # Replace temp path with the user-facing filename for display.
    result["file"]["name"] = f.filename
    result["file"].pop("path", None)
    return jsonify(result)


def main():
    parser = argparse.ArgumentParser(description="Audio Discover Web Service")
    parser.add_argument("-p", "--port", type=int, default=5005, help="Port (default: 5005)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    args = parser.parse_args()

    if not os.environ.get("ACOUSTID_API_KEY"):
        print("  WARNING: ACOUSTID_API_KEY is not set. Requests will 503.",
              file=sys.stderr)
    print(f"\n  Audio Discover Web Service")
    print(f"  Open http://localhost:{args.port} in your browser\n")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
