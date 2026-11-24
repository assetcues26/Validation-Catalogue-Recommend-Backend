"""Structured logging and phase timing."""

import json
import logging
import time
from datetime import datetime

try:
    import psutil
    MEMORY_MONITORING = True
except ImportError:
    MEMORY_MONITORING = False
    logging.warning("psutil not available - memory monitoring disabled")


class PhaseTimer:
    """Per-request phase timing for latency monitoring."""

    def __init__(self):
        self._starts: dict[str, float] = {}
        self.phases_ms: dict[str, float] = {}

    def start(self, name: str) -> None:
        self._starts[name] = time.perf_counter()

    def stop(self, name: str) -> None:
        if name in self._starts:
            self.phases_ms[name] = round(
                (time.perf_counter() - self._starts[name]) * 1000, 2
            )

    def to_dict(self) -> dict[str, float]:
        return dict(self.phases_ms)


class StructuredLogger:
    """Production structured logger with sensitive-field redaction."""

    SENSITIVE_FIELDS = frozenset({
        "password", "api_key", "apikey", "token", "secret",
        "assetimage", "barcodeimage", "GEMINI_API_KEY", "DB_PASSWORD",
    })

    def __init__(self, request_id: str, logger_name: str = "asset_analysis"):
        self.request_id = request_id
        self.logger = logging.getLogger(logger_name)

    def _sanitize_data(self, data):
        if isinstance(data, dict):
            return {
                k: "***REDACTED***" if k.lower() in self.SENSITIVE_FIELDS
                else (self._sanitize_data(v) if isinstance(v, (dict, list)) else v)
                for k, v in data.items()
            }
        if isinstance(data, list):
            return [self._sanitize_data(item) for item in data]
        return data

    def _log(self, level: str, message: str, **kwargs) -> None:
        sanitized_kwargs = self._sanitize_data(kwargs)
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "request_id": self.request_id,
            "message": message,
            **sanitized_kwargs,
        }
        getattr(self.logger, level)(json.dumps(log_data))

    def info(self, message: str, **kwargs) -> None:
        self._log("info", message, **kwargs)

    def warning(self, message: str, **kwargs) -> None:
        self._log("warning", message, **kwargs)

    def error(self, message: str, **kwargs) -> None:
        self._log("error", message, **kwargs)

    def log_api_call(self, api_name: str, response_time_ms: float, success: bool = True, **kwargs) -> None:
        self.info(
            f"API call: {api_name}",
            api_name=api_name,
            response_time_ms=response_time_ms,
            success=success,
            **kwargs,
        )

    def log_request_complete(self, total_time_ms: float, status_code: int, **kwargs) -> None:
        self.info(
            "Request completed",
            total_time_ms=total_time_ms,
            status_code=status_code,
            **kwargs,
        )


def log_memory_usage(request_id: str, stage: str) -> None:
    if not MEMORY_MONITORING:
        return
    try:
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        logging.info(f"[{request_id}] Memory usage at {stage}: {memory_mb:.1f}MB")
        if memory_mb > 1200:
            logging.warning(
                f"[{request_id}] HIGH MEMORY USAGE: {memory_mb:.1f}MB - approaching limits"
            )
    except Exception as e:
        logging.debug(f"Memory monitoring failed: {e}")


def get_memory_header() -> str:
    if not MEMORY_MONITORING:
        return "unknown"
    try:
        memory_mb = psutil.Process().memory_info().rss / 1024 / 1024
        return f"{memory_mb:.1f}MB"
    except Exception:
        return "unavailable"
