FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --create-home appuser

WORKDIR /app

# Install CPU-only torch first to keep the image small.
COPY requirements.txt .
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        torch==2.5.* && \
    pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser embed.py .
COPY --chown=appuser:appuser audio_similar.py .
COPY --chown=appuser:appuser web/ web/

USER appuser

# Pre-fetch the CLAP weights (~600 MB) so the first user query doesn't pay
# the download. Cache lands under /home/appuser/.cache/huggingface.
RUN python -c "from embed import get_model; get_model()"

# Catalog is mounted at /catalog (read by both index/query and web). Compose
# wires it to a host directory so the indexer CLI and web service share state.
ENV AUDIO_SIMILAR_CATALOG=/catalog
VOLUME ["/catalog"]

EXPOSE 5003

CMD ["gunicorn", \
     "--bind", "0.0.0.0:5003", \
     "--workers", "1", \
     "--threads", "4", \
     "--timeout", "300", \
     "--max-requests", "50", \
     "--max-requests-jitter", "10", \
     "--access-logfile", "-", \
     "web.audio_similar_web:app"]
