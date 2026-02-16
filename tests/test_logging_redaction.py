"""
Tests for audit_logging/redaction.py

Covers:
- Field name pattern detection
- Bearer token detection
- Long base64 detection
- Nested structure handling
- Adversarial secret injection
- Edge cases
"""

import pytest
from audit_logging.redaction import (
    redact,
    check_no_secrets,
    create_redaction_metadata,
)


class TestFieldNamePatterns:
    """Tests for secret field name detection."""

    def test_redact_password_field(self):
        """Redact field named 'password'"""
        obj = {"password": "secret123"}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"password": "REDACTED"}
        assert paths == ["/password"]

    def test_redact_api_key_field(self):
        """Redact field named 'api_key'"""
        obj = {"api_key": "abc123"}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"api_key": "REDACTED"}
        assert paths == ["/api_key"]

    def test_redact_authorization_field(self):
        """Redact field named 'authorization'"""
        obj = {"authorization": "Bearer token"}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"authorization": "REDACTED"}
        assert paths == ["/authorization"]

    def test_redact_token_field(self):
        """Redact field named 'token'"""
        obj = {"token": "xyz789"}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"token": "REDACTED"}
        assert paths == ["/token"]

    def test_redact_secret_field(self):
        """Redact field named 'secret'"""
        obj = {"secret": "hidden"}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"secret": "REDACTED"}
        assert paths == ["/secret"]

    def test_redact_private_key_field(self):
        """Redact field named 'private_key'"""
        obj = {"private_key": "-----BEGIN PRIVATE KEY-----"}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"private_key": "REDACTED"}
        assert paths == ["/private_key"]

    def test_redact_cookie_field(self):
        """Redact field named 'cookie'"""
        obj = {"cookie": "session=abc123"}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"cookie": "REDACTED"}
        assert paths == ["/cookie"]

    def test_case_insensitive_password(self):
        """Redact field named 'PASSWORD' (uppercase)"""
        obj = {"PASSWORD": "secret"}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"PASSWORD": "REDACTED"}
        assert paths == ["/PASSWORD"]

    def test_case_insensitive_api_key(self):
        """Redact field named 'API_KEY' (uppercase)"""
        obj = {"API_KEY": "key123"}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"API_KEY": "REDACTED"}
        assert paths == ["/API_KEY"]

    def test_mixed_case_authorization(self):
        """Redact field named 'Authorization' (mixed case)"""
        obj = {"Authorization": "Bearer token"}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"Authorization": "REDACTED"}
        assert paths == ["/Authorization"]

    def test_credential_field(self):
        """Redact field named 'credential'"""
        obj = {"credential": "user:pass"}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"credential": "REDACTED"}
        assert paths == ["/credential"]


class TestBearerTokens:
    """Tests for Bearer token detection in string values."""

    def test_redact_bearer_token_in_string(self):
        """Redact string containing Bearer token"""
        obj = {"header": "Bearer abc123def456"}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"header": "REDACTED"}
        assert paths == ["/header"]

    def test_redact_bearer_lowercase(self):
        """Redact string with lowercase 'bearer'"""
        obj = {"auth": "bearer xyz789"}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"auth": "REDACTED"}
        assert paths == ["/auth"]

    def test_redact_bearer_uppercase(self):
        """Redact string with uppercase 'BEARER'"""
        obj = {"auth": "BEARER token123"}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"auth": "REDACTED"}
        assert paths == ["/auth"]

    def test_no_redaction_without_bearer(self):
        """Don't redact normal strings without Bearer"""
        obj = {"message": "Hello world"}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"message": "Hello world"}
        assert paths == []


class TestLongBase64:
    """Tests for long base64 string detection."""

    def test_redact_long_base64_string(self):
        """Redact string with >80 chars of base64"""
        long_b64 = "A" * 85 + "=="
        obj = {"data": long_b64}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"data": "REDACTED"}
        assert paths == ["/data"]

    def test_short_base64_not_redacted(self):
        """Don't redact short base64 strings (<80 chars)"""
        short_b64 = "A" * 50
        obj = {"data": short_b64}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"data": short_b64}
        assert paths == []

    def test_redact_exactly_80_chars(self):
        """Redact base64 string with exactly 80 chars"""
        b64_80 = "A" * 80
        obj = {"data": b64_80}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"data": "REDACTED"}
        assert paths == ["/data"]


class TestNestedStructures:
    """Tests for nested dict/list handling."""

    def test_redact_nested_dict(self):
        """Redact secrets in nested dict"""
        obj = {
            "user": "alice",
            "auth": {
                "password": "secret123"
            }
        }
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {
            "user": "alice",
            "auth": {
                "password": "REDACTED"
            }
        }
        assert paths == ["/auth/password"]

    def test_redact_deeply_nested(self):
        """Redact secrets in deeply nested structure"""
        obj = {
            "level1": {
                "level2": {
                    "level3": {
                        "api_key": "secret"
                    }
                }
            }
        }
        redacted_obj, paths = redact(obj)

        assert redacted_obj["level1"]["level2"]["level3"]["api_key"] == "REDACTED"
        assert paths == ["/level1/level2/level3/api_key"]

    def test_redact_in_list(self):
        """Redact secrets in list items"""
        obj = {
            "items": [
                {"password": "secret1"},
                {"password": "secret2"}
            ]
        }
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {
            "items": [
                {"password": "REDACTED"},
                {"password": "REDACTED"}
            ]
        }
        assert paths == ["/items/0/password", "/items/1/password"]

    def test_multiple_secrets_same_level(self):
        """Redact multiple secrets at same level"""
        obj = {
            "password": "secret1",
            "api_key": "secret2",
            "token": "secret3"
        }
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {
            "password": "REDACTED",
            "api_key": "REDACTED",
            "token": "REDACTED"
        }
        assert set(paths) == {"/password", "/api_key", "/token"}


