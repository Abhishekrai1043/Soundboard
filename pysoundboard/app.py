"""
PySoundboard – main GUI entry point.

Run:   python -m pysoundboard.app
or:    python soundboard_app.py

Requires VB-CABLE (or similar) on Windows; see README.md.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk
import sounddevice as sd

from pysoundboard.audio.engine import Engine
from pysoundboard.core.config import ConfigManager
from pysoundboard.core.hotkeys import HotkeyManager, HotkeyRecorder
from pysoundboard.core.models import AppConfig, SoundItem

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

# ─── Appearance ──────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_ACCENT   = "#4f8ef7"
_ACCENT2  = "#7b5ea7"
_BG_CARD  = "#1e1e2e"
_BG_DEEP  = "#13131f"
_FG_DIM   = "#8888aa"
_RED      = "#e05252"
_GREEN    = "#4ade80"

SOUND_COLORS = [
    "#4f8ef7", "#7b5ea7", "#e05252", "#4ade80",
    "#f59e0b", "#06b6d4", "#f97316", "#a78bfa",
]


# ─── Device helpers ──────────────────────────────────────────────────────────

def _query_devices(kind: str) -> list[str]:
    """Return list of device names of a given direction ('max_output_channels' / 'max_input_channels')."""
    names = [""]
    try:
        for d in sd.query_devices():
            if d[kind] > 0:
                names.append(d["name"])
    except Exception as exc:
        logger.warning("Failed to query devices: %s", exc)
    return names


# ─── Sound card widget ───────────────────────────────────────────────────────

class SoundCard(ctk.CTkFrame):
    """A single sound tile with name, hotkey badge, play/remove buttons."""

    HEIGHT = 80

    def __init__(self, master, item: SoundItem, on_play, on_remove, on_set_hotkey, **kwargs):
        super().__init__(master, corner_radius=12, fg_color=_BG_CARD, **kwargs)
        self.item = item
        self._on_play = on_play
        self._on_remove = on_remove
        self._on_set_hotkey = on_set_hotkey
        self._build()

    def _build(self) -> None:
        color = self.item.color or _ACCENT
        # Colour stripe on the left
        stripe = ctk.CTkFrame(self, width=6, corner_radius=0, fg_color=color)
        stripe.pack(side="left", fill="y")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(side="left", fill="both", expand=True, padx=10, pady=8)

        # Name
        name_lbl = ctk.CTkLabel(
            body, text=self.item.name, font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        )
        name_lbl.pack(anchor="w")

        # Hotkey badge
        hk = self.item.hotkey or "No hotkey"
        self._hk_label = ctk.CTkLabel(
            body, text=f"⌨  {hk}",
            font=ctk.CTkFont(size=11),
            text_color=_FG_DIM,
            anchor="w",
        )
        self._hk_label.pack(anchor="w")

        # Buttons on the right
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(side="right", padx=10)

        ctk.CTkButton(
            btns, text="▶", width=36, height=36, corner_radius=8,
            fg_color=color, hover_color=_ACCENT2,
            command=self._play,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            btns, text="⌨", width=36, height=36, corner_radius=8,
            fg_color="#2a2a3e", hover_color=_ACCENT,
            command=self._hotkey,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            btns, text="✕", width=36, height=36, corner_radius=8,
            fg_color="#2a2a3e", hover_color=_RED,
            command=self._remove,
        ).pack(side="left", padx=2)

    def refresh_hotkey(self) -> None:
        hk = self.item.hotkey or "No hotkey"
        self._hk_label.configure(text=f"⌨  {hk}")

    def _play(self):    self._on_play(self.item)
    def _remove(self):  self._on_remove(self.item)
    def _hotkey(self):  self._on_set_hotkey(self.item, self)


# ─── Main application window ─────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PySoundboard")
        self.geometry("900x660")
        self.minsize(760, 520)
        self.configure(fg_color=_BG_DEEP)

        self._cfg_mgr = ConfigManager()
        self.cfg: AppConfig = self._cfg_mgr.load()

        self.engine = Engine()
        self.hotkey_mgr = HotkeyManager()

        self._card_widgets: dict[str, SoundCard] = {}   # item.id → card
        self._recorder: Optional[HotkeyRecorder] = None

        self._build_ui()
        self._apply_audio(silent=True)
        self._rebind()
        self.protocol("WM_DELETE_WINDOW", self._quit)

    # ═════════════════════════════════════════════════════════════════ UI build

    def _build_ui(self) -> None:
        # ── Left sidebar ──────────────────────────────────────────────────────
        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=_BG_CARD)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        ctk.CTkLabel(
            sidebar, text="🔊 PySoundboard",
            font=ctk.CTkFont(size=17, weight="bold"),
        ).pack(padx=16, pady=(20, 6), anchor="w")

        ctk.CTkLabel(
            sidebar, text="Audio Devices", font=ctk.CTkFont(size=11),
            text_color=_FG_DIM,
        ).pack(padx=16, anchor="w")
        self._build_device_section(sidebar)

        ctk.CTkLabel(
            sidebar, text="Volume", font=ctk.CTkFont(size=11),
            text_color=_FG_DIM,
        ).pack(padx=16, pady=(14, 0), anchor="w")
        self._build_volume_section(sidebar)

        ctk.CTkLabel(
            sidebar, text="Global Stop Hotkey", font=ctk.CTkFont(size=11),
            text_color=_FG_DIM,
        ).pack(padx=16, pady=(14, 0), anchor="w")
        self._build_stop_section(sidebar)

        # ── Main content ──────────────────────────────────────────────────────
        main = ctk.CTkFrame(self, corner_radius=0, fg_color=_BG_DEEP)
        main.pack(side="left", fill="both", expand=True)

        # Header bar
        header = ctk.CTkFrame(main, fg_color=_BG_CARD, height=56, corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header, text="Sounds", font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(side="left", padx=20, pady=10)

        ctk.CTkButton(
            header, text="+ Add Sound", width=120, height=34,
            fg_color=_ACCENT, hover_color=_ACCENT2, corner_radius=8,
            command=self._add_sounds,
        ).pack(side="right", padx=14, pady=10)

        ctk.CTkButton(
            header, text="⏹ Stop All", width=100, height=34,
            fg_color=_RED, hover_color="#c04040", corner_radius=8,
            command=self._stop_all,
        ).pack(side="right", padx=4, pady=10)

        # Scroll area for sound cards
        self._scroll = ctk.CTkScrollableFrame(main, fg_color=_BG_DEEP, corner_radius=0)
        self._scroll.pack(fill="both", expand=True, padx=16, pady=14)

        # Status bar
        self._status_var = tk.StringVar(value="Ready")
        ctk.CTkLabel(
            main, textvariable=self._status_var,
            font=ctk.CTkFont(size=11), text_color=_FG_DIM, anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 6))

        self._refresh_cards()

    def _build_device_section(self, parent) -> None:
        out_devs = _query_devices("max_output_channels")
        in_devs  = _query_devices("max_input_channels")

        self._dev_cable   = self._combo(parent, "Output (virtual cable):", out_devs, self.cfg.cable_device)
        self._dev_monitor = self._combo(parent, "Monitor (headphones):",   out_devs, self.cfg.monitor_device)
        self._dev_mic     = self._combo(parent, "Microphone (optional):",  in_devs,  self.cfg.mic_device)

        ctk.CTkButton(
            parent, text="Apply Devices", corner_radius=8,
            fg_color=_ACCENT, hover_color=_ACCENT2,
            command=self._on_apply_devices,
        ).pack(padx=16, pady=(6, 0), fill="x")

    def _combo(self, parent, label: str, values: list[str], current: str) -> ctk.CTkComboBox:
        ctk.CTkLabel(parent, text=label, font=ctk.CTkFont(size=11), text_color=_FG_DIM).pack(
            padx=16, pady=(6, 0), anchor="w",
        )
        var = ctk.StringVar(value=current)
        cb = ctk.CTkComboBox(parent, variable=var, values=values, width=190, state="readonly")
        cb.pack(padx=16, pady=(2, 0), fill="x")
        return cb

    def _build_volume_section(self, parent) -> None:
        self._vol_var = ctk.DoubleVar(value=self.cfg.master_volume)
        self._mic_vol_var = ctk.DoubleVar(value=self.cfg.mic_volume)

        for label, var, cmd in [
            ("Sound", self._vol_var, self._on_volume_change),
            ("Mic",   self._mic_vol_var, self._on_volume_change),
        ]:
            ctk.CTkLabel(parent, text=label, font=ctk.CTkFont(size=11), text_color=_FG_DIM).pack(
                padx=16, pady=(4, 0), anchor="w",
            )
            ctk.CTkSlider(parent, from_=0, to=1.5, variable=var, command=lambda _=None, c=cmd: c()).pack(
                padx=16, fill="x",
            )

    def _build_stop_section(self, parent) -> None:
        self._stop_key_var = tk.StringVar(value=self.cfg.stop_hotkey)
        ctk.CTkLabel(
            parent, textvariable=self._stop_key_var,
            font=ctk.CTkFont(size=12, weight="bold"), text_color=_ACCENT,
        ).pack(padx=16, anchor="w")

        ctk.CTkButton(
            parent, text="Set Stop Key", corner_radius=8,
            fg_color="#2a2a3e", hover_color=_ACCENT,
            command=self._set_stop_key,
        ).pack(padx=16, pady=(4, 0), fill="x")

    # ═══════════════════════════════════════════════════════════════ Sound cards

    def _refresh_cards(self) -> None:
        """Rebuild the sound card grid from config."""
        for w in self._card_widgets.values():
            w.destroy()
        self._card_widgets.clear()

        for item in self.cfg.sounds:
            self._add_card(item)

    def _add_card(self, item: SoundItem) -> None:
        color_idx = len(self._card_widgets) % len(SOUND_COLORS)
        if not item.color:
            item.color = SOUND_COLORS[color_idx]
        card = SoundCard(
            self._scroll, item,
            on_play=self._play_item,
            on_remove=self._remove_item,
            on_set_hotkey=self._set_hotkey_for,
        )
        card.pack(fill="x", pady=4)
        self._card_widgets[item.id] = card

    # ═════════════════════════════════════════════════════════════════ Actions

    def _add_sounds(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Add sounds",
            filetypes=[("Audio", "*.wav *.mp3 *.ogg *.flac *.aiff *.m4a"), ("All", "*.*")],
        )
        added = 0
        for p in paths:
            item = SoundItem(
                name=os.path.splitext(os.path.basename(p))[0],
                file_path=p,
            )
            self.cfg.sounds.append(item)
            self._add_card(item)
            added += 1
        if added:
            self._save()
            self._rebind()
            self._status(f"Added {added} sound(s).")

    def _play_item(self, item: SoundItem) -> None:
        try:
            self.engine.play(item.file_path, gain=item.volume)
            self._status(f"Playing: {item.name}")
        except Exception as exc:
            messagebox.showerror("Play error", str(exc))

    def _remove_item(self, item: SoundItem) -> None:
        self.cfg.sounds = [s for s in self.cfg.sounds if s.id != item.id]
        card = self._card_widgets.pop(item.id, None)
        if card:
            card.destroy()
        self._save()
        self._rebind()
        self._status(f"Removed: {item.name}")

    def _stop_all(self) -> None:
        self.engine.stop_all()
        self._status("Stopped all sounds.")

    # ═══════════════════════════════════════════════════════════════ Hotkeys

    def _set_hotkey_for(self, item: SoundItem, card: SoundCard) -> None:
        self._status("Press your key combo now…")
        self._recorder = HotkeyRecorder(
            on_done=lambda combo: self.after(0, self._hotkey_recorded, item, card, combo)
        )

    def _hotkey_recorded(self, item: SoundItem, card: SoundCard, combo: str) -> None:
        item.hotkey = combo
        card.refresh_hotkey()
        self._save()
        self._rebind()
        self._status(f"Hotkey set: {item.name} → {combo}")

    def _set_stop_key(self) -> None:
        self._status("Press key combo for Stop All…")
        self._recorder = HotkeyRecorder(
            on_done=lambda combo: self.after(0, self._stop_key_recorded, combo)
        )

    def _stop_key_recorded(self, combo: str) -> None:
        self.cfg.stop_hotkey = combo
        self._stop_key_var.set(combo)
        self._save()
        self._rebind()
        self._status(f"Stop hotkey set: {combo}")

    def _rebind(self) -> None:
        mapping: dict[str, callable] = {}
        for s in self.cfg.sounds:
            if s.hotkey:
                mapping[s.hotkey] = (lambda f=s.file_path: self._safe_play(f))
        if self.cfg.stop_hotkey:
            mapping[self.cfg.stop_hotkey] = self._stop_all
        try:
            self.hotkey_mgr.rebind(mapping)
        except Exception as exc:
            messagebox.showerror("Hotkey error", str(exc))

    def _safe_play(self, path: str) -> None:
        try:
            self.engine.play(path)
        except Exception as exc:
            logger.error("play error: %s", exc)

    # ═══════════════════════════════════════════════════════════════ Audio

    def _on_apply_devices(self) -> None:
        self.cfg.cable_device   = self._dev_cable.get()
        self.cfg.monitor_device = self._dev_monitor.get()
        self.cfg.mic_device     = self._dev_mic.get()
        self._apply_audio()
        self._save()

    def _apply_audio(self, silent: bool = False) -> None:
        cable = self.cfg.cable_device
        if not cable:
            self._status("Choose an output device (virtual cable) and press Apply.")
            return
        try:
            self.engine.start(
                cable_dev=cable,
                monitor_dev=self.cfg.monitor_device or None,
                mic_dev=self.cfg.mic_device or None,
            )
            self.engine.set_volume(self.cfg.master_volume, self.cfg.mic_volume)
            if not silent:
                self._status("Audio devices applied.")
        except Exception as exc:
            if not silent:
                messagebox.showerror("Audio error", str(exc))
            else:
                logger.error("Audio startup error: %s", exc)

    def _on_volume_change(self) -> None:
        v  = self._vol_var.get()
        mv = self._mic_vol_var.get()
        self.cfg.master_volume = v
        self.cfg.mic_volume = mv
        self.engine.set_volume(v, mv)

    # ═════════════════════════════════════════════════════════════════ Helpers

    def _status(self, msg: str) -> None:
        self._status_var.set(msg)

    def _save(self) -> None:
        try:
            self._cfg_mgr.save(self.cfg)
        except Exception as exc:
            logger.error("Config save failed: %s", exc)

    def _quit(self) -> None:
        self._save()
        self.hotkey_mgr.stop()
        self.engine.shutdown()
        self.destroy()


# ─── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
