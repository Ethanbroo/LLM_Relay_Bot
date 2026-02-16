"""Tests for Pydantic models and custom validators."""

import pytest
from pydantic import ValidationError
from validator.pydantic_models.envelope import Envelope
from validator.pydantic_models.fs_read import FsReadAction
from validator.pydantic_models.fs_list_dir import FsListDirAction
from validator.pydantic_models.system_health_ping import SystemHealthPingAction


class TestEnvelopeModel:
    """Tests for Envelope model."""

    def test_envelope_valid_uuid_v7(self):
        """Test envelope accepts valid UUID v7."""
        envelope = Envelope(
            envelope_version="1.0.0",
            message_id="01234567-89ab-7def-8123-456789abcdef",
            timestamp="2026-02-07T12:00:00Z",
            sender="validator",
            recipient="executor",
            action="fs.read",
            action_version="1.0.0",
            payload={}
        )

        assert envelope.message_id == "01234567-89ab-7def-8123-456789abcdef"

    def test_envelope_invalid_uuid_format(self):
        """Test envelope rejects invalid UUID format."""
        with pytest.raises(ValidationError, match="Invalid UUID"):
            Envelope(
                envelope_version="1.0.0",
                message_id="not-a-uuid",
                timestamp="2026-02-07T12:00:00Z",
                sender="validator",
                recipient="executor",
                action="fs.read",
                action_version="1.0.0",
                payload={}
            )

    def test_envelope_uuid_wrong_version(self):
        """Test envelope rejects UUID with wrong version (not v7)."""
        # UUID v4 (random, not time-ordered)
        with pytest.raises(ValidationError, match="UUID v7"):
            Envelope(
                envelope_version="1.0.0",
                message_id="550e8400-e29b-41d4-a716-446655440000",  # v4
                timestamp="2026-02-07T12:00:00Z",
                sender="validator",
                recipient="executor",
                action="fs.read",
                action_version="1.0.0",
                payload={}
            )

    def test_envelope_invalid_timestamp(self):
        """Test envelope rejects invalid ISO timestamp."""
        with pytest.raises(ValidationError, match="timestamp"):
            Envelope(
                envelope_version="1.0.0",
                message_id="01234567-89ab-7def-8123-456789abcdef",
                timestamp="not-a-timestamp",
                sender="validator",
                recipient="executor",
                action="fs.read",
                action_version="1.0.0",
                payload={}
            )

    def test_envelope_frozen(self):
        """Test that envelope is immutable (frozen)."""
        envelope = Envelope(
            envelope_version="1.0.0",
            message_id="01234567-89ab-7def-8123-456789abcdef",
            timestamp="2026-02-07T12:00:00Z",
            sender="validator",
            recipient="executor",
            action="fs.read",
            action_version="1.0.0",
            payload={}
        )

        with pytest.raises(ValidationError):
            envelope.sender = "attacker"


class TestFsReadModel:
    """Tests for FsReadAction model."""

    def test_fs_read_valid_path(self):
        """Test fs.read accepts valid relative path."""
        action = FsReadAction(path="data/test.txt")
        assert action.path == "data/test.txt"

    def test_fs_read_rejects_absolute_path(self):
        """Test fs.read rejects absolute path."""
        with pytest.raises(ValidationError, match="Absolute paths not allowed"):
            FsReadAction(path="/etc/passwd")

    def test_fs_read_rejects_parent_traversal(self):
        """Test fs.read rejects .. in path."""
        with pytest.raises(ValidationError, match="Parent directory traversal"):
            FsReadAction(path="../../../etc/passwd")

    def test_fs_read_rejects_null_byte(self):
        """Test fs.read rejects null byte in path."""
        with pytest.raises(ValidationError, match="null byte"):
            FsReadAction(path="test\x00.txt")

    def test_fs_read_rejects_control_characters(self):
        """Test fs.read rejects control characters."""
        with pytest.raises(ValidationError, match="control character"):
            FsReadAction(path="test\x01.txt")

    def test_fs_read_rejects_windows_drive(self):
        """Test fs.read rejects Windows drive letters."""
        with pytest.raises(ValidationError, match="drive"):
            FsReadAction(path="C:\\Users\\test.txt")

    def test_fs_read_normalizes_unicode(self):
        """Test fs.read normalizes Unicode to NFC."""
        # Café with combining accent (NFD)
        action = FsReadAction(path="café.txt")

        # Should be normalized to NFC
        import unicodedata
        assert unicodedata.is_normalized('NFC', action.path)

    def test_fs_read_normalizes_path_separators(self):
        """Test fs.read normalizes backslashes to forward slashes."""
        action = FsReadAction(path="data\\subdir\\file.txt")
        assert action.path == "data/subdir/file.txt"

    def test_fs_read_defaults(self):
        """Test fs.read default values."""
        action = FsReadAction(path="test.txt")

        assert action.offset == 0
        assert action.length == 1048576  # 1MB
        assert action.encoding == "utf-8"

    def test_fs_read_encoding_validation(self):
        """Test fs.read validates encoding values."""
        # Valid encodings
        FsReadAction(path="test.txt", encoding="utf-8")
        FsReadAction(path="test.txt", encoding="ascii")
        FsReadAction(path="test.txt", encoding="binary")

        # Invalid encoding
        with pytest.raises(ValidationError):
            FsReadAction(path="test.txt", encoding="invalid")


