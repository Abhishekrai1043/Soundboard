"""Unit tests for the Engine and HotkeyManager (no audio hardware required)."""

from __future__ import annotations

import threading
import time
import types
import unittest.mock as mock

import numpy as np
import pytest

# ─── Patch sounddevice before importing engine ────────────────────────────────
# Build a fake sd module so tests run without an audio device.

def _make_fake_sd():
    m = types.ModuleType("sounddevice")
    m.query_devices = mock.MagicMock(return_value=[])

    class FakeStream:
        def __init__(self, **kw):
            self.callback = kw.get("callback")
        def start(self): pass
        def stop(self):  pass
        def close(self): pass

    m.OutputStream = lambda **kw: FakeStream(**kw)
    m.InputStream  = lambda **kw: FakeStream(**kw)
    return m

import sys
if "sounddevice" not in sys.modules:
    sys.modules["sounddevice"] = _make_fake_sd()
else:
    # Already imported; patch the classes for engine tests
    sd_mod = sys.modules["sounddevice"]
    class FakeStream:
        def start(self): pass
        def stop(self):  pass
        def close(self): pass
    sd_mod.OutputStream = lambda **kw: FakeStream()
    sd_mod.InputStream  = lambda **kw: FakeStream()

# Now it's safe to import engine
from pysoundboard.audio.engine import Engine, Mixer, SR


# ─── Engine / Mixer tests ─────────────────────────────────────────────────────

def test_engine_play_caches_sound(tmp_path):
    """Engine should cache loaded audio and reuse it on second play."""
    # Create a tiny WAV via soundfile
    import soundfile as sf
    wav = tmp_path / "ping.wav"
    data = np.zeros((SR, 2), dtype="float32")
    sf.write(str(wav), data, SR)

    engine = Engine()
    engine.cable.stream = object()   # fake "stream is open"
    engine.cable._voices = []

    engine.play(str(wav))
    engine.play(str(wav))

    assert str(wav) in engine._cache
    assert len(engine.cable._voices) == 2


def test_engine_stop_all():
    """stop_all() must clear voices from both mixers."""
    engine = Engine()
    dummy = np.zeros((128, 2), dtype="float32")
    engine.cable.add(dummy)
    engine.monitor.add(dummy)
    engine.stop_all()
    assert engine.cable._voices == []
    assert engine.monitor._voices == []


def test_engine_set_volume():
    """set_volume() propagates to both mixers and cable mic_volume."""
    engine = Engine()
    engine.set_volume(0.5, 0.3)
    assert engine.cable.volume   == pytest.approx(0.5)
    assert engine.monitor.volume == pytest.approx(0.5)
    assert engine.cable.mic_volume == pytest.approx(0.3)


def test_mixer_add_and_stop_all():
    """Mixer.add enqueues voices; stop_all clears them."""
    m = Mixer()
    data = np.ones((64, 2), dtype="float32")
    m.add(data, gain=0.7)
    m.add(data, gain=1.0)
    assert len(m._voices) == 2
    m.stop_all()
    assert len(m._voices) == 0


def test_mixer_output_callback_advances_voice():
    """_out_callback should advance voice position and drop finished voices."""
    m = Mixer()
    frames = 64
    data = np.ones((frames, 2), dtype="float32") * 0.5
    m.add(data, gain=1.0)

    outdata = np.zeros((frames, 2), dtype="float32")
    m._out_callback(outdata, frames, None, None)

    # Voice finished (data length == frames), should be gone
    assert len(m._voices) == 0
    # Output should be ~0.5 (volume=1.0 * gain=1.0)
    assert np.allclose(outdata, 0.5)


def test_mixer_output_callback_clips():
    """Output must be clipped to [-1, 1]."""
    m = Mixer()
    # Two voices that together exceed 1.0
    data = np.ones((32, 2), dtype="float32") * 0.8
    m.add(data)
    m.add(data)
    outdata = np.zeros((32, 2), dtype="float32")
    m._out_callback(outdata, 32, None, None)
    assert outdata.max() <= 1.0 + 1e-6
    assert outdata.min() >= -1.0 - 1e-6


def test_engine_clear_cache(tmp_path):
    """clear_cache() evicts entries so next play reloads from disk."""
    import soundfile as sf
    wav = tmp_path / "ding.wav"
    sf.write(str(wav), np.zeros((SR // 10, 2), dtype="float32"), SR)

    engine = Engine()
    engine.cable.stream = object()
    engine.cable._voices = []
    engine.play(str(wav))
    assert len(engine._cache) == 1

    engine.clear_cache()
    assert len(engine._cache) == 0


# ─── HotkeyManager tests ──────────────────────────────────────────────────────

def test_hotkey_manager_rebind_and_stop():
    """HotkeyManager.rebind should start a GlobalHotKeys listener."""
    from pysoundboard.core.hotkeys import HotkeyManager

    mgr = HotkeyManager()
    called = threading.Event()

    with mock.patch("pysoundboard.core.hotkeys.keyboard.GlobalHotKeys") as MockGHK:
        instance = MockGHK.return_value
        instance.start = mock.MagicMock()
        instance.stop  = mock.MagicMock()

        mgr.rebind({"<ctrl>+1": lambda: None})
        MockGHK.assert_called_once()
        instance.start.assert_called_once()

        mgr.stop()
        instance.stop.assert_called_once()


def test_hotkey_manager_empty_mapping():
    """Rebinding with empty mapping should not create a listener."""
    from pysoundboard.core.hotkeys import HotkeyManager

    mgr = HotkeyManager()
    with mock.patch("pysoundboard.core.hotkeys.keyboard.GlobalHotKeys") as MockGHK:
        mgr.rebind({})
        MockGHK.assert_not_called()


# ─── key_to_str tests ────────────────────────────────────────────────────────

def test_key_to_str_modifier():
    from pynput import keyboard
    from pysoundboard.core.hotkeys import key_to_str
    assert key_to_str(keyboard.Key.ctrl_l) == "<ctrl>"
    assert key_to_str(keyboard.Key.alt)    == "<alt>"
    assert key_to_str(keyboard.Key.shift)  == "<shift>"


def test_key_to_str_char():
    from pynput.keyboard import KeyCode
    from pysoundboard.core.hotkeys import key_to_str
    k = KeyCode(char="a")
    assert key_to_str(k) == "a"

    k2 = KeyCode(char="Z")
    assert key_to_str(k2) == "z"