class TestAdversarialInjection:
    """Adversarial tests for secret injection attempts."""

    def test_secret_in_nested_payload(self):
        """Attacker injects secret in nested structure"""
        obj = {
            "action": "fs.read",
            "payload": {
                "path": "file.txt",
                "metadata": {
                    "Authorization": "Bearer attacker_token"
                }
            }
        }
        redacted_obj, paths = redact(obj)

        assert redacted_obj["payload"]["metadata"]["Authorization"] == "REDACTED"
        assert "/payload/metadata/Authorization" in paths

    def test_secret_in_list_payload(self):
        """Attacker injects secret in list"""
        obj = {
            "headers": [
                {"name": "Content-Type", "value": "application/json"},
                {"name": "Authorization", "value": "Bearer secret"}
            ]
        }
        redacted_obj, paths = redact(obj)

        assert redacted_obj["headers"][1]["value"] == "REDACTED"
        assert "/headers/1/value" in paths

    def test_disguised_api_key_field(self):
        """Attacker uses 'apiKey' instead of 'api_key'"""
        obj = {"apiKey": "secret123"}
        redacted_obj, paths = redact(obj)

        # Should still be caught (regex matches api*key)
        assert redacted_obj == {"apiKey": "REDACTED"}
        assert paths == ["/apiKey"]

    def test_bearer_in_error_message(self):
        """Attacker includes Bearer token in error message"""
        obj = {
            "error": "Authentication failed with Bearer abc123"
        }
        redacted_obj, paths = redact(obj)

        assert redacted_obj["error"] == "REDACTED"
        assert paths == ["/error"]

    def test_long_jwt_in_field(self):
        """Attacker includes long JWT (base64) in regular field"""
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." + "A" * 80
        obj = {"debug_info": jwt}
        redacted_obj, paths = redact(obj)

        assert redacted_obj["debug_info"] == "REDACTED"
        assert paths == ["/debug_info"]


class TestEdgeCases:
    """Tests for edge cases and special values."""

    def test_empty_dict(self):
        """Redact empty dict"""
        obj = {}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {}
        assert paths == []

    def test_empty_list(self):
        """Redact empty list"""
        obj = []
        redacted_obj, paths = redact(obj)

        assert redacted_obj == []
        assert paths == []

    def test_null_value(self):
        """Redact None/null value"""
        obj = {"password": None}
        redacted_obj, paths = redact(obj)

        # Field name matches, so redact even if value is None
        assert redacted_obj == {"password": "REDACTED"}
        assert paths == ["/password"]

    def test_integer_value(self):
        """Don't redact integer values"""
        obj = {"count": 42}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"count": 42}
        assert paths == []

    def test_boolean_value(self):
        """Don't redact boolean values"""
        obj = {"is_admin": True}
        redacted_obj, paths = redact(obj)

        assert redacted_obj == {"is_admin": True}
        assert paths == []

    def test_safe_fields_not_redacted(self):
        """Don't redact safe field names"""
        obj = {
            "username": "alice",
            "action": "fs.read",
            "timestamp": "2026-01-01T00:00:00Z"
        }
        redacted_obj, paths = redact(obj)

        assert redacted_obj == obj
        assert paths == []


class TestCheckNoSecrets:
    """Tests for check_no_secrets validation."""

    def test_check_no_secrets_passes_safe_object(self):
        """check_no_secrets passes for object without secrets"""
        obj = {"username": "alice", "action": "fs.read"}
        check_no_secrets(obj)  # Should not raise

    def test_check_no_secrets_fails_with_password(self):
        """check_no_secrets raises for object with password field"""
        obj = {"password": "secret123"}

        with pytest.raises(ValueError, match="Secrets still present"):
            check_no_secrets(obj)

    def test_check_no_secrets_fails_with_bearer(self):
        """check_no_secrets raises for object with Bearer token"""
        obj = {"auth": "Bearer token123"}

        with pytest.raises(ValueError, match="Secrets still present"):
            check_no_secrets(obj)


class TestRedactionMetadata:
    """Tests for create_redaction_metadata function."""

    def test_create_metadata_with_redaction(self):
        """Create metadata when secrets were redacted"""
        metadata = create_redaction_metadata(True, ["/password", "/api_key"])

        assert metadata == {
            "was_redacted": True,
            "redacted_paths": ["/password", "/api_key"]
        }

    def test_create_metadata_without_redaction(self):
        """Create metadata when no secrets were redacted"""
        metadata = create_redaction_metadata(False, [])

        assert metadata == {
            "was_redacted": False,
            "redacted_paths": []
        }
