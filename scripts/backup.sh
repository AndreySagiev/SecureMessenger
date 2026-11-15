#!/bin/bash
# Backup messages database
set -e
BACKUP_DIR=${1:-./backups}
mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="messages_db_${TIMESTAMP}.sql"

echo "Creating backup of messages_db..."
docker exec sm-postgres-messages-primary pg_dump -U msg_user -d messages_db > "$BACKUP_DIR/$FILENAME"
echo "Backup saved to $BACKUP_DIR/$FILENAME"

