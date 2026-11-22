"""
embed.py - Audio embedding extraction via LAION-CLAP.

Loads a pretrained CLAP model once and exposes ``embed(path)`` returning a
single L2-normalized vector summarizing the audio's perceptual content.

Long files are chunked into ~10-second segments at 48 kHz mono; per-segment
embeddings are averaged and re-normalized so similarity across files of
different lengths is comparable via plain cosine.
"""

import os
import threading

import numpy as np


DEFAULT_MODEL = os.environ.get("CLAP_MODEL", "laion/larger_clap_music")
SAMPLE_RATE = 48_000
SEGMENT_SECONDS = 10
MAX_SEGMENTS = 30  # cap at ~5 min of audio so very long files stay bounded

_lock = threading.Lock()
_state = {"model": None, "processor": None, "model_name": None}


def get_model(model_name=DEFAULT_MODEL):
    """Return (model, processor), loading them on first call. Thread-safe."""
    with _lock:
        if _state["model_name"] != model_name:
            from transformers import ClapModel, ClapProcessor
            _state["model"] = ClapModel.from_pretrained(model_name).eval()
            _state["processor"] = ClapProcessor.from_pretrained(model_name)
            _state["model_name"] = model_name
        return _state["model"], _state["processor"]


def _load_audio(path):
    """Load audio as a mono float32 numpy array at SAMPLE_RATE."""
    import librosa
    audio, _sr = librosa.load(path, sr=SAMPLE_RATE, mono=True)
    return audio.astype(np.float32)


def _segment(audio):
    """Split audio into SEGMENT_SECONDS chunks. Pads the last short segment."""
    seg_len = SEGMENT_SECONDS * SAMPLE_RATE
    if audio.size == 0:
        return []
    if audio.size <= seg_len:
        # Pad so the model always gets a full-length input.
        pad = np.zeros(seg_len, dtype=np.float32)
        pad[: audio.size] = audio
        return [pad]
    n_full = audio.size // seg_len
    segs = [audio[i * seg_len : (i + 1) * seg_len] for i in range(n_full)]
    # Take at most MAX_SEGMENTS evenly spaced segments to bound long files.
    if len(segs) > MAX_SEGMENTS:
        idxs = np.linspace(0, len(segs) - 1, MAX_SEGMENTS).astype(int)
        segs = [segs[i] for i in idxs]
    return segs


def embed(path, model_name=DEFAULT_MODEL):
    """Return a single L2-normalized embedding vector for ``path``.

    Raises FileNotFoundError if the file doesn't exist; bubbles up librosa
    errors for unreadable audio.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")

    import torch

    audio = _load_audio(path)
    segments = _segment(audio)
    if not segments:
        raise ValueError(f"Audio file is empty: {path}")

    model, processor = get_model(model_name)
    inputs = processor(audio=segments, sampling_rate=SAMPLE_RATE, return_tensors="pt")

    with torch.no_grad():
        outputs = model.get_audio_features(**inputs)
    # transformers >=5 returns BaseModelOutputWithPooling; the L2-normalized
    # audio embedding (per segment, shape (n_segments, dim)) is in pooler_output.
    features = outputs.pooler_output if hasattr(outputs, "pooler_output") else outputs

    vec = features.mean(dim=0).cpu().numpy().astype(np.float32)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec


def cosine_sim(a, b):
    """Cosine similarity between two L2-normalized (or arbitrary) vectors."""
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)
