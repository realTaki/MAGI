"""Unit tests for :class:`SecretRedactor`."""

from __future__ import annotations

from magi.bus.hooks.redaction import FieldClassification, SecretRedactor


def test_redact_known_secret_field_by_name():
    """``api_key`` (a known secret field name) is redacted."""
    out = SecretRedactor.redact({"api_key": "sk-1234", "name": "alice"})
    assert "api_key" in out
    assert "sk-1234" not in str(out)
    assert out["api_key"]["present"] is True
    assert out["api_key"]["type"] == "str"
    assert out["api_key"]["length"] == 7
    assert isinstance(out["api_key"]["content_hash"], str)
    assert out["name"] == "alice"


def test_redact_via_field_classification():
    out = SecretRedactor.redact(
        {"foo": "bar"},
        field_classifications={
            "foo": FieldClassification(classification="credential", mode="metadata-only"),
        },
    )
    assert "bar" not in str(out)
    assert out["foo"]["present"] is True


def test_redact_hashed_mode():
    out = SecretRedactor.redact(
        {"x": "secret-value"},
        field_classifications={
            "x": FieldClassification(classification="secret", mode="hashed"),
        },
    )
    assert "x" in out
    assert isinstance(out["x"]["sha256"], str)
    assert len(out["x"]["sha256"]) == 64


def test_redact_redacted_mode():
    out = SecretRedactor.redact(
        {"x": "secret-value"},
        field_classifications={
            "x": FieldClassification(classification="secret", mode="redacted"),
        },
    )
    assert out["x"] == "***REDACTED***"


def test_preserve_non_secret():
    out = SecretRedactor.redact(
        {"text": "hello world", "count": 5},
    )
    assert out == {"text": "hello world", "count": 5}


def test_redact_nested():
    out = SecretRedactor.redact({
        "outer": {
            "inner": {
                "api_key": "sk-9999",
                "visible": "ok",
            }
        }
    })
    assert "sk-9999" not in str(out)
    assert out["outer"]["inner"]["visible"] == "ok"


def test_redact_in_list():
    out = SecretRedactor.redact({
        "items": [
            {"api_key": "k1", "name": "x"},
            {"api_key": "k2", "name": "y"},
        ]
    })
    assert "k1" not in str(out)
    assert "k2" not in str(out)
    assert out["items"][1]["name"] == "y"


def test_redact_ignores_non_string_secret_types():
    out = SecretRedactor.redact({"api_key": 12345})
    assert out["api_key"]["type"] == "int"
    assert out["api_key"]["length"] == 1


def test_redact_handles_none_gracefully():
    out = SecretRedactor.redact(None)
    assert out is None


def test_redact_handles_binary_in_list():
    """Non-string values inside lists don't crash the redactor."""
    out = SecretRedactor.redact({"items": [b"\x00\x01", "text"]})
    assert "items" in out


def test_redact_preserves_public_label():
    """A field explicitly classified as PUBLIC keeps its value."""
    out = SecretRedactor.redact(
        {"x": "public-value"},
        field_classifications={
            "x": FieldClassification(classification="public", mode="preserve"),
        },
    )
    assert out["x"] == "public-value"


def test_redact_known_secret_field_case_insensitive():
    out = SecretRedactor.redact({"Authorization": "Bearer xyz"})
    assert "Authorization" in out
    assert "Bearer xyz" not in str(out)
