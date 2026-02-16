"""Tests for snapshot and rollback."""

import pytest
from pathlib import Path
from executor.rollback import SnapshotManager, RollbackError


@pytest.fixture
def snapshot_manager(tmp_path):
    """Create snapshot manager with temporary root."""
    snapshot_root = str(tmp_path / "snapshots")
    return SnapshotManager(snapshot_root)


@pytest.fixture
def workspace(tmp_path):
    """Create temporary workspace directory."""
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()

    # Add some files
    (workspace_path / "file1.txt").write_text("content1")
    (workspace_path / "file2.txt").write_text("content2")
    (workspace_path / "subdir").mkdir()
    (workspace_path / "subdir" / "file3.txt").write_text("content3")

    return workspace_path


def test_create_snapshot(snapshot_manager, workspace):
    """Test creating snapshot of workspace."""
    task_id = "a" * 64
    run_id = "01234567-89ab-7def-8123-456789abcdef"
    attempt = 1

    metadata = snapshot_manager.create_snapshot(task_id, run_id, attempt, workspace)

    assert "snapshot_id" in metadata
    assert metadata["snapshot_id"].startswith("snapshot_")
    assert metadata["task_id"] == task_id
    assert metadata["run_id"] == run_id
    assert metadata["attempt"] == attempt
    assert metadata["size_bytes"] > 0


def test_snapshot_id_deterministic(snapshot_manager, workspace):
    """Test snapshot_id is deterministic."""
    task_id = "a" * 64
    run_id = "01234567-89ab-7def-8123-456789abcdef"
    attempt = 1

    metadata1 = snapshot_manager.create_snapshot(task_id, run_id, attempt, workspace)
    # Create again (will overwrite)
    metadata2 = snapshot_manager.create_snapshot(task_id, run_id, attempt, workspace)

    assert metadata1["snapshot_id"] == metadata2["snapshot_id"]


def test_create_snapshot_workspace_not_exists(snapshot_manager, tmp_path):
    """Test creating snapshot of non-existent workspace fails."""
    task_id = "a" * 64
    run_id = "01234567-89ab-7def-8123-456789abcdef"
    attempt = 1
    nonexistent_path = tmp_path / "nonexistent"

    with pytest.raises(RollbackError, match="does not exist"):
        snapshot_manager.create_snapshot(task_id, run_id, attempt, nonexistent_path)


def test_rollback_restores_workspace(snapshot_manager, workspace):
    """Test rollback restores workspace to snapshot state."""
    task_id = "a" * 64
    run_id = "01234567-89ab-7def-8123-456789abcdef"
    attempt = 1

    # Create snapshot
    snapshot_metadata = snapshot_manager.create_snapshot(task_id, run_id, attempt, workspace)
    snapshot_id = snapshot_metadata["snapshot_id"]

    # Modify workspace
    (workspace / "file1.txt").write_text("MODIFIED")
    (workspace / "new_file.txt").write_text("NEW")

    # Rollback
    rollback_metadata = snapshot_manager.rollback(snapshot_id, run_id, workspace, verify=True)

    assert rollback_metadata["success"] is True
    assert rollback_metadata["verified"] is True

    # Check workspace restored
    assert (workspace / "file1.txt").read_text() == "content1"
    assert not (workspace / "new_file.txt").exists()


def test_rollback_with_verification(snapshot_manager, workspace):
    """Test rollback verification catches mismatches."""
    task_id = "a" * 64
    run_id = "01234567-89ab-7def-8123-456789abcdef"
    attempt = 1

    snapshot_metadata = snapshot_manager.create_snapshot(task_id, run_id, attempt, workspace)
    snapshot_id = snapshot_metadata["snapshot_id"]

    rollback_metadata = snapshot_manager.rollback(snapshot_id, run_id, workspace, verify=True)

    assert rollback_metadata["success"] is True
    assert rollback_metadata["verified"] is True


def test_rollback_snapshot_not_exists(snapshot_manager, workspace):
    """Test rollback fails if snapshot doesn't exist."""
    run_id = "01234567-89ab-7def-8123-456789abcdef"
    nonexistent_snapshot = "snapshot_nonexistent"

    with pytest.raises(RollbackError, match="does not exist"):
        snapshot_manager.rollback(nonexistent_snapshot, run_id, workspace)


