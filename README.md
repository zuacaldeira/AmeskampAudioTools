# Silence Trimmer

A web app for trimming long silences from audio files. Upload a file, configure thresholds, and download the trimmed result.

**Live at [ameskamp.zuacaldeira.com](https://ameskamp.zuacaldeira.com)**

## Features

- Drag-and-drop audio upload (WAV, MP3, FLAC, OGG, AAC, up to 500 MB)
- Configurable silence threshold and max silence duration
- Detailed segment breakdown showing which silences were trimmed
- Download trimmed audio in the original or a different format

## Tech Stack

- **Backend:** Flask + Gunicorn, pydub + ffmpeg for audio processing
- **Frontend:** Single-page vanilla HTML/CSS/JS
- **Deployment:** Docker, host nginx with certbot SSL

## Local Development

```bash
docker compose up --build
```

The app runs at `http://localhost:5000`.

## Deploy to Production

```bash
./deploy.sh
```

Deploys to the VPS, builds the container, configures nginx and SSL automatically.

## License

MIT
