import numpy as np
import pytest

from fawa2ly.audio import TARGET_SR, to_16k_mono
from fawa2ly.audio.resample import _linear_resample


def pcm(samples: np.ndarray) -> bytes:
    return np.asarray(samples, dtype=np.int16).tobytes()


def sine(sr: int, seconds: float, hz: float, amp: float = 10000.0) -> np.ndarray:
    t = np.arange(int(sr * seconds)) / sr
    return amp * np.sin(2 * np.pi * hz * t)


def test_mono_16k_passes_through_unchanged():
    x = np.array([0, 1000, -1000, 32767, -32768], dtype=np.int16)
    assert to_16k_mono(pcm(x), channels=1, src_sr=16000) == pcm(x)


def test_stereo_is_averaged_to_mono():
    left = np.array([1000, 2000, 3000], dtype=np.int16)
    right = np.array([3000, 2000, 1000], dtype=np.int16)
    interleaved = np.column_stack([left, right]).reshape(-1)
    out = np.frombuffer(to_16k_mono(pcm(interleaved), channels=2, src_sr=16000), dtype=np.int16)
    assert list(out) == [2000, 2000, 2000]


def test_surround_keeps_only_front_left_right():
    frames = np.array([[1000, 3000, 9000, 9000, 9000, 9000],
                       [2000, 2000, 9000, 9000, 9000, 9000]], dtype=np.int16)
    out = np.frombuffer(to_16k_mono(pcm(frames.reshape(-1)), channels=6, src_sr=16000), dtype=np.int16)
    assert list(out) == [2000, 2000]


def test_48k_to_16k_keeps_duration_and_tone():
    x = sine(48000, 0.1, 440.0)
    out = np.frombuffer(to_16k_mono(pcm(x), channels=1, src_sr=48000), dtype=np.int16)
    assert len(out) == len(x) // 3
    # a 440 Hz tone must still peak at 440 Hz after resampling
    spectrum = np.abs(np.fft.rfft(out.astype(np.float64)))
    peak_hz = np.fft.rfftfreq(len(out), 1 / TARGET_SR)[np.argmax(spectrum[1:]) + 1]
    assert peak_hz == pytest.approx(440.0, abs=TARGET_SR / len(out))


def test_44100_to_16k_length():
    x = sine(44100, 0.05, 300.0)
    out = np.frombuffer(to_16k_mono(pcm(x), channels=1, src_sr=44100), dtype=np.int16)
    assert abs(len(out) - int(len(x) * 16000 / 44100)) <= 1


def test_output_is_clipped_to_int16():
    x = np.full(160, 32767, dtype=np.int16)
    out = np.frombuffer(to_16k_mono(pcm(x), channels=1, src_sr=48000), dtype=np.int16)
    assert out.max() <= 32767 and out.min() >= -32768


def test_linear_fallback_resamples_and_handles_empty():
    x = np.arange(0, 48, dtype=np.float32)
    y = _linear_resample(x, 48000, 16000)
    assert len(y) == 16 and y[0] == 0 and y[-1] == pytest.approx(47.0)
    assert len(_linear_resample(np.zeros(0, dtype=np.float32), 48000, 16000)) == 0
    assert to_16k_mono(b"", channels=2, src_sr=48000) == b""
