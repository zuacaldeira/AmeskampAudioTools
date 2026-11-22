FROM python:3.12-slim

# wget is required: panns_inference shells out to wget on first import to
# download the AudioSet labels CSV.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg wget && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --create-home appuser

WORKDIR /app

# Install CPU-only torch first (much smaller than CUDA build).
COPY requirements.txt .
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        torch==2.5.* && \
    pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser audio_events.py .
COPY --chown=appuser:appuser web/ web/

USER appuser

# Pre-fetch the labels CSV and CNN14 tagging checkpoint (~330MB) so the
# first request doesn't pay a multi-minute download. SED checkpoint is
# fetched lazily on first timeline=on request.
RUN python -c "from panns_inference import AudioTagging; AudioTagging(device='cpu')" \
 && ls -lh /home/appuser/panns_data

EXPOSE 5004

# SED inference can be slow on CPU for long clips; bump timeout.
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5004", \
     "--workers", "1", \
     "--threads", "4", \
     "--timeout", "300", \
     "--max-requests", "50", \
     "--max-requests-jitter", "10", \
     "--access-logfile", "-", \
     "web.audio_events_web:app"]
