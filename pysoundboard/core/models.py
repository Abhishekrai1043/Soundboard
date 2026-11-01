"""Data models for PySoundboard."""

from __future__ import annotations

import os
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SoundItem:
    """Represents a single sound in the soundboard."""

    name: str
    file_path: str
    hotkey: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    volume: float = 1.0
    loop: bool = False
    color: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert SoundItem to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SoundItem:
        """Create SoundItem from dictionary, supporting legacy keys."""
        # Support legacy "file" key
        file_path = data.get("file_path") or data.get("file", "")
        name = data.get("name") or os.path.splitext(os.path.basename(file_path))[0]
        return cls(
            id=data.get("id") or uuid.uuid4().hex[:8],
            name=name,
            file_path=file_path,
            hotkey=data.get("hotkey", ""),
            volume=float(data.get("volume", 1.0)),
            loop=bool(data.get("loop", False)),
            color=data.get("color", ""),
        )


@dataclass
class AudioDeviceInfo:
    """Represents an enumerated audio device."""

    index: int
    name: str
    host_api: str
    max_input_channels: int
    max_output_channels: int
    default_sample_rate: float

    @property
    def display_name(self) -> str:
        """Display name with Host API to disambiguate Windows duplicates."""
        return f"{self.name} [{self.host_api}]"

    @property
    def identifier(self) -> str:
        """Unique identifier combining Host API and device name."""
        return f"{self.host_api}::{self.name}"


@dataclass
class AppConfig:
    """Application configuration with schema versioning."""

    schema_version: int = 1
    cable_device: str = ""
    monitor_device: str = ""
    mic_device: str = ""
    cable_host_api: str = ""
    monitor_host_api: str = ""
    mic_host_api: str = ""
    master_volume: float = 1.0
    mic_volume: float = 1.0
    mic_enabled: bool = False
    stop_hotkey: str = "<ctrl>+<alt>+0"
    sounds: List[SoundItem] = field(default_factory=list)
    theme: str = "dark"
    minimize_to_tray: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert AppConfig to dictionary for JSON persistence."""
        return {
            "schema_version": self.schema_version,
            "cable_device": self.cable_device,
            "monitor_device": self.monitor_device,
            "mic_device": self.mic_device,
            "cable_host_api": self.cable_host_api,
            "monitor_host_api": self.monitor_host_api,
            "mic_host_api": self.mic_host_api,
            "master_volume": self.master_volume,
            "mic_volume": self.mic_volume,
            "mic_enabled": self.mic_enabled,
            "stop_hotkey": self.stop_hotkey,
            "theme": self.theme,
            "minimize_to_tray": self.minimize_to_tray,
            "sounds": [s.to_dict() for s in self.sounds],
            # Legacy compatibility keys
            "cable": self.cable_device,
            "monitor": self.monitor_device,
            "mic": self.mic_device,
            "volume": self.master_volume,
            "stop_key": self.stop_hotkey,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AppConfig:
        """Construct AppConfig from dict with legacy fallback."""
        schema_ver = data.get("schema_version", 0)

        # Legacy mappings
        cable = data.get("cable_device") or data.get("cable", "")
        monitor = data.get("monitor_device") or data.get("monitor", "")
        mic = data.get("mic_device") or data.get("mic", "")
        master_vol = float(data.get("master_volume", data.get("volume", 1.0)))
        mic_vol = float(data.get("mic_volume", 1.0))
        # If legacy config had a mic set, default mic_enabled to True if volume > 0
        mic_enabled = bool(data.get("mic_enabled", bool(mic)))
        stop_key = data.get("stop_hotkey") or data.get("stop_key", "<ctrl>+<alt>+0")

        sounds_raw = data.get("sounds", [])
        sounds = [SoundItem.from_dict(s) for s in sounds_raw if isinstance(s, dict)]

        return cls(
            schema_version=max(schema_ver, 1),
            cable_device=cable,
            monitor_device=monitor,
            mic_device=mic,
            cable_host_api=data.get("cable_host_api", ""),
            monitor_host_api=data.get("monitor_host_api", ""),
            mic_host_api=data.get("mic_host_api", ""),
            master_volume=master_vol,
            mic_volume=mic_vol,
            mic_enabled=mic_enabled,
            stop_hotkey=stop_key,
            sounds=sounds,
            theme=data.get("theme", "dark"),
            minimize_to_tray=bool(data.get("minimize_to_tray", True)),
        )
