#!/usr/bin/env python3
"""
audio_match_web.py - Web service for identifying audio via AcoustID.

Run with: python audio_match_web.py [--port PORT]
Then open http://localhost:5002 in your browser.

Requires the ACOUSTID_API_KEY environment variable to be set
(register at https://acoustid.org/new-application).
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_match import lookup  # noqa: E402

app = Flask(__name__)

UPLOAD_DIR = tempfile.mkdtemp(prefix="audio_match_")
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB
CLEANUP_MAX_AGE = 3600  # 1 hour
DEFAULT_LIMIT = 5

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


@app.route("/api/match", methods=["POST"])
def match():
    api_key = os.environ.get("ACOUSTID_API_KEY")
    if not api_key:
        return jsonify({
            "error": "Server is missing ACOUSTID_API_KEY. Register a free key at "
                     "https://acoustid.org/new-application and set the env var."
        }), 503

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    try:
        limit = int(request.form.get("limit", DEFAULT_LIMIT))
    except ValueError:
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, 25))

    job_id = str(uuid.uuid4())[:8]
    ext = os.path.splitext(file.filename)[1]
    input_path = os.path.join(UPLOAD_DIR, f"{job_id}_input{ext}")

    try:
        file.save(input_path)
        result = lookup(api_key, input_path, limit=limit)
    except acoustid.NoBackendError:
        return jsonify({
            "error": "Server has no fingerprinting backend (fpcalc / libchromaprint)."
        }), 500
    except acoustid.FingerprintGenerationError as e:
        return jsonify({"error": f"Could not fingerprint file: {e}"}), 400
    except acoustid.WebServiceError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(input_path):
            try:
                os.remove(input_path)
            except OSError:
                pass

    # Replace temp path with original filename for display.
    result["file"]["name"] = file.filename
    result["file"].pop("path", None)

    return jsonify(result)


def main():
    parser = argparse.ArgumentParser(description="Audio Match Web Service")
    parser.add_argument("-p", "--port", type=int, default=5002, help="Port (default: 5002)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    args = parser.parse_args()

    if not os.environ.get("ACOUSTID_API_KEY"):
        print(
            "  WARNING: ACOUSTID_API_KEY is not set. Requests will fail until you set it.\n"
            "  Get a free key at https://acoustid.org/new-application\n",
            file=sys.stderr,
        )

    print(f"\n  Audio Match Web Service")
    print(f"  Open http://localhost:{args.port} in your browser\n")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
