#!/bin/bash
# Promote Postgres replica to primary
# В проде используйте Patroni / Stolon / managed service.

set -e

echo "=== Promote replica to primary ==="
echo "1. Останавливаем primary (имитация аварии)..."
docker stop sm-postgres-messages-primary || true

echo "2. Promote replica..."
# В реальном standby.signal уже есть. Для promote:
docker exec sm-postgres-messages-replica pg_ctl promote -D /var/lib/postgresql/data || \
  docker exec sm-postgres-messages-replica psql -U msg_user -d messages_db -c "SELECT pg_promote();"

echo "3. Проверяем статус..."
docker exec sm-postgres-messages-replica psql -U msg_user -d messages_db -c "SELECT pg_is_in_recovery();"

echo ""
echo "Replica теперь должна быть primary (pg_is_in_recovery = f)."
echo "Для полного восстановления нужно перенастроить бывший primary как новую replica"
echo "и обновить DATABASE_URL в message-service (или использовать VIP / service discovery)."
echo ""
echo "Для возврата к исходному состоянию: docker compose down -v && docker compose up -d"
