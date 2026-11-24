"""Structured step logging for Additional Asset debugging."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from additional_asset.core import config


class StepLogger:
    def __init__(self, request_id: str, *, logger_name: str = "additional_asset"):
        self.request_id = request_id
        self.steps: list[dict[str, Any]] = []
        self._log = logging.getLogger(logger_name or "additional_asset")
        self._file = None
        self._file_path: Path | None = None
        if config.debug_log_to_file():
            try:
                debug_dir = Path(config.debug_log_dir())
                debug_dir.mkdir(parents=True, exist_ok=True)
                self._file_path = debug_dir / f"{request_id}.jsonl"
                self._file = open(self._file_path, "a", encoding="utf-8")
                self._log.info("[%s] debug log file: %s", request_id, self._file_path)
            except Exception as exc:
                self._log.warning("[%s] debug file log disabled: %s", request_id, exc)
                self._file = None

    @property
    def debug_file_path(self) -> str | None:
        return str(self._file_path) if self._file_path is not None else None

    def step(self, phase: str, latency_ms: float | None = None, **detail: Any) -> None:
        entry = {"phase": phase, "latency_ms": latency_ms, **detail}
        # Drop Nones for cleaner logs
        entry = {k: v for k, v in entry.items() if v is not None}
        self.steps.append(entry)
        try:
            payload = json.dumps(entry, default=str, ensure_ascii=False)
        except Exception:
            payload = str(entry)
        self._log.info("[%s] %s", self.request_id, payload)
        if self._file is not None:
            try:
                self._file.write(payload + "\n")
                self._file.flush()
            except Exception as exc:
                self._log.warning("[%s] debug file write failed: %s", self.request_id, exc)

    def info(self, message: str, **kwargs: Any) -> None:
        if kwargs:
            self.step("info", message=message, **kwargs)
        else:
            self._log.info("[%s] %s", self.request_id, message)

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None
