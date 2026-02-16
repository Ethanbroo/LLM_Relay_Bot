"""Tests for sanitization module."""

import pytest
from validator.sanitize import sanitize_payload, SanitizationError


def test_sanitize_fs_read_valid():
    """Test sanitization of valid fs.read payload."""
    payload = {
        "path": "test.txt",
        "offset": 0,
        "length": 1024,
        "encoding": "utf-8"
    }

    result = sanitize_payload(payload, "fs.read")

    assert result["path"] == "test.txt"
    assert result["offset"] == 0
    assert result["length"] == 1024
    assert result["encoding"] == "utf-8"


def test_sanitize_unicode_normalization():
    """Test that Unicode is normalized to NFC."""
    # Decomposed form (NFD) should be normalized to composed (NFC)
    payload = {
        "path": "café.txt",  # May be in different Unicode forms
        "offset": 0
    }

    result = sanitize_payload(payload, "fs.read")

    # Should be NFC normalized
    import unicodedata
    assert unicodedata.is_normalized('NFC', result["path"])


def test_reject_parent_traversal_in_sanitization():
    """Test that sanitization rejects .. even if it somehow got through."""
    # This should never happen (Pydantic should catch it),
    # but sanitization is a defense-in-depth layer
    payload = {
        "path": "../etc/passwd"
    }

    with pytest.raises(SanitizationError, match="\\.\\."):
        sanitize_payload(payload, "fs.read")


def test_reject_absolute_path_in_sanitization():
    """Test that sanitization rejects absolute paths."""
    payload = {
        "path": "/etc/passwd"
    }

    with pytest.raises(SanitizationError, match="absolute"):
        sanitize_payload(payload, "fs.read")


def test_reject_null_byte():
    """Test that null bytes are rejected."""
    payload = {
        "path": "test\x00.txt"
    }

    with pytest.raises(SanitizationError, match="null byte"):
        sanitize_payload(payload, "fs.read")


def test_sanitize_fs_list_dir():
    """Test sanitization of fs.list_dir payload."""
    payload = {
        "path": "subdir",
        "max_entries": 50,
        "sort_order": "name_asc",
        "include_hidden": False,
        "recursive": False
    }

    result = sanitize_payload(payload, "fs.list_dir")

    assert result["path"] == "subdir"
    assert result["max_entries"] == 50


def test_sanitize_health_ping_with_echo():
    """Test sanitization of health_ping with echo."""
    payload = {
        "echo": "hello world"
    }

    result = sanitize_payload(payload, "system.health_ping")

    assert result["echo"] == "hello world"


def test_sanitize_health_ping_empty():
    """Test sanitization of empty health_ping payload."""
    payload = {}

    result = sanitize_payload(payload, "system.health_ping")

    assert result == {}


def test_sanitize_preserves_defaults():
    """Test that sanitization includes default values."""
    payload = {
        "path": "test.txt"  # Only required field
    }

    result = sanitize_payload(payload, "fs.read")

    # Should have defaults
    assert "offset" in result
    assert "length" in result
    assert "encoding" in result
