"""Semantic desktop control backed by the system AT-SPI service."""

import json
from pathlib import Path
import subprocess


class SemanticDesktopController:
    def __init__(self):
        self.helper = Path(__file__).with_name("atspi_helper.py")

    def _run(self, *arguments, timeout=12):
        try:
            result = subprocess.run(
                ["/usr/bin/python3", str(self.helper), *arguments],
                capture_output=True, text=True, check=False, timeout=timeout,
            )
            payload = json.loads(result.stdout)
        except (FileNotFoundError, subprocess.SubprocessError, json.JSONDecodeError):
            return {"ok": False, "error": "accessibility_unavailable"}
        return payload if isinstance(payload, dict) else {"ok": False}

    def controls(self, window_title=""):
        payload = self._run("list", str(window_title))
        controls = payload.get("controls", [])
        return controls if payload.get("ok") and isinstance(controls, list) else []

    def activate(self, target_id, window_title=""):
        payload = self._run("activate", str(target_id), str(window_title))
        return bool(payload.get("ok")), payload
