#!/bin/bash
# Daily backup runner script
# Add to crontab: 0 2 * * * /path/to/backup_runner.sh

set -e

BACKUP_DIR="./backups"
LOG_FILE="$BACKUP_DIR/backup.log"

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

echo "========================================" >> "$LOG_FILE"
echo "Backup started at $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"

# Run backup using docker-compose
cd "$(dirname "$0")"

if command -v docker-compose &> /dev/null; then
    echo "Running backup via docker-compose..." >> "$LOG_FILE"
    docker-compose exec -T db pg_dump -U postgres xsb_db | gzip > "$BACKUP_DIR/xsb_db_backup_$(date +%Y%m%d_%H%M%S).sql.gz" 2>> "$LOG_FILE"
    echo "✅ Backup completed successfully" >> "$LOG_FILE"
elif command -v python3 &> /dev/null; then
    echo "Running backup via Python script..." >> "$LOG_FILE"
    python3 scripts/backup_db.py backup >> "$LOG_FILE" 2>&1
    echo "✅ Backup completed successfully" >> "$LOG_FILE"
else
    echo "❌ Neither docker-compose nor python3 found" >> "$LOG_FILE"
    exit 1
fi

echo "Backup finished at $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
