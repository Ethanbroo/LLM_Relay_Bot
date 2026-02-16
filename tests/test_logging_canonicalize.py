"""
Tests for audit_logging/canonicalize.py

Covers:
- Canonical JSON generation
- Float rejection
- Key sorting
- Hash computation
- Event ID generation
- Edge cases
"""

import pytest
from audit_logging.canonicalize import (
    canonical_json,
    compute_sha256_hex,
    compute_payload_hash,
    compute_event_hash,
    compute_event_id,
)


class TestCanonicalJson:
    """Tests for canonical_json function."""

    def test_empty_dict(self):
        """Empty dict produces {}"""
        result = canonical_json({})
        assert result == b'{}'

    def test_empty_list(self):
        """Empty list produces []"""
        result = canonical_json([])
        assert result == b'[]'

    def test_sorted_keys(self):
        """Keys are sorted alphabetically"""
        obj = {"z": 1, "a": 2, "m": 3}
        result = canonical_json(obj)
        assert result == b'{"a":2,"m":3,"z":1}'

    def test_no_whitespace(self):
        """No whitespace in output"""
        obj = {"key": "value", "nested": {"inner": "data"}}
        result = canonical_json(obj)
        assert b' ' not in result
        assert b'\n' not in result
        assert b'\t' not in result

    def test_nested_sorted_keys(self):
        """Nested dicts also have sorted keys"""
        obj = {"outer": {"z": 1, "a": 2}}
        result = canonical_json(obj)
        assert result == b'{"outer":{"a":2,"z":1}}'

    def test_unicode_preserved(self):
        """Unicode characters preserved (not escaped)"""
        obj = {"emoji": "🔒", "chinese": "中文"}
        result = canonical_json(obj)
        # Should contain actual UTF-8 bytes, not \u escapes
        assert "🔒".encode('utf-8') in result
        assert "中文".encode('utf-8') in result

    def test_float_rejected(self):
        """Floats raise TypeError"""
        obj = {"value": 3.14}
        with pytest.raises(TypeError, match="Floats not allowed"):
            canonical_json(obj)

    def test_nested_float_rejected(self):
        """Floats in nested structures rejected"""
        obj = {"outer": {"inner": [1, 2, 3.14]}}
        with pytest.raises(TypeError, match="Floats not allowed"):
            canonical_json(obj)

    def test_integers_allowed(self):
        """Integers are allowed"""
        obj = {"count": 42, "negative": -17}
        result = canonical_json(obj)
        assert result == b'{"count":42,"negative":-17}'

    def test_booleans_and_null(self):
        """Booleans and null handled correctly"""
        obj = {"is_true": True, "is_false": False, "is_null": None}
        result = canonical_json(obj)
        assert result == b'{"is_false":false,"is_null":null,"is_true":true}'

    def test_list_items(self):
        """Lists preserve order"""
        obj = {"items": [3, 1, 2]}
        result = canonical_json(obj)
        assert result == b'{"items":[3,1,2]}'

    def test_deterministic(self):
        """Same input produces same output"""
        obj = {"z": 26, "a": 1, "m": 13}
        result1 = canonical_json(obj)
        result2 = canonical_json(obj)
        assert result1 == result2


class TestSha256Hex:
    """Tests for compute_sha256_hex function."""

    def test_known_hash(self):
        """SHA-256 of 'hello' is correct"""
        data = b'hello'
        expected = '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
        assert compute_sha256_hex(data) == expected

    def test_empty_bytes(self):
        """SHA-256 of empty bytes"""
        data = b''
        expected = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
        assert compute_sha256_hex(data) == expected

    def test_lowercase_hex(self):
        """Output is lowercase hex"""
        data = b'test'
        result = compute_sha256_hex(data)
        assert result.islower()
        assert len(result) == 64
        assert all(c in '0123456789abcdef' for c in result)


class TestPayloadHash:
    """Tests for compute_payload_hash function."""

    def test_empty_payload(self):
        """Empty payload produces deterministic hash"""
        payload = {}
        hash1 = compute_payload_hash(payload)
        hash2 = compute_payload_hash(payload)
        assert hash1 == hash2
        assert len(hash1) == 64

    def test_simple_payload(self):
        """Simple payload hashes correctly"""
        payload = {"key": "value"}
        result = compute_payload_hash(payload)
        assert len(result) == 64

    def test_key_order_independent(self):
        """Key order doesn't affect hash"""
        payload1 = {"z": 1, "a": 2}
        payload2 = {"a": 2, "z": 1}
        assert compute_payload_hash(payload1) == compute_payload_hash(payload2)

    def test_different_payloads_different_hashes(self):
        """Different payloads produce different hashes"""
        payload1 = {"key": "value1"}
        payload2 = {"key": "value2"}
        assert compute_payload_hash(payload1) != compute_payload_hash(payload2)


