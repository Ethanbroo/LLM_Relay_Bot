#!/bin/bash
#
# Creates a compressed backup of workspace and redis data.
# Stores backups in /opt/relay-bot/backups/ with 7-day retention.
# Run via cron daily at 3 AM.
#
# Setup:
#   chmod +x /opt/relay-bot/scripts/backup.sh
#   crontab: 0 3 * * * /opt/relay-bot/scripts/backup.sh >> /opt/relay-bot/backups/backup.log 2>&1

BACKUP_DIR="/opt/relay-bot/backups"
DATE=$(date +%Y-%m-%d)
mkdir -p "$BACKUP_DIR"

# Back up the workspace volume.
docker run --rm \
    -v llm-relay_shared-workspace:/data:ro \
    -v "$BACKUP_DIR":/backup \
    alpine tar czf "/backup/workspace-${DATE}.tar.gz" -C /data .

# Back up redis data (trigger a save first).
docker exec redis redis-cli --no-auth-warning -a "$REDIS_PASSWORD" BGSAVE
sleep 2
docker run --rm \
    -v llm-relay_redis-data:/data:ro \
    -v "$BACKUP_DIR":/backup \
    alpine tar czf "/backup/redis-${DATE}.tar.gz" -C /data .

# Delete backups older than 7 days.
find "$BACKUP_DIR" -name "*.tar.gz" -mtime +7 -delete

echo "Backup complete: workspace-${DATE}.tar.gz, redis-${DATE}.tar.gz"
