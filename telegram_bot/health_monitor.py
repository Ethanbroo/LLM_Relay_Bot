"""Docker container health monitoring with Telegram alerts.

Periodically checks Docker container stats and sends Telegram alerts
when resource usage exceeds thresholds.

IMPORTANT: docker stats without --no-stream runs forever (live-updating
display). ALWAYS use --no-stream for programmatic access.

Alert cooldown: Without cooldown, a container hovering at 81% RAM sends
an alert every 5 minutes — 288 alerts per day. The 30-minute cooldown
means max 2 alerts per hour per issue.
"""

import asyncio
import json
import logging
import time

logger = logging.getLogger(__name__)

# Alert thresholds
RAM_ALERT_THRESHOLD = 0.80     # 80% of container memory limit
CPU_ALERT_THRESHOLD = 0.90     # 90% CPU sustained
CHECK_INTERVAL = 300           # Check every 5 minutes
ALERT_COOLDOWN = 1800          # Don't re-alert for same issue within 30 min


class HealthMonitor:
    """Monitors Docker containers and sends alerts via Telegram."""

    def __init__(self, bot, owner_id: int, redis_client=None):
        self.bot = bot
        self.owner_id = owner_id
        self.redis = redis_client
        self._last_alerts: dict[str, float] = {}
        self._task: asyncio.Task | None = None

    def start(self):
        """Start the background monitoring loop."""
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Health monitor started")

    async def stop(self):
        """Stop the monitoring loop."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _monitor_loop(self):
        """Background loop: check container stats every CHECK_INTERVAL seconds."""
        while True:
            try:
                await self._check_containers()
                await asyncio.sleep(CHECK_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Health monitor error: %s", e)
                await asyncio.sleep(CHECK_INTERVAL)

    async def _check_containers(self):
        """Check all container stats via docker stats --no-stream."""
        proc = await asyncio.create_subprocess_exec(
            "docker", "stats", "--no-stream", "--format",
            '{"name":"{{.Name}}","cpu":"{{.CPUPerc}}","mem":"{{.MemPerc}}","mem_usage":"{{.MemUsage}}"}',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        if proc.returncode != 0:
            return

        for line in stdout.decode().strip().split("\n"):
            if not line.strip():
                continue
            try:
                stats = json.loads(line)
            except json.JSONDecodeError:
                continue

            name = stats.get("name", "")
            cpu_str = stats.get("cpu", "0%").replace("%", "")
            mem_str = stats.get("mem", "0%").replace("%", "")

            try:
                cpu_pct = float(cpu_str) / 100
                mem_pct = float(mem_str) / 100
            except ValueError:
                continue

            if mem_pct >= RAM_ALERT_THRESHOLD:
                await self._alert(
                    f"ram:{name}",
                    f"\u26a0\ufe0f RAM Alert: {name}\n"
                    f"Memory: {mem_pct:.0%} (threshold: {RAM_ALERT_THRESHOLD:.0%})\n"
                    f"Usage: {stats.get('mem_usage', 'N/A')}"
                )

            if cpu_pct >= CPU_ALERT_THRESHOLD:
                await self._alert(
                    f"cpu:{name}",
                    f"\u26a0\ufe0f CPU Alert: {name}\n"
                    f"CPU: {cpu_pct:.0%} (threshold: {CPU_ALERT_THRESHOLD:.0%})"
                )

    async def _alert(self, alert_key: str, message: str):
        """Send a Telegram alert, respecting cooldown."""
        now = time.time()
        last = self._last_alerts.get(alert_key, 0)

        if now - last < ALERT_COOLDOWN:
            return

        self._last_alerts[alert_key] = now

        try:
            await self.bot.send_message(chat_id=self.owner_id, text=message)
            logger.info("Alert sent: %s", alert_key)
        except Exception as e:
            logger.error("Failed to send alert: %s", e)

    async def get_status(self) -> str:
        """Get current container status for /health command."""
        proc = await asyncio.create_subprocess_exec(
            "docker", "stats", "--no-stream", "--format",
            "{{.Name}}\t{{.CPUPerc}}\t{{.MemPerc}}\t{{.MemUsage}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return "Unable to read container stats"

        lines = ["Container       CPU    RAM    Usage"]
        lines.append("\u2500" * 45)
        for line in stdout.decode().strip().split("\n"):
            if line.strip():
                lines.append(line)

        return "\n".join(lines)
