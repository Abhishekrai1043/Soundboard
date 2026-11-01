"""
PySoundboard - PC soundboard with global hotkeys.

Audio flow:
  sound file -> [mixer A] -> virtual cable input (apps hear it as a "microphone")
             -> [mixer B] -> your headphones (so you hear it too)
  optional: real microphone is mixed into mixer A so teammates hear you + the sounds.

Requires a virtual audio cable (e.g. VB-CABLE) on Windows. See README.md.
"""
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
import sounddevice as sd
import soundfile as sf
from pynput import keyboard

SR = 48000
BLOCK = 512
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".pysoundboard.json")


# ----------------------------------------------------------------- audio ---
def load_sound(path):
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    elif data.shape[1] > 2:
        data = data[:, :2]
    if sr != SR:  # simple linear resample
        n = int(len(data) * SR / sr)
        x_old = np.linspace(0, 1, len(data), endpoint=False)
        x_new = np.linspace(0, 1, n, endpoint=False)
        data = np.stack([np.interp(x_new, x_old, data[:, c]) for c in range(2)], axis=1)
        data = data.astype("float32")
    return data


class Mixer:
    """One output stream that sums all active voices (+ optional mic)."""

    def __init__(self):
        self.voices = []  # each: [data, position]
        self.lock = threading.Lock()
        self.volume = 1.0
        self.mic_q = None  # queue of mic blocks, set when mic is used
        self.mic_volume = 1.0
        self.stream = None
        self.mic_stream = None

    def add(self, data, gain=1.0):
        with self.lock:
            self.voices.append([data, 0, gain])

    def stop_all(self):
        with self.lock:
            self.voices.clear()

    def _callback(self, outdata, frames, time_info, status):
        out = np.zeros((frames, 2), dtype="float32")
        with self.lock:
            alive = []
            for v in self.voices:
                data, pos, gain = v
                chunk = data[pos:pos + frames]
                out[:len(chunk)] += chunk * gain
                v[1] = pos + frames
                if v[1] < len(data):
                    alive.append(v)
            self.voices = alive
        out *= self.volume
        if self.mic_q is not None:
            try:
                mic = self.mic_q.get_nowait()
                out[:len(mic)] += mic * self.mic_volume
            except queue.Empty:
                pass
        np.clip(out, -1.0, 1.0, out=out)
        outdata[:] = out

    def _mic_callback(self, indata, frames, time_info, status):
        block = np.repeat(indata[:, :1], 2, axis=1).copy()
        if self.mic_q.qsize() > 8:  # drop old audio to keep latency low
            try:
                self.mic_q.get_nowait()
            except queue.Empty:
                pass
        self.mic_q.put(block)

    def start(self, out_dev, mic_dev=None):
        self.stop()
        self.stream = sd.OutputStream(
            samplerate=SR, blocksize=BLOCK, channels=2, dtype="float32",
            device=out_dev, callback=self._callback)
        if mic_dev is not None:
            self.mic_q = queue.Queue()
            self.mic_stream = sd.InputStream(
                samplerate=SR, blocksize=BLOCK, channels=1, dtype="float32",
                device=mic_dev, callback=self._mic_callback)
            self.mic_stream.start()
        self.stream.start()

    def stop(self):
        for s in (self.stream, self.mic_stream):
            if s is not None:
                s.stop()
                s.close()
        self.stream = self.mic_stream = None
        self.mic_q = None


class Engine:
    def __init__(self):
        self.cable = Mixer()    # goes to the virtual cable
        self.monitor = Mixer()  # goes to your headphones
        self.cache = {}

    def start(self, cable_dev, monitor_dev, mic_dev):
        self.cable.start(cable_dev, mic_dev)
        if monitor_dev is not None:
            self.monitor.start(monitor_dev)

    def play(self, path, gain=1.0):
        if path not in self.cache:
            self.cache[path] = load_sound(path)
        data = self.cache[path]
        if self.cable.stream:
            self.cable.add(data, gain)
        if self.monitor.stream:
            self.monitor.add(data, gain)

    def stop_all(self):
        self.cable.stop_all()
        self.monitor.stop_all()

    def shutdown(self):
        self.cable.stop()
        self.monitor.stop()


