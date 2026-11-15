#!/bin/bash
set -e

# Ждём primary
until pg_isready -h postgres-messages-primary -U msg_user -d messages_db; do
  echo "Waiting for primary..."
  sleep 2
done

# Если данные уже есть — просто запускаем
if [ -f /var/lib/postgresql/data/PG_VERSION ]; then
  echo "Replica data exists, starting postgres..."
  exec docker-entrypoint.sh postgres -c hot_standby=on
fi

echo "Taking base backup from primary..."
export PGPASSWORD=repl_pass

# Очищаем директорию (на всякий случай)
rm -rf /var/lib/postgresql/data/*

pg_basebackup -h postgres-messages-primary -D /var/lib/postgresql/data -U replicator -v -P -R -X stream -C -S replica_1

echo "Base backup done. Starting replica..."
# -R создаёт standby.signal и primary_conninfo
exec docker-entrypoint.sh postgres -c hot_standby=on
