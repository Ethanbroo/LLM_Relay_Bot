"""Tests for RBAC policy enforcement."""

import pytest
from validator.rbac import RBACPolicy, RBACDeniedError


@pytest.fixture
def rbac():
    """Create RBAC policy fixture."""
    return RBACPolicy(policy_path="config/policy.yaml")


def test_allow_fs_read_in_workspace(rbac):
    """Test that fs.read is allowed in workspace."""
    rule_id = rbac.check_access(
        principal="validator",
        action="fs.read",
        resource="/workspace/test.txt"
    )

    assert rule_id is not None
    assert "executor.fs.read" in rule_id


def test_allow_fs_list_dir_in_workspace(rbac):
    """Test that fs.list_dir is allowed in workspace."""
    rule_id = rbac.check_access(
        principal="validator",
        action="fs.list_dir",
        resource="/workspace/subdir"
    )

    assert rule_id is not None


def test_allow_health_ping(rbac):
    """Test that system.health_ping is allowed."""
    rule_id = rbac.check_access(
        principal="validator",
        action="system.health_ping",
        resource="*"
    )

    assert rule_id is not None
    assert "health_ping" in rule_id


def test_deny_unknown_principal(rbac):
    """Test that unknown principals are denied."""
    with pytest.raises(RBACDeniedError, match="Unknown principal"):
        rbac.check_access(
            principal="hacker",
            action="fs.read",
            resource="/workspace/test.txt"
        )


def test_deny_outside_workspace(rbac):
    """Test that filesystem access outside workspace is denied."""
    with pytest.raises(RBACDeniedError):
        rbac.check_access(
            principal="validator",
            action="fs.read",
            resource="/etc/passwd"
        )


def test_deny_git_directory(rbac):
    """Test that .git directory is explicitly denied."""
    with pytest.raises(RBACDeniedError) as exc_info:
        rbac.check_access(
            principal="validator",
            action="fs.read",
            resource="/workspace/.git/config"
        )
    assert exc_info.value.reason == "Git internals are never accessible"


def test_deny_env_file(rbac):
    """Test that .env files are explicitly denied."""
    with pytest.raises(RBACDeniedError) as exc_info:
        rbac.check_access(
            principal="validator",
            action="fs.read",
            resource="/workspace/.env"
        )
    assert exc_info.value.reason == "Environment files contain secrets"


def test_deny_config_files(rbac):
    """Test that config files are explicitly denied."""
    with pytest.raises(RBACDeniedError) as exc_info:
        rbac.check_access(
            principal="validator",
            action="fs.read",
            resource="/workspace/config/policy.yaml"
        )
    assert exc_info.value.reason == "Config files are immutable at runtime"


def test_deny_by_default(rbac):
    """Test that unlisted actions are denied by default."""
    with pytest.raises(RBACDeniedError):
        rbac.check_access(
            principal="validator",
            action="fs.write",  # Not in policy
            resource="/workspace/test.txt"
        )


def test_nested_workspace_path(rbac):
    """Test that nested workspace paths are allowed."""
    rule_id = rbac.check_access(
        principal="validator",
        action="fs.read",
        resource="/workspace/deeply/nested/path/file.txt"
    )

    assert rule_id is not None


def test_list_principal_permissions(rbac):
    """Test listing permissions for a principal."""
    perms = rbac.list_principal_permissions("validator")

    assert len(perms) > 0
    actions = [p['action'] for p in perms]
    assert "fs.read" in actions
    assert "fs.list_dir" in actions