# --------------------------------------------------------------- hotkeys ---
def key_to_str(k):
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
    """Captures one key combination (released when the first key goes up)."""

    def __init__(self, on_done):
        self.pressed = []
        self.on_done = on_done
        self.listener = keyboard.Listener(on_press=self._press, on_release=self._release)
        self.listener.start()

    def _press(self, k):
        s = key_to_str(k)
        if s and s not in self.pressed:
            self.pressed.append(s)

    def _release(self, k):
        mods = [m for m in ("<ctrl>", "<alt>", "<shift>", "<cmd>") if m in self.pressed]
        rest = [p for p in self.pressed if p not in mods]
        combo = "+".join(mods + rest)
        self.on_done(combo)
        return False


# ------------------------------------------------------------------- app ---
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PySoundboard")
        self.geometry("760x560")
        self.engine = Engine()
        self.hotkey_listener = None
        self.cfg = {"cable": "", "monitor": "", "mic": "", "volume": 1.0,
                    "mic_volume": 1.0, "stop_key": "<ctrl>+<alt>+0", "sounds": []}
        self._load_cfg()
        self._build_ui()
        self._apply_audio()
        self._rebind()
        self.protocol("WM_DELETE_WINDOW", self._quit)

    # config
    def _load_cfg(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, encoding="utf-8") as f:
                    self.cfg.update(json.load(f))
            except Exception:
                pass

    def _save_cfg(self):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, indent=2)

    # ui
    def _devices(self, kind):
        names = [""]
        for d in sd.query_devices():
            if d[kind] > 0:
                names.append(d["name"])
        return names

    def _build_ui(self):
        top = ttk.LabelFrame(self, text="Audio devices")
        top.pack(fill="x", padx=10, pady=8)
        self.vars = {}
        rows = [("cable", "Output to apps (e.g. CABLE Input):", "max_output_channels"),
                ("monitor", "Monitor (your headphones):", "max_output_channels"),
                ("mic", "Real microphone (optional mix-in):", "max_input_channels")]
        for i, (key, label, kind) in enumerate(rows):
            ttk.Label(top, text=label).grid(row=i, column=0, sticky="w", padx=6, pady=3)
            var = tk.StringVar(value=self.cfg[key])
            cb = ttk.Combobox(top, textvariable=var, values=self._devices(kind),
                              width=55, state="readonly")
            cb.grid(row=i, column=1, padx=6)
            self.vars[key] = var
        ttk.Button(top, text="Apply devices", command=self._on_apply).grid(
            row=0, column=2, rowspan=3, padx=8)

        vol = ttk.Frame(self)
        vol.pack(fill="x", padx=10)
        ttk.Label(vol, text="Sound volume").pack(side="left")
        self.vol = tk.DoubleVar(value=self.cfg["volume"])
        ttk.Scale(vol, from_=0, to=1.5, variable=self.vol, command=self._on_vol,
                  length=200).pack(side="left", padx=8)
        ttk.Label(vol, text="Mic volume").pack(side="left", padx=(20, 0))
        self.mvol = tk.DoubleVar(value=self.cfg["mic_volume"])
        ttk.Scale(vol, from_=0, to=1.5, variable=self.mvol, command=self._on_vol,
                  length=200).pack(side="left", padx=8)

        mid = ttk.LabelFrame(self, text="Sounds")
        mid.pack(fill="both", expand=True, padx=10, pady=8)
        self.tree = ttk.Treeview(mid, columns=("name", "hotkey", "file"),
                                 show="headings", height=12)
        for c, w in (("name", 150), ("hotkey", 170), ("file", 380)):
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=w)
        self.tree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(mid, command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", lambda e: self._test_play())

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(btns, text="Add sound", command=self._add).pack(side="left")
        ttk.Button(btns, text="Set hotkey", command=self._set_hotkey).pack(side="left", padx=4)
        ttk.Button(btns, text="Play", command=self._test_play).pack(side="left")
        ttk.Button(btns, text="Remove", command=self._remove).pack(side="left", padx=4)
        ttk.Button(btns, text="STOP ALL", command=self.engine.stop_all).pack(side="right")
        ttk.Button(btns, text="Set stop hotkey", command=self._set_stop_key).pack(side="right", padx=4)
        self.status = ttk.Label(self, text="", foreground="gray")
        self.status.pack(fill="x", padx=10)
        self._refresh()

    def _refresh(self):
        self.tree.delete(*self.tree.get_children())
        for i, s in enumerate(self.cfg["sounds"]):
            self.tree.insert("", "end", iid=str(i),
                             values=(s["name"], s.get("hotkey", ""), s["file"]))
        self.status.config(text=f"Stop-all hotkey: {self.cfg['stop_key']}")

    # actions
    def _on_vol(self, _=None):
        self.cfg["volume"] = self.vol.get()
        self.cfg["mic_volume"] = self.mvol.get()
        self.engine.cable.volume = self.engine.monitor.volume = self.vol.get()
        self.engine.cable.mic_volume = self.mvol.get()

    def _on_apply(self):
        for k, v in self.vars.items():
            self.cfg[k] = v.get()
        self._apply_audio()
        self._save_cfg()

    def _apply_audio(self):
        cable = self.cfg["cable"] or None
        if cable is None:
            self.status.config(text="Choose an output device (virtual cable) and press Apply.")
            return
        try:
            self.engine.start(cable, self.cfg["monitor"] or None, self.cfg["mic"] or None)
            self._on_vol()
        except Exception as e:
            messagebox.showerror("Audio error", str(e))

    def _add(self):
        paths = filedialog.askopenfilenames(
            filetypes=[("Audio", "*.wav *.mp3 *.ogg *.flac"), ("All", "*.*")])
        for p in paths:
            self.cfg["sounds"].append(
                {"name": os.path.splitext(os.path.basename(p))[0], "file": p, "hotkey": ""})
        self._save_cfg()
        self._refresh()

    def _selected(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _remove(self):
        i = self._selected()
        if i is not None:
            del self.cfg["sounds"][i]
            self._save_cfg()
            self._refresh()
            self._rebind()

    def _test_play(self):
        i = self._selected()
        if i is not None:
            self.engine.play(self.cfg["sounds"][i]["file"])

    def _record(self, callback):
        self.status.config(text="Press your key combination now...")

        def done(combo):
            self.after(0, lambda: callback(combo))

        HotkeyRecorder(done)

    def _set_hotkey(self):
        i = self._selected()
        if i is None:
            return

        def cb(combo):
            self.cfg["sounds"][i]["hotkey"] = combo
            self._save_cfg()
            self._refresh()
            self._rebind()

        self._record(cb)

    def _set_stop_key(self):
        def cb(combo):
            self.cfg["stop_key"] = combo
            self._save_cfg()
            self._refresh()
            self._rebind()

        self._record(cb)

    def _rebind(self):
        if self.hotkey_listener:
            self.hotkey_listener.stop()
        mapping = {}
        for s in self.cfg["sounds"]:
            hk = s.get("hotkey")
            if hk:
                mapping[hk] = (lambda f=s["file"]: self._safe_play(f))
        if self.cfg.get("stop_key"):
            mapping[self.cfg["stop_key"]] = self.engine.stop_all
        try:
            self.hotkey_listener = keyboard.GlobalHotKeys(mapping)
            self.hotkey_listener.start()
        except Exception as e:
            messagebox.showerror("Hotkey error", f"Invalid hotkey set: {e}")

    def _safe_play(self, f):
        try:
            self.engine.play(f)
        except Exception as e:
            print("play error:", e)

    def _quit(self):
        self._save_cfg()
        if self.hotkey_listener:
            self.hotkey_listener.stop()
        self.engine.shutdown()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
