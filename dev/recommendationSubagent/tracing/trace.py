import hashlib, json
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, allow_nan=False).encode()
    ).hexdigest()


def redact(value):
    if isinstance(value, dict):
        return {
            k: (
                "[REDACTED]"
                if any(
                    s in k.lower()
                    for s in ("password", "api_key", "authorization", "secret")
                )
                else redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value
