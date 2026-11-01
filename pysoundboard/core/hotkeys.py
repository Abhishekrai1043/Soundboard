"""
Hotkey utilities for PySoundboard.

Provides:
  key_to_str()        – normalises a pynput Key/KeyCode to a canonical string
  HotkeyRecorder      – captures one combo while the user holds then releases keys
  HotkeyManager       – registers/unregisters GlobalHotKeys for all sounds + stop-all
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, Optional

from pynput import keyboard

logger = logging.getLogger(__name__)


def key_to_str(k: keyboard.Key | keyboard.KeyCode) -> Optional[str]:
    """Convert a pynput key to canonical combo-part string, or None if unmappable."""
    if isinstance(k, keyboard.Key):
        name = k.name
        for base in ("ctrl", "alt", "shift", "cmd"):
            if name.startswith(base):
                return f"<{base}>"
        if name == "alt_gr":
            return "<alt>"
        return f"<{name}>"
    ch = getattr(k, "char", None)
    vk = getattr(k, "vk", None)
    if ch and ch.isprintable():
        return ch.lower()
    if vk is not None:
        if 48 <= vk <= 90:
            return chr(vk).lower()
        return f"<{vk}>"
    return None


class HotkeyRecorder:
    """Records one key combination.  Calls *on_done(combo_string)* on first key-up."""

    def __init__(self, on_done: Callable[[str], None]) -> None:
        self._pressed: list[str] = []
        self._on_done = on_done
        self._listener = keyboard.Listener(
            on_press=self._press,
            on_release=self._release,
        )
        self._listener.start()

    def _press(self, k) -> None:
        s = key_to_str(k)
        if s and s not in self._pressed:
            self._pressed.append(s)

    def _release(self, k) -> None:
        mods = [m for m in ("<ctrl>", "<alt>", "<shift>", "<cmd>") if m in self._pressed]
        rest = [p for p in self._pressed if p not in mods]
        combo = "+".join(mods + rest)
        self._on_done(combo)
        return False   # stop listener


class HotkeyManager:
    """Manages a single GlobalHotKeys listener for all sound and stop-all hotkeys."""

    def __init__(self) -> None:
        self._listener: Optional[keyboard.GlobalHotKeys] = None
        self._lock = threading.Lock()

    def rebind(self, mapping: Dict[str, Callable[[], None]]) -> None:
        """Replace the active hotkey set with *mapping* {combo: callback}."""
        with self._lock:
            self._stop_listener()
            if not mapping:
                return
            try:
                self._listener = keyboard.GlobalHotKeys(mapping)
                self._listener.start()
                logger.info("Hotkeys bound: %s", list(mapping.keys()))
            except Exception as exc:
                logger.error("Failed to bind hotkeys: %s", exc)
                raise

    def stop(self) -> None:
        """Stop the active listener if any."""
        with self._lock:
            self._stop_listener()

    def _stop_listener(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception as exc:
                logger.warning("Error stopping hotkey listener: %s", exc)
            self._listener = None
