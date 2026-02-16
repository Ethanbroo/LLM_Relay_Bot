"""Comprehensive RBAC tests."""

import pytest
from validator.rbac import RBACPolicy, RBACDeniedError


@pytest.fixture
def rbac():
    """Create RBAC policy."""
    return RBACPolicy(policy_path="config/policy.yaml")


def test_rbac_pattern_wildcard_matches_all(rbac):
    """Test that * pattern matches any resource."""
    # system.health_ping allows resource: "*"
    rule_id = rbac.check_access("validator", "system.health_ping", "/any/path")
    assert rule_id is not None

    rule_id = rbac.check_access("validator", "system.health_ping", "")
    assert rule_id is not None


def test_rbac_glob_pattern_single_star(rbac):
    """Test single * wildcard in patterns."""
    # Test that /** pattern matches paths
    result = rbac._match_resource_pattern("/workspace/file.txt", "/workspace/*")
    assert result is True

    # Single * doesn't match across slashes
    result = rbac._match_resource_pattern("/workspace/deep/file.txt", "/workspace/*")
    assert result is False


def test_rbac_glob_pattern_double_star(rbac):
    """Test ** wildcard matches multiple path components."""
    # /workspace/** should match any depth under /workspace
    result = rbac._match_resource_pattern("/workspace/a/b/c/file.txt", "/workspace/**")
    assert result is True

    result = rbac._match_resource_pattern("/workspace/file.txt", "/workspace/**")
    assert result is True


def test_rbac_deny_rule_priority_over_allow(rbac):
    """Test that deny rules have priority over allow rules."""
    # .env is denied globally but might match workspace allow rule
    with pytest.raises(RBACDeniedError) as exc_info:
        rbac.check_access("validator", "fs.read", "/workspace/.env")

    assert exc_info.value.reason == "Environment files contain secrets"


def test_rbac_deny_rules_processed_first(rbac):
    """Test that deny rules are checked before allow rules."""
    # Even though workspace/** is allowed, .git is denied
    with pytest.raises(RBACDeniedError):
        rbac.check_access("validator", "fs.read", "/workspace/project/.git/config")


def test_rbac_action_glob_pattern(rbac):
    """Test glob pattern matching for actions."""
    # fs.* pattern should match fs.read and fs.list_dir
    result = rbac._matches_rule("fs.read", "/workspace/.git/config", {
        "action": "fs.*",
        "resource": "/**/.git/**"
    })
    assert result is True

    result = rbac._matches_rule("fs.list_dir", "/workspace/.git/", {
        "action": "fs.*",
        "resource": "/**/.git/**"
    })
    assert result is True


def test_rbac_list_principal_permissions_unknown_principal(rbac):
    """Test listing permissions for unknown principal."""
    perms = rbac.list_principal_permissions("unknown_principal")
    assert perms == []


def test_rbac_principal_with_no_roles(rbac, tmp_path):
    """Test principal with no roles assigned."""
    # Create custom policy with principal but no roles
    policy_file = tmp_path / "test_policy.yaml"
    policy_file.write_text("""
principals:
  no_roles_user:
    description: "User with no roles"

roles:
  executor:
    allow:
      - action: "fs.read"
        resource: "/workspace/**"
        rule_id: "test.rule"
""")

    rbac_test = RBACPolicy(policy_path=str(policy_file))

    with pytest.raises(RBACDeniedError, match="no roles"):
        rbac_test.check_access("no_roles_user", "fs.read", "/workspace/file.txt")


def test_rbac_role_without_permissions(rbac, tmp_path):
    """Test role with no allow rules."""
    policy_file = tmp_path / "test_policy.yaml"
    policy_file.write_text("""
principals:
  limited_user:
    roles: ["empty_role"]

roles:
  empty_role:
    description: "Role with no permissions"
""")

    rbac_test = RBACPolicy(policy_path=str(policy_file))

    # Should be denied because role has no allow rules
    with pytest.raises(RBACDeniedError) as exc_info:
        rbac_test.check_access("limited_user", "fs.read", "/workspace/file.txt")

    # Check the reason mentions no matching allow rule
    assert "No allow rule" in exc_info.value.reason or "empty_role" in exc_info.value.reason


def test_rbac_complex_resource_pattern(rbac):
    """Test complex resource pattern matching."""
    # **/config/*.yaml should match /any/path/config/file.yaml
    result = rbac._match_resource_pattern(
        "/workspace/project/config/settings.yaml",
        "**/config/*.yaml"
    )
    assert result is True

    # Should not match non-yaml files
    result = rbac._match_resource_pattern(
        "/workspace/project/config/settings.txt",
        "**/config/*.yaml"
    )
    assert result is False


def test_rbac_exact_path_match(rbac):
    """Test exact path matching without wildcards."""
    result = rbac._match_resource_pattern(
        "/workspace/specific/file.txt",
        "/workspace/specific/file.txt"
    )
    assert result is True

    result = rbac._match_resource_pattern(
        "/workspace/specific/other.txt",
        "/workspace/specific/file.txt"
    )
    assert result is False


def test_rbac_pattern_with_dots_in_filename(rbac):
    """Test pattern matching with dots in filenames."""
    # .env pattern should match exactly
    result = rbac._match_resource_pattern("/workspace/.env", "/**/.env")
    assert result is True

    # Should not match similar names
    result = rbac._match_resource_pattern("/workspace/.env.backup", "/**/.env")
    assert result is False


def test_rbac_nested_directory_patterns(rbac):
    """Test patterns for nested directories."""
    # /.git/ in path
    result = rbac._match_resource_pattern(
        "/workspace/project/.git/hooks/pre-commit",
        "/**/.git/**"
    )
    assert result is True

    result = rbac._match_resource_pattern(
        "/workspace/project/.github/workflows/ci.yaml",
        "/**/.git/**"
    )
    assert result is False


def test_rbac_multiple_deny_rules_evaluated(rbac):
    """Test that all deny rules are evaluated."""
    # .git denied
    with pytest.raises(RBACDeniedError) as exc_info:
        rbac.check_access("validator", "fs.read", "/workspace/.git/config")
    assert "Git internals" in exc_info.value.reason

    # .env denied
    with pytest.raises(RBACDeniedError) as exc_info:
        rbac.check_access("validator", "fs.read", "/workspace/.env")
    assert "Environment files" in exc_info.value.reason

    # config/*.yaml denied
    with pytest.raises(RBACDeniedError) as exc_info:
        rbac.check_access("validator", "fs.read", "/workspace/config/policy.yaml")
    assert "Config files" in exc_info.value.reason


def test_rbac_case_sensitive_action_matching(rbac):
    """Test that action matching is case-sensitive."""
    # fs.read should not match FS.READ or Fs.Read
    with pytest.raises(RBACDeniedError):
        rbac.check_access("validator", "FS.READ", "/workspace/file.txt")


def test_rbac_special_characters_in_pattern(rbac):
    """Test pattern matching with regex special characters."""
    # Patterns with dots should match literally, not as regex wildcards
    result = rbac._match_resource_pattern("/workspace/file.txt", "/workspace/file.txt")
    assert result is True

    # Dot should not act as regex wildcard
    result = rbac._match_resource_pattern("/workspace/filextxt", "/workspace/file.txt")
    assert result is False
