"""Unit tests for models, atomic config, ring buffer, and audio resampler."""

import os
import tempfile
import numpy as np
import pytest

from pysoundboard.core.models import AppConfig, SoundItem, AudioDeviceInfo
from pysoundboard.core.config import ConfigManager
from pysoundboard.audio.ring_buffer import AudioRingBuffer
from pysoundboard.audio.resampler import resample_audio


# --- Models & Config Tests ---

def test_sound_item_legacy_mapping():
    """Verify SoundItem deserializes legacy 'file' key."""
    legacy_data = {"name": "airhorn", "file": "C:/sounds/airhorn.wav", "hotkey": "<ctrl>+1"}
    item = SoundItem.from_dict(legacy_data)
    assert item.name == "airhorn"
    assert item.file_path == "C:/sounds/airhorn.wav"
    assert item.hotkey == "<ctrl>+1"
    assert item.volume == 1.0


def test_app_config_legacy_roundtrip():
    """Verify AppConfig parses legacy soundboard config format."""
    legacy_dict = {
        "cable": "CABLE Input",
        "monitor": "Headphones",
        "mic": "Microphone",
        "volume": 0.8,
        "mic_volume": 1.2,
        "stop_key": "<ctrl>+<alt>+s",
        "sounds": [
            {"name": "bell", "file": "bell.wav", "hotkey": "<alt>+b"}
        ]
    }
    cfg = AppConfig.from_dict(legacy_dict)
    assert cfg.cable_device == "CABLE Input"
    assert cfg.monitor_device == "Headphones"
    assert cfg.mic_device == "Microphone"
    assert cfg.master_volume == 0.8
    assert cfg.mic_volume == 1.2
    assert cfg.stop_hotkey == "<ctrl>+<alt>+s"
    assert len(cfg.sounds) == 1
    assert cfg.sounds[0].name == "bell"

    # Export dictionary and check legacy keys are preserved
    exported = cfg.to_dict()
    assert exported["cable"] == "CABLE Input"
    assert exported["volume"] == 0.8
    assert exported["stop_key"] == "<ctrl>+<alt>+s"


def test_config_atomic_save_and_load(tmp_path):
    """Verify atomic config saving and loading."""
    cfg_file = str(tmp_path / "soundboard_test.json")
    mgr = ConfigManager(cfg_file)

    cfg = AppConfig(
        cable_device="Test Cable",
        master_volume=0.75,
        sounds=[SoundItem(name="sound1", file_path="sound1.wav", hotkey="<ctrl>+1")]
    )
    mgr.save(cfg)

    # Read back
    mgr2 = ConfigManager(cfg_file)
    loaded = mgr2.load()
    assert loaded.cable_device == "Test Cable"
    assert loaded.master_volume == 0.75
    assert len(loaded.sounds) == 1
    assert loaded.sounds[0].name == "sound1"


def test_config_corrupted_file_backup(tmp_path):
    """Verify corrupt JSON triggers a backup file and returns default config."""
    cfg_file = str(tmp_path / "bad_syntax_config.json")
    with open(cfg_file, "w", encoding="utf-8") as f:
        f.write("{ invalid json content ::: [")

    mgr = ConfigManager(cfg_file)
    loaded = mgr.load()
    assert loaded.schema_version == 1
    assert loaded.master_volume == 1.0

    # Ensure backup was created
    dir_files = os.listdir(tmp_path)
    backup_files = [f for f in dir_files if f.endswith(".bak")]
    assert len(backup_files) == 1


# --- Ring Buffer Tests ---

def test_ring_buffer_basic_rw():
    """Verify basic write and read operations."""
    rb = AudioRingBuffer(capacity_frames=128, channels=2)
    assert rb.capacity == 128
    assert rb.available_read == 0

    data = np.ones((64, 2), dtype=np.float32) * 0.5
    written = rb.write(data)
    assert written == 64
    assert rb.available_read == 64

    out = np.zeros((64, 2), dtype=np.float32)
    read = rb.read(out, 64)
    assert read == 64
    assert rb.available_read == 0
    assert np.allclose(out, 0.5)


def test_ring_buffer_wraparound():
    """Verify circular wraparound across the buffer capacity boundary."""
    rb = AudioRingBuffer(capacity_frames=64, channels=2)

    # Write 48 frames, read 48 frames -> pointers at 48
    d1 = np.full((48, 2), 1.0, dtype=np.float32)
    rb.write(d1)
    out1 = np.zeros((48, 2), dtype=np.float32)
    rb.read(out1, 48)

    # Now write 32 frames -> wraps around from 48 across 64 to 16
    d2 = np.full((32, 2), 2.0, dtype=np.float32)
    rb.write(d2)
    assert rb.available_read == 32

    out2 = np.zeros((32, 2), dtype=np.float32)
    read2 = rb.read(out2, 32)
    assert read2 == 32
    assert np.allclose(out2, 2.0)


def test_ring_buffer_underflow():
    """Verify underflow pads remaining buffer with zeros."""
    rb = AudioRingBuffer(capacity_frames=64, channels=2)
    data = np.full((20, 2), 3.0, dtype=np.float32)
    rb.write(data)

    out = np.full((40, 2), -1.0, dtype=np.float32)
    read = rb.read(out, 40)
    assert read == 20
    assert np.allclose(out[:20], 3.0)
    assert np.allclose(out[20:], 0.0)


def test_ring_buffer_overrun():
    """Verify overrun drops oldest frames to bound latency."""
    rb = AudioRingBuffer(capacity_frames=64, channels=2)
    # Fill with 40 frames of 1.0
    rb.write(np.full((40, 2), 1.0, dtype=np.float32))

    # Write 40 frames of 2.0 (total 80 > 64 capacity)
    rb.write(np.full((40, 2), 2.0, dtype=np.float32))

    # Available frames should be capped at capacity (64)
    assert rb.available_read == 64
    out = np.zeros((64, 2), dtype=np.float32)
    rb.read(out, 64)
    # The oldest 16 frames of 1.0 should have been dropped.
    # So 24 frames of 1.0 remain, followed by 40 frames of 2.0.
    assert np.allclose(out[:24], 1.0)
    assert np.allclose(out[24:], 2.0)


# --- Resampler Tests ---

def test_resample_mono_to_stereo():
    """Verify 1D mono audio is converted to stereo at matching rate."""
    mono = np.sin(np.linspace(0, 10, 48000)).astype(np.float32)
    res = resample_audio(mono, in_rate=48000, target_rate=48000)
    assert res.shape == (48000, 2)
    assert np.array_equal(res[:, 0], res[:, 1])


def test_resample_soxr():
    """Verify soxr resampling from 44100 to 48000."""
    t = np.linspace(0, 1, 44100, endpoint=False)
    sine = np.stack([np.sin(2 * np.pi * 440 * t), np.cos(2 * np.pi * 440 * t)], axis=1).astype(np.float32)
    res = resample_audio(sine, in_rate=44100, target_rate=48000)
    assert res.shape == (48000, 2)
    assert res.dtype == np.float32


def test_resample_linear_fallback():
    """Verify fallback linear resampling works when forced."""
    t = np.linspace(0, 1, 44100, endpoint=False)
    sine = np.stack([np.sin(2 * np.pi * 440 * t), np.cos(2 * np.pi * 440 * t)], axis=1).astype(np.float32)
    res = resample_audio(sine, in_rate=44100, target_rate=48000, force_fallback=True)
    assert res.shape == (48000, 2)
    assert res.dtype == np.float32
