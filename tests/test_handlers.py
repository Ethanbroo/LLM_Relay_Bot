"""Tests for execution handlers."""

import pytest
from executor.handlers.health_ping import HealthPingHandler
from executor.handlers.fs_read import FsReadHandler
from executor.handlers.fs_list_dir import FsListDirHandler
from executor.handlers.registry import HandlerRegistry
from executor.handlers import HandlerError
from executor.sandbox import Sandbox


@pytest.fixture
def sandbox(tmp_path):
    """Create active sandbox for testing."""
    task_id = "a" * 64
    run_id = "01234567-89ab-7def-8123-456789abcdef"
    workspace_root = str(tmp_path / "sandboxes")

    sandbox = Sandbox(task_id, run_id, workspace_root)
    sandbox.create()

    yield sandbox

    sandbox.destroy()


# Health Ping Handler Tests

def test_health_ping_with_echo(sandbox):
    """Test health_ping handler with echo value."""
    handler = HealthPingHandler()

    validated_action = {
        "sanitized_payload": {
            "echo": "test message"
        }
    }

    artifacts = handler.execute(validated_action, sandbox)

    assert artifacts["echo"] == "test message"
    assert artifacts["status"] == "healthy"


def test_health_ping_without_echo(sandbox):
    """Test health_ping handler without echo value."""
    handler = HealthPingHandler()

    validated_action = {
        "sanitized_payload": {}
    }

    artifacts = handler.execute(validated_action, sandbox)

    assert artifacts["echo"] is None
    assert artifacts["status"] == "healthy"


def test_health_ping_with_none_echo(sandbox):
    """Test health_ping handler with None echo."""
    handler = HealthPingHandler()

    validated_action = {
        "sanitized_payload": {
            "echo": None
        }
    }

    artifacts = handler.execute(validated_action, sandbox)

    assert artifacts["echo"] is None


# fs.read Handler Tests

def test_fs_read_basic(sandbox):
    """Test fs.read handler basic file read."""
    handler = FsReadHandler()

    # Create test file
    sandbox.write_file("test.txt", "Hello, World!")

    validated_action = {
        "sanitized_payload": {
            "path": "test.txt",
            "offset": 0,
            "length": 1048576,
            "encoding": "utf-8"
        }
    }

    artifacts = handler.execute(validated_action, sandbox)

    assert artifacts["content"] == "Hello, World!"
    assert artifacts["bytes_read"] == 13
    assert artifacts["encoding"] == "utf-8"
    assert artifacts["path"] == "test.txt"


def test_fs_read_with_offset(sandbox):
    """Test fs.read with offset."""
    handler = FsReadHandler()

    sandbox.write_file("test.txt", "0123456789")

    validated_action = {
        "sanitized_payload": {
            "path": "test.txt",
            "offset": 5,
            "length": 100,
            "encoding": "utf-8"
        }
    }

    artifacts = handler.execute(validated_action, sandbox)

    assert artifacts["content"] == "56789"
    assert artifacts["offset"] == 5


def test_fs_read_with_length_limit(sandbox):
    """Test fs.read with length limit."""
    handler = FsReadHandler()

    sandbox.write_file("test.txt", "0123456789")

    validated_action = {
        "sanitized_payload": {
            "path": "test.txt",
            "offset": 0,
            "length": 5,
            "encoding": "utf-8"
        }
    }

    artifacts = handler.execute(validated_action, sandbox)

    assert artifacts["content"] == "01234"


def test_fs_read_file_not_found(sandbox):
    """Test fs.read with non-existent file."""
    handler = FsReadHandler()

    validated_action = {
        "sanitized_payload": {
            "path": "nonexistent.txt",
            "offset": 0,
            "length": 100,
            "encoding": "utf-8"
        }
    }

    with pytest.raises(HandlerError, match="File not found"):
        handler.execute(validated_action, sandbox)


