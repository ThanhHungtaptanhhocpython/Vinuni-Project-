import base64
import re
from typing import Any

# Regex to heuristically detect base64 strings of sufficient length
_BASE64_PATTERN = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_INLINE_SECRET_PATTERN = re.compile(
    r"(?P<prefix>[\"']?(?:password|secret|token|api[_-]?key|authorization|"
    r"access_token|refresh_token|id_token|client_secret|passwd|"
    r"proxy-authorization|cookie|set-cookie)[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"']?)(?P<value>[^\"'\s,}]+)(?P=quote)",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


def _sanitize_string(value: str, max_string_len: int) -> str:
    value = _BEARER_PATTERN.sub("Bearer [REDACTED_SECRET]", value)
    value = _INLINE_SECRET_PATTERN.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('quote')}"
            f"[REDACTED_SECRET]{match.group('quote')}"
        ),
        value,
    )
    if len(value) > max_string_len:
        return value[:max_string_len] + "... [TRUNCATED]"
    return value

def is_base64_blob(value: str) -> bool:
    if len(value) < 100:
        return False
    # Check if it looks like base64
    if not _BASE64_PATTERN.match(value):
        return False
    try:
        # Check if it decodes cleanly
        base64.b64decode(value, validate=True)
        return True
    except Exception:
        return False

def sanitize_payload(
    obj: Any,
    depth: int = 0,
    max_depth: int = 10,
    max_list_items: int = 50,
    max_dict_keys: int = 50,
    max_string_len: int = 2000,
    _seen: set | None = None
) -> Any:
    """
    Sanitize an object before logging or storing it in the trace repository.
    - Limits recursion depth
    - Limits list/dict sizes
    - Truncates long strings
    - Redacts base64 blobs
    - Redacts sensitive keys
    - Detects circular references
    """
    if _seen is None:
        _seen = set()

    obj_id = id(obj)
    if obj_id in _seen:
        return "[CIRCULAR]"

    if depth >= max_depth:
        return "[MAX_DEPTH_REACHED]"

    if isinstance(obj, str):
        # Redact secrets (naive check if the key is known, but here we only have the value)
        # Wait, key redaction happens in the dict handling.
        if is_base64_blob(obj):
            return "[REDACTED_BASE64]"
        return _sanitize_string(obj, max_string_len)

    if isinstance(obj, int | float | bool | type(None)):
        return obj

    _seen.add(obj_id)

    try:
        if isinstance(obj, dict):
            sanitized_dict = {}
            keys = list(obj.keys())
            for i, k in enumerate(keys):
                if i >= max_dict_keys:
                    sanitized_dict["__truncated__"] = f"{len(keys) - max_dict_keys} keys omitted"
                    break

                k_str = str(k)
                if re.match(r"^(password|secret|token|api[_-]?key|authorization|access_token|refresh_token|id_token|client_secret|passwd|proxy-authorization|cookie|set-cookie)$", k_str, re.IGNORECASE):
                    sanitized_dict[k] = "[REDACTED_SECRET]"
                else:
                    sanitized_dict[k] = sanitize_payload(
                        obj[k], depth + 1, max_depth, max_list_items, max_dict_keys, max_string_len, _seen
                    )
            return sanitized_dict

        if isinstance(obj, list | tuple | set):
            sanitized_list = []
            obj_list = list(obj)
            for i, item in enumerate(obj_list):
                if i >= max_list_items:
                    sanitized_list.append(f"... [{len(obj_list) - max_list_items} items omitted]")
                    break
                sanitized_list.append(
                    sanitize_payload(item, depth + 1, max_depth, max_list_items, max_dict_keys, max_string_len, _seen)
                )
            if isinstance(obj, tuple):
                return tuple(sanitized_list)
            return sanitized_list

        # Fallback for objects (like Pydantic models or datacenters)
        if hasattr(obj, "model_dump"):
            return sanitize_payload(
                obj.model_dump(), depth + 1, max_depth, max_list_items, max_dict_keys, max_string_len, _seen
            )
        elif hasattr(obj, "__dict__"):
            return sanitize_payload(
                obj.__dict__, depth + 1, max_depth, max_list_items, max_dict_keys, max_string_len, _seen
            )

        # Convert unknown objects to string
        res = str(obj)
        if len(res) > max_string_len:
            return res[:max_string_len] + "... [TRUNCATED]"
        return res
    finally:
        _seen.remove(obj_id)
