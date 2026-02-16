"""Comprehensive tests for sanitization module."""

import pytest
from validator.sanitize import sanitize_payload, SanitizationError, _sanitize_string, _sanitize_dict


def test_sanitize_unknown_action_uses_generic():
    """Test that unknown actions use generic sanitization."""
    payload = {
        "field": "value",
        "nested": {"key": "data"}
    }

    result = sanitize_payload(payload, "unknown.action")

    assert result["field"] == "value"
    assert result["nested"]["key"] == "data"


def test_sanitize_dict_recursive():
    """Test that _sanitize_dict handles nested structures."""
    obj = {
        "string": "café",
        "number": 42,
        "nested": {
            "another_string": "test"
        },
        "list": ["item1", "item2"]
    }

    result = _sanitize_dict(obj)

    assert result["string"] == "café"
    assert result["number"] == 42
    assert result["nested"]["another_string"] == "test"
    assert result["list"] == ["item1", "item2"]


def test_sanitize_dict_list_of_dicts():
    """Test sanitization of list containing dicts."""
    obj = [
        {"key": "value1"},
        {"key": "value2"}
    ]

    result = _sanitize_dict(obj)

    assert len(result) == 2
    assert result[0]["key"] == "value1"


def test_sanitize_dict_preserves_primitives():
    """Test that sanitization preserves primitive types."""
    obj = {
        "string": "test",
        "int": 123,
        "float": 45.67,
        "bool": True,
        "none": None
    }

    result = _sanitize_dict(obj)

    assert result["string"] == "test"
    assert result["int"] == 123
    assert result["float"] == 45.67
    assert result["bool"] is True
    assert result["none"] is None


def test_sanitize_string_allows_common_whitespace():
    """Test that common whitespace is allowed."""
    # Space, tab, newline, carriage return are allowed
    text = "line1\nline2\r\nline3\tindented"

    result = _sanitize_string(text)

    assert "\n" in result
    assert "\t" in result


def test_sanitize_string_rejects_control_chars():
    """Test that control characters are rejected."""
    # ASCII control characters (except allowed whitespace)
    for code in range(32):
        char = chr(code)
        if char not in [' ', '\t', '\n', '\r']:
            with pytest.raises(SanitizationError) as exc_info:
                _sanitize_string(f"test{char}string")

            # Null byte has specific message, others get "control character"
            if char == '\x00':
                assert "null byte" in str(exc_info.value)
            else:
                assert "control character" in str(exc_info.value)


def test_sanitize_fs_read_with_all_defaults():
    """Test fs.read sanitization fills in all defaults."""
    payload = {
        "path": "minimal.txt"
    }

    result = sanitize_payload(payload, "fs.read")

    assert result["path"] == "minimal.txt"
    assert result["offset"] == 0
    assert result["length"] == 1048576
    assert result["encoding"] == "utf-8"


def test_sanitize_fs_list_dir_with_all_defaults():
    """Test fs.list_dir sanitization fills in all defaults."""
    payload = {
        "path": "dir"
    }

    result = sanitize_payload(payload, "fs.list_dir")

    assert result["path"] == "dir"
    assert result["max_entries"] == 100
    assert result["sort_order"] == "name_asc"
    assert result["include_hidden"] is False
    assert result["recursive"] is False


def test_sanitize_fs_read_with_explicit_values():
    """Test fs.read sanitization preserves explicit values."""
    payload = {
        "path": "file.txt",
        "offset": 100,
        "length": 500,
        "encoding": "ascii"
    }

    result = sanitize_payload(payload, "fs.read")

    assert result["offset"] == 100
    assert result["length"] == 500
    assert result["encoding"] == "ascii"


def test_sanitize_fs_list_dir_with_explicit_values():
    """Test fs.list_dir sanitization preserves explicit values."""
    payload = {
        "path": "dir",
        "max_entries": 50,
        "sort_order": "mtime_desc",
        "include_hidden": True,
        "recursive": True
    }

    result = sanitize_payload(payload, "fs.list_dir")

    assert result["max_entries"] == 50
    assert result["sort_order"] == "mtime_desc"
    assert result["include_hidden"] is True
    assert result["recursive"] is True


def test_sanitize_health_ping_none_echo():
    """Test health_ping with None echo value."""
    payload = {
        "echo": None
    }

    result = sanitize_payload(payload, "system.health_ping")

    # Should not have echo key since it's None
    assert result == {}


def test_sanitize_string_unicode_edge_cases():
    """Test Unicode normalization edge cases."""
    import unicodedata

    # Test various Unicode forms
    # Combining characters
    text = "e\u0301"  # e + combining acute accent
    result = _sanitize_string(text)
    assert unicodedata.is_normalized('NFC', result)

    # Emoji
    text = "test 🚀 emoji"
    result = _sanitize_string(text)
    assert unicodedata.is_normalized('NFC', result)
    assert "🚀" in result


def test_sanitize_empty_string():
    """Test sanitization of empty string."""
    result = _sanitize_string("")
    assert result == ""


def test_sanitize_whitespace_only():
    """Test sanitization of whitespace-only string."""
    result = _sanitize_string("   \t\n   ")
    assert result == "   \t\n   "


def test_sanitize_very_long_string():
    """Test sanitization doesn't corrupt long strings."""
    long_string = "a" * 10000
    result = _sanitize_string(long_string)
    assert len(result) == 10000
    assert result == long_string


def test_sanitize_fs_read_double_dot_detection():
    """Test that .. in path is detected during sanitization."""
    # This should have been caught by Pydantic, but sanitization double-checks
    payload = {
        "path": "dir/../file.txt"  # Contains ..
    }

    with pytest.raises(SanitizationError, match="\\.\\."):
        sanitize_payload(payload, "fs.read")


def test_sanitize_fs_list_dir_double_dot_detection():
    """Test .. detection in fs.list_dir."""
    payload = {
        "path": "../dir"
    }

    with pytest.raises(SanitizationError, match="\\.\\."):
        sanitize_payload(payload, "fs.list_dir")


def test_sanitize_complex_nested_structure():
    """Test sanitization of complex nested structure."""
    payload = {
        "level1": {
            "level2": {
                "level3": {
                    "strings": ["café", "naïve"],
                    "number": 123
                }
            },
            "list": [
                {"key": "value1"},
                {"key": "value2"}
            ]
        }
    }

    result = sanitize_payload(payload, "custom.action")

    assert result["level1"]["level2"]["level3"]["strings"][0] == "café"
    assert result["level1"]["level2"]["level3"]["number"] == 123
    assert result["level1"]["list"][1]["key"] == "value2"
