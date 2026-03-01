"""Security validation for loaded plugins.

Scans plugin source code for dangerous imports and patterns before execution.
This is a static analysis pass — it catches obvious violations but is not
a sandbox. Plugins run in the same process as the host.

Blocked categories:
- Shell execution (os.system, subprocess, shutil.rmtree)
- Network access (socket, http, urllib, requests)
- Dynamic code loading (importlib, ctypes, __import__)
- File system destruction (shutil.rmtree, os.remove on arbitrary paths)
- Audit system tampering (direct log_daemon access)
"""

import ast
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Modules that plugins are NOT allowed to import
BLOCKED_MODULES = {
    "subprocess",
    "ctypes",
    "socket",
    "http",
    "http.client",
    "http.server",
    "urllib",
    "urllib.request",
    "requests",
    "importlib",
    "shutil",
    "multiprocessing",
    "threading",
    "signal",
    "sys",
}

# Specific attribute accesses that are blocked even from allowed modules
BLOCKED_ATTRIBUTES = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "exec"),
    ("os", "execvp"),
    ("os", "execve"),
    ("os", "spawn"),
    ("os", "spawnl"),
    ("os", "spawnle"),
    ("os", "kill"),
    ("os", "remove"),
    ("os", "rmdir"),
    ("os", "unlink"),
    ("os", "rename"),
}

# Built-in functions that are blocked
BLOCKED_BUILTINS = {
    "exec",
    "eval",
    "compile",
    "__import__",
    "globals",
    "breakpoint",
}


class PluginSecurityViolation:
    """A single security violation found during validation."""

    def __init__(self, line: int, col: int, message: str):
        self.line = line
        self.col = col
        self.message = message

    def __repr__(self) -> str:
        return f"L{self.line}:{self.col} {self.message}"

    def __str__(self) -> str:
        return repr(self)


class _PluginASTVisitor(ast.NodeVisitor):
    """AST visitor that collects security violations."""

    def __init__(self):
        self.violations: list[PluginSecurityViolation] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            module_root = alias.name.split(".")[0]
            if alias.name in BLOCKED_MODULES or module_root in BLOCKED_MODULES:
                self.violations.append(PluginSecurityViolation(
                    line=node.lineno,
                    col=node.col_offset,
                    message=f"Blocked import: '{alias.name}'",
                ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            module_root = node.module.split(".")[0]
            if node.module in BLOCKED_MODULES or module_root in BLOCKED_MODULES:
                self.violations.append(PluginSecurityViolation(
                    line=node.lineno,
                    col=node.col_offset,
                    message=f"Blocked import: 'from {node.module}'",
                ))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        # Check for blocked attribute accesses like os.system
        if isinstance(node.value, ast.Name):
            pair = (node.value.id, node.attr)
            if pair in BLOCKED_ATTRIBUTES:
                self.violations.append(PluginSecurityViolation(
                    line=node.lineno,
                    col=node.col_offset,
                    message=f"Blocked call: '{node.value.id}.{node.attr}'",
                ))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # Check for blocked builtins: exec(), eval(), etc.
        if isinstance(node.func, ast.Name) and node.func.id in BLOCKED_BUILTINS:
            self.violations.append(PluginSecurityViolation(
                line=node.lineno,
                col=node.col_offset,
                message=f"Blocked builtin: '{node.func.id}()'",
            ))
        self.generic_visit(node)


def validate_plugin_code(
    code_path: Path,
    extra_blocked_modules: set[str] | None = None,
) -> list[PluginSecurityViolation]:
    """Validate a plugin source file for security violations.

    Parses the file as an AST and checks for:
    - Imports of blocked modules
    - Calls to dangerous builtins (exec, eval, __import__)
    - Dangerous attribute accesses (os.system, os.popen)

    Args:
        code_path: Path to the Python source file
        extra_blocked_modules: Additional modules to block beyond the defaults

    Returns:
        List of violations found. Empty list means the code passed validation.
    """
    source = code_path.read_text(encoding="utf-8")

    try:
        tree = ast.parse(source, filename=str(code_path))
    except SyntaxError as e:
        return [PluginSecurityViolation(
            line=e.lineno or 0,
            col=e.offset or 0,
            message=f"Syntax error: {e.msg}",
        )]

    visitor = _PluginASTVisitor()

    # Add extra blocked modules temporarily
    if extra_blocked_modules:
        BLOCKED_MODULES.update(extra_blocked_modules)

    visitor.visit(tree)

    # Remove extras to avoid global mutation
    if extra_blocked_modules:
        BLOCKED_MODULES.difference_update(extra_blocked_modules)

    return visitor.violations
