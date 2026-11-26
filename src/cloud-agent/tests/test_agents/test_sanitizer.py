import base64

from agents.sanitizer import sanitize_payload


def test_sanitize_payload_string_length():
    long_str = "a" * 2005
    sanitized = sanitize_payload(long_str, max_string_len=2000)
    assert len(sanitized) == 2000 + len("... [TRUNCATED]")
    assert sanitized.endswith("... [TRUNCATED]")

def test_sanitize_payload_depth():
    nested = {"a": {"b": {"c": {"d": {"e": {"f": "too deep"}}}}}}
    sanitized = sanitize_payload(nested, max_depth=5)
    assert sanitized["a"]["b"]["c"]["d"]["e"] == "[MAX_DEPTH_REACHED]"

def test_sanitize_payload_circular():
    circular = []
    circular.append(circular)
    sanitized = sanitize_payload(circular)
    assert sanitized == ["[CIRCULAR]"]

def test_sanitize_payload_base64():
    raw = b"some random binary data that is somewhat long so it gets encoded to more than 100 characters in base64 format."
    b64 = base64.b64encode(raw).decode('utf-8')
    assert sanitize_payload(b64) == "[REDACTED_BASE64]"

def test_sanitize_payload_sensitive_keys():
    obj = {
        "password": "my_password",
        "api_key": "my_key",
        "API-KEY": "my_key",
        "authorization": "Bearer token",
        "normal_key": "value"
    }
    sanitized = sanitize_payload(obj)
    assert sanitized["password"] == "[REDACTED_SECRET]"
    assert sanitized["api_key"] == "[REDACTED_SECRET]"
    assert sanitized["API-KEY"] == "[REDACTED_SECRET]"
    assert sanitized["authorization"] == "[REDACTED_SECRET]"
    assert sanitized["normal_key"] == "value"


def test_sanitize_payload_redacts_inline_secrets_and_bearer_tokens():
    value = (
        '{"authorization":"SECRET_JSON","safe":"visible"} '
        "api_key=SECRET_KEY Authorization: Bearer SECRET_BEARER"
    )

    sanitized = sanitize_payload(value)

    assert "SECRET_JSON" not in sanitized
    assert "SECRET_KEY" not in sanitized
    assert "SECRET_BEARER" not in sanitized
    assert "visible" in sanitized

def test_sanitize_payload_list_limit():
    obj = list(range(100))
    sanitized = sanitize_payload(obj, max_list_items=50)
    assert len(sanitized) == 51
    assert sanitized[-1] == "... [50 items omitted]"

def test_sanitize_payload_dict_limit():
    obj = {f"k{i}": i for i in range(100)}
    sanitized = sanitize_payload(obj, max_dict_keys=50)
    assert len(sanitized) == 51
    assert sanitized["__truncated__"] == "50 keys omitted"
