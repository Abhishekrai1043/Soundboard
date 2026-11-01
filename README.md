# PySoundboard

A PC soundboard: press a hotkey (or combo) and the sound plays into games, Discord, Zoom, TeamSpeak, etc.

## How it works

Apps can only "hear" a microphone, so the soundboard plays into a **virtual audio cable**. The cable's output shows up as a microphone in other apps.

```
sound file -> PySoundboard -> CABLE Input  ==>  CABLE Output (mic in Discord/game)
                           -> your headphones (monitor)
real mic   -> PySoundboard (optional mix-in) -> CABLE Input
```

## Setup (Windows)

1. Install **VB-CABLE** (https://vb-audio.com/Cable/) and reboot.
2. Install Python 3.10+, then:
   ```
   pip install -r requirements.txt
   python soundboard.py
   ```
3. In PySoundboard:
   - "Output to apps" = **CABLE Input**
   - "Monitor" = your headphones
   - "Real microphone" = your physical mic (so friends hear you AND the sounds)
   - Click **Apply devices**
4. In Discord/game voice settings set **Input Device = CABLE Output**.
5. Add sounds, select one, click **Set hotkey**, press the combo (e.g. Ctrl+Alt+1).

Tip: use different devices in the same host API if you see device errors; keep the mic's sample rate at 48 kHz in Windows Sound settings.

## Notes

- Hotkeys are global (work while a game is focused). Some games running as Administrator need PySoundboard to run as Administrator too.
- Some anti-cheat systems dislike global keyboard hooks; use at your own discretion.
- Config is stored in `~/.pysoundboard.json`.

## Package as .exe

```
pip install pyinstaller
pyinstaller --noconsole --onefile soundboard.py
```

## Roadmap ideas

Per-sound volume, loop/toggle mode, sound groups/tabs, tray icon, push-to-talk integration, mic noise gate, voice effects (pitch/echo), controller/MIDI/Stream Deck triggers, or a rewrite in PySide6 / C# (NAudio) / Rust for lower latency.
