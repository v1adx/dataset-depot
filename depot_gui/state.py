"""The interface's json state: node positions and table settings.

One class for all four files, which used to be read and written by four nearly
identical pairs of load/save helpers. The key is the same everywhere — the
dataset's key — so one wrapper is enough.

A corrupt file reads as empty rather than taking the page down: interface state
rebuilds itself, and losing the graph over one stray comma would be a poor
trade.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StateFile:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return {}

    def write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get(self, key: str, default: Any = None) -> Any:
        return self.read().get(key, default)

    def set(self, key: str, value: Any) -> None:
        data = self.read()
        data[key] = value
        self.write(data)