def test_fs_read_path_traversal_blocked(sandbox):
    """Test fs.read blocks path traversal."""
    handler = FsReadHandler()

    validated_action = {
        "sanitized_payload": {
            "path": "../../../etc/passwd",
            "offset": 0,
            "length": 100,
            "encoding": "utf-8"
        }
    }

    with pytest.raises(HandlerError, match="Invalid path"):
        handler.execute(validated_action, sandbox)


def test_fs_read_directory_raises_error(sandbox):
    """Test fs.read on directory raises error."""
    handler = FsReadHandler()

    sandbox.write_file("subdir/file.txt", "content")

    validated_action = {
        "sanitized_payload": {
            "path": "subdir",
            "offset": 0,
            "length": 100,
            "encoding": "utf-8"
        }
    }

    with pytest.raises(HandlerError, match="Not a file"):
        handler.execute(validated_action, sandbox)


# fs.list_dir Handler Tests

def test_fs_list_dir_basic(sandbox):
    """Test fs.list_dir basic directory listing."""
    handler = FsListDirHandler()

    sandbox.write_file("file1.txt", "content1")
    sandbox.write_file("file2.txt", "content2")

    validated_action = {
        "sanitized_payload": {
            "path": ".",
            "max_entries": 100,
            "sort_order": "name_asc",
            "include_hidden": False,
            "recursive": False
        }
    }

    artifacts = handler.execute(validated_action, sandbox)

    assert artifacts["count"] == 2
    assert artifacts["truncated"] is False
    assert len(artifacts["entries"]) == 2

    names = [e["name"] for e in artifacts["entries"]]
    assert "file1.txt" in names
    assert "file2.txt" in names


def test_fs_list_dir_sorted_by_name_asc(sandbox):
    """Test fs.list_dir sorting by name ascending."""
    handler = FsListDirHandler()

    sandbox.write_file("zebra.txt", "")
    sandbox.write_file("alpha.txt", "")
    sandbox.write_file("beta.txt", "")

    validated_action = {
        "sanitized_payload": {
            "path": ".",
            "max_entries": 100,
            "sort_order": "name_asc",
            "include_hidden": False,
            "recursive": False
        }
    }

    artifacts = handler.execute(validated_action, sandbox)

    names = [e["name"] for e in artifacts["entries"]]
    assert names == ["alpha.txt", "beta.txt", "zebra.txt"]


def test_fs_list_dir_sorted_by_name_desc(sandbox):
    """Test fs.list_dir sorting by name descending."""
    handler = FsListDirHandler()

    sandbox.write_file("alpha.txt", "")
    sandbox.write_file("beta.txt", "")

    validated_action = {
        "sanitized_payload": {
            "path": ".",
            "max_entries": 100,
            "sort_order": "name_desc",
            "include_hidden": False,
            "recursive": False
        }
    }

    artifacts = handler.execute(validated_action, sandbox)

    names = [e["name"] for e in artifacts["entries"]]
    assert names == ["beta.txt", "alpha.txt"]


def test_fs_list_dir_max_entries(sandbox):
    """Test fs.list_dir respects max_entries limit."""
    handler = FsListDirHandler()

    for i in range(10):
        sandbox.write_file(f"file{i}.txt", "content")

    validated_action = {
        "sanitized_payload": {
            "path": ".",
            "max_entries": 5,
            "sort_order": "name_asc",
            "include_hidden": False,
            "recursive": False
        }
    }

    artifacts = handler.execute(validated_action, sandbox)

    assert artifacts["count"] == 5
    assert artifacts["truncated"] is True


def test_fs_list_dir_excludes_hidden_by_default(sandbox):
    """Test fs.list_dir excludes hidden files by default."""
    handler = FsListDirHandler()

    sandbox.write_file("visible.txt", "")
    sandbox.write_file(".hidden.txt", "")

    validated_action = {
        "sanitized_payload": {
            "path": ".",
            "max_entries": 100,
            "sort_order": "name_asc",
            "include_hidden": False,
            "recursive": False
        }
    }

    artifacts = handler.execute(validated_action, sandbox)

    names = [e["name"] for e in artifacts["entries"]]
    assert "visible.txt" in names
    assert ".hidden.txt" not in names


