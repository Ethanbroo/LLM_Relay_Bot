"""Tests for sandbox manager."""

import pytest
from pathlib import Path
from executor.sandbox import Sandbox, SandboxError


@pytest.fixture
def sandbox(tmp_path):
    """Create sandbox with temporary workspace."""
    task_id = "a" * 64
    run_id = "01234567-89ab-7def-8123-456789abcdef"
    workspace_root = str(tmp_path / "sandboxes")

    return Sandbox(task_id, run_id, workspace_root)


def test_sandbox_init(sandbox):
    """Test sandbox initialization."""
    assert sandbox.task_id == "a" * 64
    assert sandbox.run_id == "01234567-89ab-7def-8123-456789abcdef"
    assert sandbox.is_active is False
    assert sandbox.is_destroyed is False
    assert sandbox.sandbox_id.startswith("sandbox_")


def test_sandbox_id_deterministic(tmp_path):
    """Test sandbox_id is deterministic."""
    task_id = "a" * 64
    run_id = "01234567-89ab-7def-8123-456789abcdef"
    workspace_root = str(tmp_path / "sandboxes")

    sandbox1 = Sandbox(task_id, run_id, workspace_root)
    sandbox2 = Sandbox(task_id, run_id, workspace_root)

    assert sandbox1.sandbox_id == sandbox2.sandbox_id


def test_sandbox_create(sandbox):
    """Test sandbox creation."""
    sandbox.create()

    assert sandbox.is_active is True
    assert sandbox.workspace_path.exists()
    assert sandbox.workspace_path.is_dir()


def test_sandbox_create_already_active_raises_error(sandbox):
    """Test creating already active sandbox raises error."""
    sandbox.create()

    with pytest.raises(SandboxError, match="already active"):
        sandbox.create()


def test_sandbox_create_already_destroyed_raises_error(sandbox):
    """Test creating destroyed sandbox raises error."""
    sandbox.create()
    sandbox.destroy()

    with pytest.raises(SandboxError, match="already destroyed"):
        sandbox.create()


def test_sandbox_destroy(sandbox):
    """Test sandbox destruction."""
    sandbox.create()
    sandbox.destroy()

    assert sandbox.is_active is False
    assert sandbox.is_destroyed is True
    assert not sandbox.workspace_path.exists()


def test_sandbox_destroy_idempotent(sandbox):
    """Test destroy is idempotent."""
    sandbox.create()
    sandbox.destroy()
    sandbox.destroy()  # Should not raise

    assert sandbox.is_destroyed is True


def test_sandbox_context_manager(tmp_path):
    """Test sandbox as context manager."""
    task_id = "a" * 64
    run_id = "01234567-89ab-7def-8123-456789abcdef"
    workspace_root = str(tmp_path / "sandboxes")

    with Sandbox(task_id, run_id, workspace_root) as sandbox:
        assert sandbox.is_active is True
        assert sandbox.workspace_path.exists()

    assert sandbox.is_destroyed is True
    assert not sandbox.workspace_path.exists()


def test_get_workspace_path_relative(sandbox):
    """Test get_workspace_path with relative path."""
    sandbox.create()

    path = sandbox.get_workspace_path("subdir/file.txt")

    assert path.is_absolute()
    assert "subdir/file.txt" in str(path)


def test_get_workspace_path_escapes_sandbox_raises_error(sandbox):
    """Test path traversal is rejected."""
    sandbox.create()

    with pytest.raises(SandboxError, match="escapes sandbox"):
        sandbox.get_workspace_path("../outside.txt")


def test_get_workspace_path_not_active_raises_error(sandbox):
    """Test get_workspace_path requires active sandbox."""
    with pytest.raises(SandboxError, match="not active"):
        sandbox.get_workspace_path("file.txt")


def test_write_file(sandbox):
    """Test writing file to sandbox."""
    sandbox.create()

    file_path = sandbox.write_file("test.txt", "Hello, World!")

    assert file_path.exists()
    assert file_path.read_text(encoding='utf-8') == "Hello, World!"


def test_write_file_creates_parent_dirs(sandbox):
    """Test write_file creates parent directories."""
    sandbox.create()

    file_path = sandbox.write_file("subdir/nested/test.txt", "content")

    assert file_path.exists()
    assert file_path.parent.exists()


def test_read_file(sandbox):
    """Test reading file from sandbox."""
    sandbox.create()

    sandbox.write_file("test.txt", "Hello, World!")
    content = sandbox.read_file("test.txt")

    assert content == "Hello, World!"


def test_read_file_not_exists_raises_error(sandbox):
    """Test reading non-existent file raises error."""
    sandbox.create()

    with pytest.raises(SandboxError, match="Failed to read file"):
        sandbox.read_file("nonexistent.txt")


def test_list_dir(sandbox):
    """Test listing directory contents."""
    sandbox.create()

    sandbox.write_file("file1.txt", "content1")
    sandbox.write_file("file2.txt", "content2")
    sandbox.write_file("subdir/file3.txt", "content3")

    entries = sandbox.list_dir(".")

    assert len(entries) == 3  # file1, file2, subdir
    names = [e["name"] for e in entries]
    assert "file1.txt" in names
    assert "file2.txt" in names
    assert "subdir" in names


def test_list_dir_sorted_by_name(sandbox):
    """Test directory listing is sorted by name."""
    sandbox.create()

    sandbox.write_file("zebra.txt", "")
    sandbox.write_file("alpha.txt", "")
    sandbox.write_file("beta.txt", "")

    entries = sandbox.list_dir(".")

    names = [e["name"] for e in entries]
    assert names == ["alpha.txt", "beta.txt", "zebra.txt"]


def test_list_dir_includes_type_and_size(sandbox):
    """Test directory listing includes type and size."""
    sandbox.create()

    sandbox.write_file("file.txt", "12345")
    sandbox.write_file("subdir/nested.txt", "content")

    entries = sandbox.list_dir(".")

    file_entry = next(e for e in entries if e["name"] == "file.txt")
    dir_entry = next(e for e in entries if e["name"] == "subdir")

    assert file_entry["type"] == "file"
    assert file_entry["size"] == 5

    assert dir_entry["type"] == "directory"
    assert dir_entry["size"] is None


def test_list_dir_not_directory_raises_error(sandbox):
    """Test listing file raises error."""
    sandbox.create()

    sandbox.write_file("file.txt", "content")

    with pytest.raises(SandboxError, match="Not a directory"):
        sandbox.list_dir("file.txt")


def test_sandbox_isolation(tmp_path):
    """Test multiple sandboxes are isolated."""
    workspace_root = str(tmp_path / "sandboxes")

    sandbox1 = Sandbox("a" * 64, "01234567-89ab-7def-8123-456789abcdef", workspace_root)
    sandbox2 = Sandbox("b" * 64, "11234567-89ab-7def-8123-456789abcdef", workspace_root)

    sandbox1.create()
    sandbox2.create()

    sandbox1.write_file("file1.txt", "sandbox1")
    sandbox2.write_file("file2.txt", "sandbox2")

    # sandbox1 should not see sandbox2's files
    entries1 = sandbox1.list_dir(".")
    assert len(entries1) == 1
    assert entries1[0]["name"] == "file1.txt"

    # sandbox2 should not see sandbox1's files
    entries2 = sandbox2.list_dir(".")
    assert len(entries2) == 1
    assert entries2[0]["name"] == "file2.txt"

    sandbox1.destroy()
    sandbox2.destroy()
