# Ameskamp — Audio Match

Identify a recording by acoustic fingerprint. Generates a Chromaprint fingerprint from an audio file and looks it up against the [AcoustID](https://acoustid.org/) / [MusicBrainz](https://musicbrainz.org/) database, returning the best-matching recordings with title, artist, and release info. Ships as a CLI and a web app.

## Requirements

- Python 3.10+
- An AcoustID application key — free, register at <https://acoustid.org/new-application>
- A Chromaprint backend, either:
  - `fpcalc` from `libchromaprint-tools` (recommended; the Docker image installs it), or
  - the `chromaprint` Python package + `audioread` (already installed alongside `librosa`)

Pass the API key via `--api-key` or the `ACOUSTID_API_KEY` environment variable.

## CLI

```bash
pip install -r requirements.txt
export ACOUSTID_API_KEY=your_key_here

python audio_match.py song.mp3
python audio_match.py song.flac --json
python audio_match.py clip.wav --limit 10
```

Exit codes: `1` file not found, `2` missing API key, `3` no fingerprint backend, `4` fingerprinting failed, `5` AcoustID API error.

## Web app

```bash
export ACOUSTID_API_KEY=your_key_here
docker compose up --build
```

The app runs at <http://localhost:5002>. Drop an audio file and the server fingerprints it, queries AcoustID, and renders ranked matches with MusicBrainz links.

Or run directly for development:

```bash
pip install -r requirements.txt
ACOUSTID_API_KEY=your_key_here python web/audio_match_web.py
```

## How it works

1. The input audio is decoded and fed to [Chromaprint](https://acoustid.org/chromaprint), which condenses it into a compact perceptual fingerprint.
2. The fingerprint and duration are sent to the AcoustID lookup API.
3. AcoustID returns candidate MusicBrainz recording IDs ranked by confidence score, enriched with title, artist, and release metadata.

Best matches reflect the same recording (covers, edits, samples often score lower or don't match — the fingerprint targets the actual audio waveform, not the composition).

## Tech Stack

- **Backend:** Flask + Gunicorn, [pyacoustid](https://pypi.org/project/pyacoustid/) for fingerprinting and AcoustID API calls
- **Fingerprinting:** Chromaprint via `fpcalc`
- **Frontend:** Single-page vanilla HTML/CSS/JS
- **Deployment:** Docker

## License

MIT
