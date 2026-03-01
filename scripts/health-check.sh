#!/bin/bash
#
# Checks that all expected containers are running.
# Sends a Telegram alert if any are down.
# Run via cron every 5 minutes.
#
# Setup:
#   chmod +x /opt/relay-bot/scripts/health-check.sh
#   crontab: */5 * * * * cd /opt/relay-bot && source .env && ./scripts/health-check.sh

BOT_TOKEN="${TELEGRAM_BOT_TOKEN}"
CHAT_ID="${TELEGRAM_BOT_OWNER_ID}"
EXPECTED_CONTAINERS=("relay-bot" "redis" "code-server" "nginx" "claude-code")

for container in "${EXPECTED_CONTAINERS[@]}"; do
    status=$(docker inspect --format='{{.State.Status}}' "$container" 2>/dev/null)
    if [ "$status" != "running" ]; then
        curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -d chat_id="${CHAT_ID}" \
            -d text="⚠️ Container ${container} is ${status:-missing}. Check the VPS." \
            > /dev/null
    fi
done

# Disk usage monitoring -- alert if above 80%
DISK_USAGE=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
if [ "$DISK_USAGE" -gt 80 ]; then
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -d chat_id="${CHAT_ID}" \
        -d text="⚠️ Disk usage is ${DISK_USAGE}%. Consider pruning Docker images: docker system prune -f" \
        > /dev/null
fi
