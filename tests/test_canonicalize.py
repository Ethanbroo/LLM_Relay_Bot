"""Tests for canonicalization utilities."""

import pytest
from validator.canonicalize import (
    canonicalize_json,
    compute_sha256,
    compute_schema_hash,
    canonicalize_envelope,
)


def test_canonicalize_json_deterministic():
    """Test that canonicalization is deterministic."""
    obj1 = {"b": 2, "a": 1, "c": 3}
    obj2 = {"a": 1, "c": 3, "b": 2}
    obj3 = {"c": 3, "b": 2, "a": 1}

    result1 = canonicalize_json(obj1)
    result2 = canonicalize_json(obj2)
    result3 = canonicalize_json(obj3)

    assert result1 == result2 == result3
    assert result1 == '{"a":1,"b":2,"c":3}'


def test_canonicalize_json_no_whitespace():
    """Test that canonical JSON has no whitespace."""
    obj = {"key": "value", "nested": {"a": 1}}
    result = canonicalize_json(obj)

    assert ' ' not in result
    assert '\n' not in result
    assert '\t' not in result


def test_canonicalize_json_nested():
    """Test canonicalization of nested objects."""
    obj = {
        "outer": {
            "z": 3,
            "a": 1,
            "m": 2
        }
    }

    result = canonicalize_json(obj)
    assert result == '{"outer":{"a":1,"m":2,"z":3}}'


def test_canonicalize_json_array():
    """Test that arrays maintain order (not sorted)."""
    obj = {"arr": [3, 1, 2]}
    result = canonicalize_json(obj)

    assert result == '{"arr":[3,1,2]}'


def test_canonicalize_json_unicode():
    """Test Unicode handling."""
    obj = {"emoji": "🚀", "chinese": "你好"}
    result = canonicalize_json(obj)

    # Should preserve Unicode (not ASCII-escape)
    assert "🚀" in result
    assert "你好" in result


def test_canonicalize_json_rejects_nan():
    """Test that NaN is rejected."""
    with pytest.raises(ValueError):
        canonicalize_json({"value": float('nan')})


def test_compute_sha256_string():
    """Test SHA-256 of string."""
    result = compute_sha256("hello")
    assert len(result) == 64
    assert result == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_compute_sha256_bytes():
    """Test SHA-256 of bytes."""
    result = compute_sha256(b"hello")
    assert len(result) == 64
    assert result == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_compute_sha256_deterministic():
    """Test that hash is deterministic."""
    result1 = compute_sha256("test")
    result2 = compute_sha256("test")
    assert result1 == result2


def test_compute_schema_hash():
    """Test schema hash computation."""
    schema = {
        "type": "object",
        "properties": {
            "b": {"type": "string"},
            "a": {"type": "number"}
        }
    }

    hash1 = compute_schema_hash(schema)
    assert len(hash1) == 64

    # Reorder and compute again - should be same
    schema2 = {
        "properties": {
            "a": {"type": "number"},
            "b": {"type": "string"}
        },
        "type": "object"
    }

    hash2 = compute_schema_hash(schema2)
    assert hash1 == hash2


def test_canonicalize_envelope():
    """Test envelope canonicalization produces JSONL."""
    envelope = {"message_id": "123", "action": "test"}
    result = canonicalize_envelope(envelope)

    assert result.endswith('\n')
    assert result == '{"action":"test","message_id":"123"}\n'