class TestFsListDirModel:
    """Tests for FsListDirAction model."""

    def test_fs_list_dir_valid_path(self):
        """Test fs.list_dir accepts valid path."""
        action = FsListDirAction(path="data")
        assert action.path == "data"

    def test_fs_list_dir_rejects_absolute_path(self):
        """Test fs.list_dir rejects absolute path."""
        with pytest.raises(ValidationError, match="Absolute"):
            FsListDirAction(path="/etc")

    def test_fs_list_dir_rejects_parent_traversal(self):
        """Test fs.list_dir rejects .. traversal."""
        with pytest.raises(ValidationError, match="traversal"):
            FsListDirAction(path="../data")

    def test_fs_list_dir_rejects_null_byte(self):
        """Test fs.list_dir rejects null byte."""
        with pytest.raises(ValidationError, match="null"):
            FsListDirAction(path="data\x00")

    def test_fs_list_dir_rejects_control_characters(self):
        """Test fs.list_dir rejects control characters."""
        with pytest.raises(ValidationError, match="control"):
            FsListDirAction(path="data\x1f")

    def test_fs_list_dir_normalizes_path_separators(self):
        """Test fs.list_dir normalizes path separators."""
        action = FsListDirAction(path="data\\subdir")
        assert action.path == "data/subdir"

    def test_fs_list_dir_defaults(self):
        """Test fs.list_dir default values."""
        action = FsListDirAction(path="data")

        assert action.max_entries == 100
        assert action.sort_order == "name_asc"
        assert action.include_hidden is False
        assert action.recursive is False

    def test_fs_list_dir_sort_order_validation(self):
        """Test fs.list_dir validates sort_order values."""
        # Valid sort orders
        FsListDirAction(path="data", sort_order="name_asc")
        FsListDirAction(path="data", sort_order="name_desc")
        FsListDirAction(path="data", sort_order="mtime_asc")
        FsListDirAction(path="data", sort_order="mtime_desc")

        # Invalid sort order
        with pytest.raises(ValidationError):
            FsListDirAction(path="data", sort_order="invalid")


class TestSystemHealthPingModel:
    """Tests for SystemHealthPingAction model."""

    def test_health_ping_with_echo(self):
        """Test health_ping with echo string."""
        action = SystemHealthPingAction(echo="hello world")
        assert action.echo == "hello world"

    def test_health_ping_without_echo(self):
        """Test health_ping without echo (optional)."""
        action = SystemHealthPingAction()
        assert action.echo is None

    def test_health_ping_empty_payload(self):
        """Test health_ping with explicit empty dict."""
        action = SystemHealthPingAction(**{})
        assert action.echo is None

    def test_health_ping_max_length(self):
        """Test health_ping enforces max length on echo."""
        # Within limit
        SystemHealthPingAction(echo="a" * 256)

        # Exceeds limit
        with pytest.raises(ValidationError):
            SystemHealthPingAction(echo="a" * 257)


class TestModelStrictness:
    """Tests for model strictness settings."""

    def test_models_reject_extra_fields(self):
        """Test that all models reject extra fields."""
        # Envelope
        with pytest.raises(ValidationError):
            Envelope(
                envelope_version="1.0.0",
                message_id="01234567-89ab-7def-8123-456789abcdef",
                timestamp="2026-02-07T12:00:00Z",
                sender="validator",
                recipient="executor",
                action="fs.read",
                action_version="1.0.0",
                payload={},
                extra_field="not_allowed"
            )

        # FsReadAction
        with pytest.raises(ValidationError):
            FsReadAction(path="test.txt", extra_field="not_allowed")

        # FsListDirAction
        with pytest.raises(ValidationError):
            FsListDirAction(path="data", extra_field="not_allowed")

        # SystemHealthPingAction
        with pytest.raises(ValidationError):
            SystemHealthPingAction(extra_field="not_allowed")

    def test_models_are_frozen(self):
        """Test that all models are immutable."""
        # FsReadAction
        action = FsReadAction(path="test.txt")
        with pytest.raises(ValidationError):
            action.path = "changed.txt"

        # FsListDirAction
        action2 = FsListDirAction(path="data")
        with pytest.raises(ValidationError):
            action2.path = "changed"

        # SystemHealthPingAction
        action3 = SystemHealthPingAction(echo="test")
        with pytest.raises(ValidationError):
            action3.echo = "changed"
