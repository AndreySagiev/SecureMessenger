#!/bin/bash
set -e

# Создаём пользователя репликации (выполняется один раз при инициализации тома)
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'replicator') THEN
            CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'repl_pass';
        END IF;
    END
    \$\$;

    -- Разрешаем репликацию (для простоты демо — все)
    -- В реальном проде — более жёсткие pg_hba
EOSQL

echo "Primary setup for replication completed."