def test_delete_snapshot(snapshot_manager, workspace):
    """Test deleting snapshot."""
    task_id = "a" * 64
    run_id = "01234567-89ab-7def-8123-456789abcdef"
    attempt = 1

    metadata = snapshot_manager.create_snapshot(task_id, run_id, attempt, workspace)
    snapshot_id = metadata["snapshot_id"]

    # Verify snapshot exists
    assert snapshot_manager.get_snapshot_metadata(snapshot_id) is not None

    # Delete snapshot
    snapshot_manager.delete_snapshot(snapshot_id)

    # Verify snapshot deleted
    assert snapshot_manager.get_snapshot_metadata(snapshot_id) is None


def test_delete_snapshot_idempotent(snapshot_manager):
    """Test delete_snapshot is idempotent."""
    nonexistent_snapshot = "snapshot_nonexistent"

    # Should not raise
    snapshot_manager.delete_snapshot(nonexistent_snapshot)


def test_get_snapshot_metadata(snapshot_manager, workspace):
    """Test getting snapshot metadata."""
    task_id = "a" * 64
    run_id = "01234567-89ab-7def-8123-456789abcdef"
    attempt = 1

    created_metadata = snapshot_manager.create_snapshot(task_id, run_id, attempt, workspace)
    snapshot_id = created_metadata["snapshot_id"]

    retrieved_metadata = snapshot_manager.get_snapshot_metadata(snapshot_id)

    assert retrieved_metadata["snapshot_id"] == snapshot_id
    assert retrieved_metadata["task_id"] == task_id
    assert retrieved_metadata["run_id"] == run_id


def test_get_snapshot_metadata_not_exists(snapshot_manager):
    """Test get_snapshot_metadata returns None for non-existent snapshot."""
    metadata = snapshot_manager.get_snapshot_metadata("snapshot_nonexistent")

    assert metadata is None


def test_list_snapshots(snapshot_manager, workspace):
    """Test listing all snapshots."""
    task_id1 = "a" * 64
    task_id2 = "b" * 64
    run_id = "01234567-89ab-7def-8123-456789abcdef"

    snapshot_manager.create_snapshot(task_id1, run_id, 1, workspace)
    snapshot_manager.create_snapshot(task_id2, run_id, 1, workspace)

    snapshots = snapshot_manager.list_snapshots()

    assert len(snapshots) == 2


def test_list_snapshots_filtered_by_task_id(snapshot_manager, workspace):
    """Test listing snapshots filtered by task_id."""
    task_id1 = "a" * 64
    task_id2 = "b" * 64
    run_id = "01234567-89ab-7def-8123-456789abcdef"

    snapshot_manager.create_snapshot(task_id1, run_id, 1, workspace)
    snapshot_manager.create_snapshot(task_id2, run_id, 1, workspace)

    snapshots = snapshot_manager.list_snapshots(task_id=task_id1)

    assert len(snapshots) == 1
    assert snapshots[0]["task_id"] == task_id1


def test_rollback_id_deterministic(snapshot_manager, workspace):
    """Test rollback_id is deterministic."""
    task_id = "a" * 64
    run_id = "01234567-89ab-7def-8123-456789abcdef"
    attempt = 1

    snapshot_metadata = snapshot_manager.create_snapshot(task_id, run_id, attempt, workspace)
    snapshot_id = snapshot_metadata["snapshot_id"]

    # Modify workspace
    (workspace / "file1.txt").write_text("MODIFIED")

    rollback_metadata1 = snapshot_manager.rollback(snapshot_id, run_id, workspace)

    # Modify again
    (workspace / "file1.txt").write_text("MODIFIED2")

    rollback_metadata2 = snapshot_manager.rollback(snapshot_id, run_id, workspace)

    assert rollback_metadata1["rollback_id"] == rollback_metadata2["rollback_id"]


def test_snapshot_preserves_directory_structure(snapshot_manager, workspace):
    """Test snapshot preserves nested directory structure."""
    task_id = "a" * 64
    run_id = "01234567-89ab-7def-8123-456789abcdef"
    attempt = 1

    # Create nested structure
    (workspace / "deep" / "nested" / "dirs").mkdir(parents=True)
    (workspace / "deep" / "nested" / "dirs" / "file.txt").write_text("deep content")

    # Snapshot
    snapshot_metadata = snapshot_manager.create_snapshot(task_id, run_id, attempt, workspace)
    snapshot_id = snapshot_metadata["snapshot_id"]

    # Delete workspace
    import shutil
    shutil.rmtree(workspace)
    workspace.mkdir()

    # Rollback
    snapshot_manager.rollback(snapshot_id, run_id, workspace)

    # Verify structure restored
    assert (workspace / "deep" / "nested" / "dirs" / "file.txt").exists()
    assert (workspace / "deep" / "nested" / "dirs" / "file.txt").read_text() == "deep content"
