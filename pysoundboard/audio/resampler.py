"""Audio resampler with python-soxr and numpy linear fallback."""

from __future__ import annotations

import logging
import numpy as np

logger = logging.getLogger(__name__)

# Check soxr availability
_SOXR_AVAILABLE = False
try:
    import soxr  # type: ignore
    _SOXR_AVAILABLE = True
except ImportError:
    logger.warning("soxr is not installed; falling back to numpy linear resampling.")


def resample_audio(
    data: np.ndarray,
    in_rate: int,
    target_rate: int = 48000,
    force_fallback: bool = False,
) -> np.ndarray:
    """
    Resample audio data to target sample rate and ensure float32 stereo format.

    Args:
        data: Audio samples, float32, mono or multi-channel.
        in_rate: Original sample rate in Hz.
        target_rate: Target sample rate in Hz (default 48000).
        force_fallback: If True, forces numpy linear interpolation (for testing).

    Returns:
        np.ndarray: Resampled float32 array with shape (frames, 2).
    """
    # Ensure 2D
    if data.ndim == 1:
        data = np.expand_dims(data, axis=1)

    # Normalize channel count to stereo
    channels = data.shape[1]
    if channels == 1:
        data = np.repeat(data, 2, axis=1)
    elif channels > 2:
        data = data[:, :2]

    # Ensure float32
    if data.dtype != np.float32:
        data = data.astype(np.float32)

    # If already at target rate, return immediately
    if in_rate == target_rate:
        return data

    # 1. Primary: High-fidelity sinc resampling via soxr
    if _SOXR_AVAILABLE and not force_fallback:
        try:
            resampled = soxr.resample(data, in_rate, target_rate, quality="HQ")
            return resampled.astype(np.float32, copy=False)
        except Exception as e:
            logger.warning("soxr resampling failed (%s); using numpy fallback.", e)

    # 2. Fallback: Fast linear interpolation using numpy
    num_frames = len(data)
    target_frames = int(num_frames * target_rate / in_rate)
    x_old = np.linspace(0.0, 1.0, num_frames, endpoint=False, dtype=np.float32)
    x_new = np.linspace(0.0, 1.0, target_frames, endpoint=False, dtype=np.float32)

    left = np.interp(x_new, x_old, data[:, 0]).astype(np.float32)
    right = np.interp(x_new, x_old, data[:, 1]).astype(np.float32)

    return np.stack([left, right], axis=1)
