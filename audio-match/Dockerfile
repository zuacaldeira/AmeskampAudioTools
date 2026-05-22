FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libchromaprint-tools \
        ffmpeg && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --create-home appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser audio_match.py .
COPY --chown=appuser:appuser web/ web/

USER appuser

EXPOSE 5002

CMD ["gunicorn", \
     "--bind", "0.0.0.0:5002", \
     "--workers", "2", \
     "--timeout", "120", \
     "--max-requests", "200", \
     "--max-requests-jitter", "20", \
     "--access-logfile", "-", \
     "web.audio_match_web:app"]
