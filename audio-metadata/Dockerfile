FROM python:3.12-slim

RUN useradd --create-home appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser audio_metadata.py .
COPY --chown=appuser:appuser web/ web/

USER appuser

EXPOSE 5001

CMD ["gunicorn", \
     "--bind", "0.0.0.0:5001", \
     "--workers", "2", \
     "--timeout", "120", \
     "--max-requests", "200", \
     "--max-requests-jitter", "20", \
     "--access-logfile", "-", \
     "web.audio_metadata_web:app"]