def test_fs_list_dir_includes_hidden_when_requested(sandbox):
    """Test fs.list_dir includes hidden files when requested."""
    handler = FsListDirHandler()

    sandbox.write_file("visible.txt", "")
    sandbox.write_file(".hidden.txt", "")

    validated_action = {
        "sanitized_payload": {
            "path": ".",
            "max_entries": 100,
            "sort_order": "name_asc",
            "include_hidden": True,
            "recursive": False
        }
    }

    artifacts = handler.execute(validated_action, sandbox)

    names = [e["name"] for e in artifacts["entries"]]
    assert "visible.txt" in names
    assert ".hidden.txt" in names


def test_fs_list_dir_recursive(sandbox):
    """Test fs.list_dir recursive listing."""
    handler = FsListDirHandler()

    sandbox.write_file("file1.txt", "")
    sandbox.write_file("subdir/file2.txt", "")
    sandbox.write_file("subdir/nested/file3.txt", "")

    validated_action = {
        "sanitized_payload": {
            "path": ".",
            "max_entries": 100,
            "sort_order": "name_asc",
            "include_hidden": False,
            "recursive": True
        }
    }

    artifacts = handler.execute(validated_action, sandbox)

    # Should include all files and directories
    assert artifacts["count"] >= 5  # file1, subdir, file2, nested, file3


def test_fs_list_dir_not_directory_raises_error(sandbox):
    """Test fs.list_dir on file raises error."""
    handler = FsListDirHandler()

    sandbox.write_file("file.txt", "content")

    validated_action = {
        "sanitized_payload": {
            "path": "file.txt",
            "max_entries": 100,
            "sort_order": "name_asc",
            "include_hidden": False,
            "recursive": False
        }
    }

    with pytest.raises(HandlerError, match="Not a directory"):
        handler.execute(validated_action, sandbox)


def test_fs_list_dir_entry_types(sandbox):
    """Test fs.list_dir distinguishes files and directories."""
    handler = FsListDirHandler()

    sandbox.write_file("file.txt", "content")
    sandbox.write_file("subdir/nested.txt", "content")

    validated_action = {
        "sanitized_payload": {
            "path": ".",
            "max_entries": 100,
            "sort_order": "name_asc",
            "include_hidden": False,
            "recursive": False
        }
    }

    artifacts = handler.execute(validated_action, sandbox)

    file_entry = next(e for e in artifacts["entries"] if e["name"] == "file.txt")
    dir_entry = next(e for e in artifacts["entries"] if e["name"] == "subdir")

    assert file_entry["type"] == "file"
    assert file_entry["size"] == 7

    assert dir_entry["type"] == "directory"
    assert "size" not in dir_entry  # Directories don't have size field


# Handler Registry Tests

def test_registry_get_handler_health_ping():
    """Test registry returns health_ping handler."""
    registry = HandlerRegistry()

    handler = registry.get_handler("system.health_ping")

    assert isinstance(handler, HealthPingHandler)


def test_registry_get_handler_fs_read():
    """Test registry returns fs.read handler."""
    registry = HandlerRegistry()

    handler = registry.get_handler("fs.read")

    assert isinstance(handler, FsReadHandler)


def test_registry_get_handler_fs_list_dir():
    """Test registry returns fs.list_dir handler."""
    registry = HandlerRegistry()

    handler = registry.get_handler("fs.list_dir")

    assert isinstance(handler, FsListDirHandler)


def test_registry_get_handler_not_found():
    """Test registry raises error for unknown action."""
    registry = HandlerRegistry()

    with pytest.raises(HandlerError, match="Handler not found"):
        registry.get_handler("unknown.action")


def test_registry_has_handler():
    """Test registry has_handler check."""
    registry = HandlerRegistry()

    assert registry.has_handler("system.health_ping") is True
    assert registry.has_handler("fs.read") is True
    assert registry.has_handler("unknown.action") is False


def test_registry_list_actions():
    """Test registry lists all supported actions."""
    registry = HandlerRegistry()

    actions = registry.list_actions()

    assert "system.health_ping" in actions
    assert "fs.read" in actions
    assert "fs.list_dir" in actions
