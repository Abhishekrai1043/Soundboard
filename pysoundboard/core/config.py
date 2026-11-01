"""Configuration manager with atomic persistence, backup, and migrations."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from typing import Optional

from pysoundboard.core.models import AppConfig

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".pysoundboard.json")


class ConfigManager:
    """Manages application configuration persistence."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or DEFAULT_CONFIG_PATH
        self.config = AppConfig()

    def load(self) -> AppConfig:
        """Load configuration from disk with fallback and corruption backup."""
        if not os.path.exists(self.path):
            logger.info("Config file does not exist at %s. Using default config.", self.path)
            self.config = AppConfig()
            return self.config

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            if not isinstance(raw_data, dict):
                raise ValueError("Config JSON must be a dictionary.")
            self.config = AppConfig.from_dict(raw_data)
            logger.info("Config loaded successfully from %s (schema v%d)", self.path, self.config.schema_version)
            return self.config
        except Exception as e:
            logger.error("Failed to load config from %s: %s", self.path, e)
            self._backup_corrupt_file()
            self.config = AppConfig()
            return self.config

    def save(self, config: Optional[AppConfig] = None) -> None:
        """Atomically save configuration to disk."""
        if config is not None:
            self.config = config

        data = self.config.to_dict()
        dir_name = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(dir_name, exist_ok=True)

        # Write to temporary file in the same directory, then atomically replace
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".cfg_tmp_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
            logger.info("Config saved atomically to %s", self.path)
        except Exception as e:
            logger.error("Failed to save config atomically: %s", e)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise

    def _backup_corrupt_file(self) -> None:
        """Preserve corrupt config file so user data is not lost."""
        if not os.path.exists(self.path):
            return
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_path = f"{self.path}.corrupt_{timestamp}.bak"
        try:
            shutil.copy2(self.path, backup_path)
            logger.warning("Corrupt config backed up to %s", backup_path)
        except Exception as err:
            logger.error("Failed to backup corrupt config: %s", err)
