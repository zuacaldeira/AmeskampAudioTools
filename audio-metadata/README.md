# Ameskamp — Audio Metadata

Inspect tags, stream info, and embedded cover art from audio files. Ships as a CLI and a web app.

## Features

- Tag extraction across MP3 (ID3), FLAC, WAV, OGG Vorbis, Opus, M4A/MP4, AIFF, WMA, APE, Musepack
- Stream info: duration, bitrate, sample rate, channels, bit depth
- Embedded cover art — preview in the browser or extract to a file via CLI
- Proprietary/extended frames surfaced: ID3 `TXXX`, `PRIV`, `GEOB`, `UFID`, `WXXX`; MP4 freeform atoms
- Broadcast WAV support via [`wavinfo`](https://pypi.org/project/wavinfo/): BWF `bext`, iXML, RIFF `INFO` lists, cues
- Optional `ffprobe` fallback that surfaces any additional container-level tags when the binary is available
- JSON output for scripting

## CLI

```bash
pip install -r requirements.txt
python audio_metadata.py song.mp3
python audio_metadata.py song.flac --json
python audio_metadata.py album.mp3 --cover cover.jpg
python audio_metadata.py track.m4a --raw
```

## Web app

```bash
docker compose up --build
```

The app runs at `http://localhost:5001`.

Or run directly for development:

```bash
pip install -r requirements.txt
python web/audio_metadata_web.py
```

## Tech Stack

- **Backend:** Flask + Gunicorn, [mutagen](https://mutagen.readthedocs.io/) for metadata parsing
- **Frontend:** Single-page vanilla HTML/CSS/JS
- **Deployment:** Docker

## License

MIT
