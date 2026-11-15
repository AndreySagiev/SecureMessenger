#!/bin/bash
# Backup RabbitMQ definitions
set -e
BACKUP_DIR=${1:-./backups}
mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="rabbitmq_definitions_${TIMESTAMP}.json"

echo "Exporting RabbitMQ definitions..."
docker exec sm-rabbitmq rabbitmqctl export_definitions - > "$BACKUP_DIR/$FILENAME" 2>/dev/null || \
  docker exec sm-rabbitmq rabbitmqadmin export "$BACKUP_DIR/$FILENAME" 2>/dev/null || {
    echo "Trying alternative method..."
    docker exec sm-rabbitmq rabbitmqctl list_exchanges name type durable > "$BACKUP_DIR/exchanges_${TIMESTAMP}.txt" || true
    docker exec sm-rabbitmq rabbitmqctl list_queues name durable messages > "$BACKUP_DIR/queues_${TIMESTAMP}.txt" || true
    echo "Partial backup of exchanges/queues saved."
    exit 0
  }

echo "Definitions saved to $BACKUP_DIR/$FILENAME"
echo "Restore (пример): docker exec -i sm-rabbitmq rabbitmqctl import_definitions - < $BACKUP_DIR/$FILENAME"
