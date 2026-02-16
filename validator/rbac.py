"""
Role-Based Access Control (RBAC) enforcement.

Deny-by-default policy evaluation.
"""

import yaml
import re
from pathlib import Path
from typing import Optional
from fnmatch import fnmatch


class RBACDeniedError(Exception):
    """RBAC check denied."""
    def __init__(self, message: str, reason: Optional[str] = None):
        super().__init__(message)
        self.reason = reason


class RBACPolicy:
    """
    RBAC policy evaluator.

    Deny-by-default: only explicitly allowed actions are permitted.
    """

    def __init__(self, policy_path: str = "config/policy.yaml"):
        """
        Initialize RBAC policy.

        Args:
            policy_path: Path to policy.yaml
        """
        self.policy_path = Path(policy_path)

        # Load policy
        with open(self.policy_path, 'r') as f:
            self.policy = yaml.safe_load(f)

        # Build lookup tables
        self._role_permissions = self._build_role_permissions()
        self._principal_roles = self.policy.get('principals', {})
        self._deny_rules = self.policy.get('deny', [])

    def check_access(
        self,
        principal: str,
        action: str,
        resource: str
    ) -> str:
        """
        Check if principal is allowed to perform action on resource.

        Args:
            principal: Principal identifier (e.g., "validator", "llm_claude")
            action: Action identifier (e.g., "fs.read", "fs.list_dir")
            resource: Resource path (e.g., "/workspace/data/file.txt")

        Returns:
            Rule ID that authorized the action

        Raises:
            RBACDeniedError: If access is denied
        """
        # Check deny rules first (highest priority)
        for deny_rule in self._deny_rules:
            if self._matches_rule(action, resource, deny_rule):
                reason = deny_rule.get('reason', 'Access explicitly denied by policy')
                raise RBACDeniedError(
                    f"Access denied for principal '{principal}' to action '{action}' on resource '{resource}'",
                    reason=reason
                )

        # Get principal's roles
        if principal not in self._principal_roles:
            raise RBACDeniedError(
                f"Unknown principal '{principal}' (not in policy)",
                reason="Principal not defined in policy"
            )

        principal_config = self._principal_roles[principal]
        roles = principal_config.get('roles', [])

        if not roles:
            raise RBACDeniedError(
                f"Principal '{principal}' has no roles assigned",
                reason="No roles assigned to principal"
            )

        # Check allow rules for each role
        for role in roles:
            if role not in self._role_permissions:
                continue

            for allow_rule in self._role_permissions[role]:
                if self._matches_rule(action, resource, allow_rule):
                    # Access granted
                    return allow_rule['rule_id']

        # No matching allow rule found
        raise RBACDeniedError(
            f"Access denied for principal '{principal}' to action '{action}' on resource '{resource}'",
            reason=f"No allow rule matches (roles: {roles})"
        )

    def _build_role_permissions(self) -> dict[str, list[dict]]:
        """Build role to permissions mapping."""
        role_perms = {}

        roles_config = self.policy.get('roles', {})
        for role_name, role_config in roles_config.items():
            allow_rules = role_config.get('allow', [])
            role_perms[role_name] = allow_rules

        return role_perms

    def _matches_rule(self, action: str, resource: str, rule: dict) -> bool:
        """
        Check if action and resource match a rule.

        Supports glob patterns for action and resource.

        Args:
            action: Action identifier
            resource: Resource path
            rule: Rule dict with 'action' and 'resource' keys

        Returns:
            True if rule matches
        """
        rule_action = rule.get('action', '')
        rule_resource = rule.get('resource', '')

        # Match action (supports glob patterns like "fs.*")
        action_matches = fnmatch(action, rule_action)

        # Match resource (supports glob patterns like "/workspace/**")
        # Note: ** is not standard fnmatch, need to handle it
        resource_matches = self._match_resource_pattern(resource, rule_resource)

        return action_matches and resource_matches

    def _match_resource_pattern(self, resource: str, pattern: str) -> bool:
        """
        Match resource against pattern with ** support.

        Converts glob pattern to regex:
        - ** matches any number of path components
        - * matches anything except /
        - Literal characters match themselves

        Args:
            resource: Resource path
            pattern: Pattern (supports * and **)

        Returns:
            True if matches
        """
        if pattern == '*':
            return True

        # Convert glob pattern to regex
        # Escape special regex chars except * and /
        regex_pattern = pattern

        # Escape regex special characters (except * which we handle below)
        # Note: Don't escape backslash in the pattern itself, as patterns shouldn't contain it
        for char in ['.', '+', '?', '^', '$', '(', ')', '[', ']', '{', '}', '|']:
            regex_pattern = regex_pattern.replace(char, '\\' + char)

        # Replace ** with regex for "any path components" (.*)
        # Must do this before replacing single *
        regex_pattern = regex_pattern.replace('**', '{{DOUBLE_STAR}}')

        # Replace single * with regex for "anything except /" ([^/]*)
        regex_pattern = regex_pattern.replace('*', '[^/]*')

        # Now replace our placeholder for ** with .*
        regex_pattern = regex_pattern.replace('{{DOUBLE_STAR}}', '.*')

        # Anchor the pattern
        regex_pattern = '^' + regex_pattern + '$'

        try:
            return bool(re.match(regex_pattern, resource))
        except re.error:
            # If regex compilation fails, fall back to exact match
            return resource == pattern

    def list_principal_permissions(self, principal: str) -> list[dict]:
        """
        List all permissions for a principal.

        Args:
            principal: Principal identifier

        Returns:
            List of allow rules
        """
        if principal not in self._principal_roles:
            return []

        roles = self._principal_roles[principal].get('roles', [])
        permissions = []

        for role in roles:
            if role in self._role_permissions:
                permissions.extend(self._role_permissions[role])

        return permissions