class TestEventHash:
    """Tests for compute_event_hash function."""

    def test_excludes_event_hash_field(self):
        """event_hash field is excluded from hash computation"""
        event1 = {
            "schema_id": "relay.audit_event",
            "run_id": "123",
            "event_seq": 1,
            "event_hash": "old_hash"
        }
        event2 = {
            "schema_id": "relay.audit_event",
            "run_id": "123",
            "event_seq": 1,
            "event_hash": "new_hash"
        }
        # Should produce same hash (event_hash excluded)
        assert compute_event_hash(event1) == compute_event_hash(event2)

    def test_excludes_signature_field(self):
        """signature field is excluded from hash computation"""
        event1 = {
            "schema_id": "relay.audit_event",
            "run_id": "123",
            "signature": "sig1"
        }
        event2 = {
            "schema_id": "relay.audit_event",
            "run_id": "123",
            "signature": "sig2"
        }
        assert compute_event_hash(event1) == compute_event_hash(event2)

    def test_includes_other_fields(self):
        """Other fields affect the hash"""
        event1 = {"schema_id": "relay.audit_event", "run_id": "123"}
        event2 = {"schema_id": "relay.audit_event", "run_id": "456"}
        assert compute_event_hash(event1) != compute_event_hash(event2)

    def test_deterministic(self):
        """Same event produces same hash"""
        event = {
            "schema_id": "relay.audit_event",
            "run_id": "abc",
            "event_seq": 1,
            "payload": {"data": "test"}
        }
        hash1 = compute_event_hash(event)
        hash2 = compute_event_hash(event)
        assert hash1 == hash2


class TestEventId:
    """Tests for compute_event_id function."""

    def test_deterministic(self):
        """Same inputs produce same event_id"""
        event_id1 = compute_event_id(
            run_id="run123",
            event_seq=1,
            event_type="RUN_STARTED",
            actor="supervisor",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload_hash="a" * 64
        )
        event_id2 = compute_event_id(
            run_id="run123",
            event_seq=1,
            event_type="RUN_STARTED",
            actor="supervisor",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload_hash="a" * 64
        )
        assert event_id1 == event_id2

    def test_different_run_id_different_id(self):
        """Different run_id produces different event_id"""
        id1 = compute_event_id(
            run_id="run1",
            event_seq=1,
            event_type="RUN_STARTED",
            actor="supervisor",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload_hash="a" * 64
        )
        id2 = compute_event_id(
            run_id="run2",
            event_seq=1,
            event_type="RUN_STARTED",
            actor="supervisor",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload_hash="a" * 64
        )
        assert id1 != id2

    def test_different_event_seq_different_id(self):
        """Different event_seq produces different event_id"""
        id1 = compute_event_id(
            run_id="run123",
            event_seq=1,
            event_type="RUN_STARTED",
            actor="supervisor",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload_hash="a" * 64
        )
        id2 = compute_event_id(
            run_id="run123",
            event_seq=2,
            event_type="RUN_STARTED",
            actor="supervisor",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload_hash="a" * 64
        )
        assert id1 != id2

    def test_correlation_key_order_independent(self):
        """Correlation dict key order doesn't affect event_id"""
        id1 = compute_event_id(
            run_id="run123",
            event_seq=1,
            event_type="RUN_STARTED",
            actor="supervisor",
            correlation={"session_id": "s1", "task_id": "t1", "message_id": "m1"},
            payload_hash="a" * 64
        )
        id2 = compute_event_id(
            run_id="run123",
            event_seq=1,
            event_type="RUN_STARTED",
            actor="supervisor",
            correlation={"task_id": "t1", "message_id": "m1", "session_id": "s1"},
            payload_hash="a" * 64
        )
        assert id1 == id2

    def test_returns_64_char_hex(self):
        """Event ID is 64-character hex string"""
        event_id = compute_event_id(
            run_id="run123",
            event_seq=1,
            event_type="RUN_STARTED",
            actor="supervisor",
            correlation={"session_id": None, "message_id": None, "task_id": None},
            payload_hash="a" * 64
        )
        assert len(event_id) == 64
        assert all(c in '0123456789abcdef' for c in event_id)
