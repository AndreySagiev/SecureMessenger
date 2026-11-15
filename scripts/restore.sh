#!/bin/bash
# Restore database from dump
set -e

if [ -z "$1" ]; then
  echo "Usage: $0 <path-to-backup.sql>"
  exit 1
fi

BACKUP_FILE=$1
echo "Restoring $BACKUP_FILE into messages_db (primary)..."
cat "$BACKUP_FILE" | docker exec -i sm-postgres-messages-primary psql -U msg_user -d messages_db
echo "Restore completed."
