"""HTTP error response helpers."""

import json
from datetime import datetime
from typing import Any

try:
    import azure.functions as func
except ImportError:  # FastAPI host — provide a minimal stub so imports succeed
    class _HttpResponse:
        def __init__(
            self,
            body: Any = None,
            *,
            mimetype: str | None = None,
            status_code: int = 200,
            headers: dict | None = None,
        ):
            self.body = body
            self.mimetype = mimetype
            self.status_code = status_code
            self.headers = headers or {}

    class _FuncModule:
        HttpResponse = _HttpResponse
        HttpRequest = object

    func = _FuncModule()  # type: ignore

from asset_validator.utils.helpers import to_db_safe_text


def create_error_response(
    message: str,
    status_code: int = 400,
    request_id: str | None = None,
    **extra_fields,
) -> func.HttpResponse:
    error_response = {
        "success": False,
        "error": {
            # Validation messages quote the offending value ("got: 'abc'"), so this text
            # needs the same apostrophe treatment as the success payload.
            "message": to_db_safe_text(str(message)),
            "code": status_code,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        },
    }
    if request_id:
        error_response["error"]["request_id"] = request_id
    error_response.update(extra_fields)
    return func.HttpResponse(
        json.dumps(error_response),
        mimetype="application/json",
        status_code=status_code,
        headers={
            "X-Request-ID": request_id or "unknown",
            "X-Error-Code": str(status_code),
        },
    )
