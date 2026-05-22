# Ameskamp — Audio Similar

Find sounds-alike tracks in your own music catalog via neural audio embeddings.

Each track in the catalog is embedded once with [LAION CLAP](https://huggingface.co/laion/larger_clap_music) (a music-aware audio-text contrastive model). Querying for "what sounds like this?" is then a single cosine similarity over the catalog's vectors — very fast, even on a personal-sized library.

Unlike [audio-match](../audio-match/), which uses acoustic fingerprinting to find the *same recording* (covers, samples, exact matches), audio-similar finds tracks that *sound similar in genre, mood, timbre, and texture*.

## Catalog model

The catalog is a single directory with two files:

- `index.db` — SQLite with one row per track (path, mtime, tags, vector offset).
- `vectors.npy` — a contiguous `float32` array of shape `(N, 512)`, one row per indexed track in vector-offset order.

Both are portable — you can build a catalog on your laptop and ship it to the VPS by copying the directory.

## CLI

```bash
pip install -r requirements.txt

# Walk a directory and embed every audio file (resumable — re-running skips
# already-indexed files based on path+mtime).
python audio_similar.py index ~/Music --catalog ./catalog

# Find the top-N most similar tracks to a query song.
python audio_similar.py query song.mp3 --catalog ./catalog --top 10

# JSON output and self-exclusion are both available.
python audio_similar.py query song.mp3 --catalog ./catalog --top 5 --exclude-self --json
```

Exit codes: `0` success, `1` missing input/catalog, `2` some files failed to embed.

## Web app

```bash
docker compose up --build
```

The app runs at <http://localhost:5003>. It's read-only — the catalog must be populated via the CLI first. The catalog status panel at the top of the page shows whether the catalog is loaded; the submit button is disabled until it is.

To populate the catalog inside the container's catalog volume:

```bash
# Run a one-off index against a host directory, writing into the catalog volume.
docker compose run --rm \
    -v /path/to/your/music:/music:ro \
    audio-similar \
    python /app/audio_similar.py index /music --catalog /catalog
```

Or — and this is usually easier — build the catalog locally on a machine with the music files, then copy the resulting directory onto the VPS.

## How it works

1. **Embedding** — each audio file is loaded at 48 kHz mono, split into 10-second segments (or zero-padded if shorter), and run through CLAP's audio encoder. Per-segment 512-dim embeddings are mean-pooled and L2-normalized. Long files are subsampled to a fixed max segment count so cost stays bounded.
2. **Storage** — embeddings land at a deterministic offset in `vectors.npy`; SQLite's `vector_offset` column maps each track to its row. The two stay in sync as the indexer appends.
3. **Query** — the query file is embedded the same way. Cosine similarity is `vectors @ qvec` (a single matrix-vector dot since everything is L2-normalized). The top-N rows are returned with their SQLite metadata.

For catalogs under ~10,000 tracks the brute-force approach is fast enough and adds no dependencies; beyond that you'd want to swap in FAISS or HNSW. Not in v1.

## Tech Stack

- **Embedding model:** [LAION CLAP](https://huggingface.co/laion/larger_clap_music) (`laion/larger_clap_music`, 512-dim audio embeddings)
- **Backend:** Flask + Gunicorn
- **Storage:** SQLite + raw float32 memmap
- **Frontend:** Single-page vanilla HTML/CSS/JS

## License

MIT
