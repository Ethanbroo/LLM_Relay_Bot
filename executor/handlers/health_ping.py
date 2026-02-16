"""Handler for system.health_ping action.

Minimal health check that echoes input.
Used to verify execution engine is working.
"""

from typing import Any
from executor.handlers import HandlerError


class HealthPingHandler:
    """Handler for system.health_ping action."""

    def execute(self, validated_action: dict, sandbox: Any) -> dict:
        """Execute health ping.

        Args:
            validated_action: ValidatedAction with system.health_ping payload
            sandbox: Sandbox instance (not used for health ping)

        Returns:
            Artifacts dict with echo field

        Raises:
            HandlerError: If execution fails
        """
        try:
            # Extract payload
            payload = validated_action.get("sanitized_payload", {})
            echo_value = payload.get("echo")

            # Build artifacts
            artifacts = {
                "echo": echo_value,
                "status": "healthy"
            }

            return artifacts

        except Exception as e:
            raise HandlerError(f"Health ping failed: {e}") from e
