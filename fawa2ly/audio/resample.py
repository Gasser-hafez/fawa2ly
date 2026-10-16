"""
PCM conversion for the STT stream: any int16 layout -> 16 kHz mono int16.

Devices report all sorts of formats (48 kHz stereo is typical, surround devices
report 6-8 channels of which most are silent). Deepgram gets linear16 at 16 kHz
mono, so every captured chunk goes through to_16k_mono().
"""

from __future__ import annotations

import numpy as np

try:
    from scipy.signal import resample_poly
except ImportError:  # crude fallback, see _linear_resample
    resample_poly = None

TARGET_SR = 16000


def _linear_resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    n = int(len(audio) * dst_sr / src_sr)
    if n == 0 or len(audio) == 0:
        return np.zeros(0, dtype=audio.dtype)
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)


def to_16k_mono(raw: bytes, channels: int, src_sr: int, target_sr: int = TARGET_SR) -> bytes:
    """int16 PCM bytes with `channels` interleaved channels at `src_sr` -> int16 mono at target_sr."""
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if audio.size == 0:
        return b""
    if channels > 1:  # keep only front L/R: surround devices report 6-8ch, most silent
        audio = audio.reshape(-1, channels)[:, :2].mean(axis=1)
    if src_sr != target_sr:
        if resample_poly is not None:
            g = np.gcd(src_sr, target_sr)
            audio = resample_poly(audio, target_sr // g, src_sr // g)
        else:
            audio = _linear_resample(audio, src_sr, target_sr)
    return np.clip(audio, -32768, 32767).astype(np.int16).tobytes()
