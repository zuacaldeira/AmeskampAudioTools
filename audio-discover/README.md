# Ameskamp — Audio Discover

Find related tracks by chaining acoustic identification with collaborative-filtering similarity. Ships as a CLI and a web app.

Different similarity flavor than [audio-similar](../audio-similar/):

- **audio-similar** → "what sounds like this?" (perceptual CLAP embeddings against your own catalog)
- **audio-discover** → "what do other listeners hear alongside this?" (behavior-based, tapping the public ListenBrainz dataset)

## The chain

```
audio file
  -> Chromaprint fingerprint
  -> AcoustID lookup           (needs an AcoustID API key)
  -> MusicBrainz recording ID
  -> ListenBrainz similar-recordings
  -> ranked related tracks
```

Only works for recordings that (a) are catalogued in AcoustID/MusicBrainz and (b) have enough listener data on ListenBrainz to produce similars. Niche/rare tracks identify fine but often return an empty related-list — that's a real "no recommendations available" outcome, not a failure.

## Requirements

- A free **AcoustID API key** — register at <https://acoustid.org/new-application>. Pass via `--api-key` or `ACOUSTID_API_KEY`.
- ListenBrainz is queried anonymously — no key required.
- Chromaprint backend (the Docker image installs `libchromaprint-tools`; locally, install it or rely on the `chromaprint` Python package + `audioread`).

## CLI

```bash
pip install -r requirements.txt
export ACOUSTID_API_KEY=your_key_here

python audio_discover.py song.mp3
python audio_discover.py song.flac --json --top 25
```

Exit codes: `1` missing file, `2` missing API key, `3` no Chromaprint backend, `4` fingerprint failed, `5` AcoustID API error, `6` ListenBrainz request failed.

## Web app

```bash
export ACOUSTID_API_KEY=your_key_here
docker compose up --build
```

The app runs at <http://localhost:5005>.

Or for development:

```bash
pip install -r requirements.txt
ACOUSTID_API_KEY=your_key_here python web/audio_discover_web.py
```

## How "similar" is computed

ListenBrainz's `similar-recordings` endpoint returns recordings that get listened to in the same listening sessions as the query — collaborative filtering over real listening data. The default `session_based_days_7500_session_300_contribution_5_threshold_15_limit_50_skip_30` algorithm uses ~7,500 days of data with a 50-result cap. The integer score is relative within the result set; higher = more co-listened.

## Tech Stack

- **Backend:** Flask + Gunicorn, [pyacoustid](https://pypi.org/project/pyacoustid/) for fingerprinting + AcoustID, `requests` to ListenBrainz labs
- **Fingerprinting:** Chromaprint via `fpcalc`
- **Frontend:** Single-page vanilla HTML/CSS/JS
- **Deployment:** Docker

## License

MIT
