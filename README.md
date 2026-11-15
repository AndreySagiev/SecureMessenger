# SecureMessenger

Микросервисный мессенджер с аутентификацией.

---

## Быстрый старт

```bash
cd secure-messenger

# Поднять всё (первый запуск 1–3 минуты: сборка + репликация Postgres + join кластера RabbitMQ)
docker compose up -d --build

# Запуск с несколькими репликами сервисов
docker compose up -d --build --scale auth-service=2 --scale message-service=2

# Статус
docker compose ps

# Открыть
# Frontend:          http://localhost:8080
# RabbitMQ Mgmt:     http://localhost:15672  (mq_user / mq_pass)
# HAProxy Stats:     http://localhost:8404/stats  (admin / admin)
# Locust:            http://localhost:8089
```

---

## Архитектура и сети

| Сеть              | Тип      | Назначение                     | Кто подключён                          |
|-------------------|----------|--------------------------------|----------------------------------------|
| `sm-frontend-net` | public   | Публичный вход                 | nginx, haproxy, rabbitmq (mgmt)        |
| `sm-backend-net`  | private  | Общение сервисов               | haproxy, auth-service, message-service |
| `sm-db-net`       | **internal** | Базы данных                 | postgres-*, auth, message              |
| `sm-cache-net`    | **internal** | Redis                        | redis, auth, message                   |
| `sm-mq-net`       | **internal** | RabbitMQ cluster             | rabbitmq, rabbitmq2, message-service   |

**Сетевые правила (Docker networks):**
- Контейнер может общаться **только** с теми, кто в тех же сетях.
- `internal: true` — нет маршрута наружу и с хоста.
- Auth **не** имеет `sm-mq-net` → не может достучаться до RabbitMQ.
- Message **не** имеет credentials от users DB (разные БД).

Проверка изоляции:

```bash
chmod +x scripts/*.sh
./scripts/demo_network_isolation.sh
```

Скрипт показывает:
1. Сегменты сетей и membership.
2. Разрешённые соединения проходят.
3. Запрещённые — не проходят.
4. Временное отключение сети → сервис теряет доступ, затем восстановление.

---

## Основные API (через gateway http://localhost:8080)

**Auth** (`/api/auth/...`):
- `POST /register` — регистрация
- `POST /login` — JWT
- `POST /logout` — blacklist в Redis
- `GET /me` — текущий пользователь

**Messages** (`/api/messages/...`):
- `POST /conversations` — создать чат
- `GET /conversations` — список
- `POST /messages` — отправить (БД + publish в RabbitMQ + cache Redis)
- `GET /conversations/{id}/messages` — история
- `DELETE /messages/{id}` — soft-delete (+ publish)
- `GET /mq/stats` — статистика потребления (`processed_events`)

---

## БД, кэш и брокер

### PostgreSQL
- Отдельные БД: `users_db` и `messages_db`
- SQL-схемы: `db/users/init.sql`, `db/messages/init.sql`
- CRUD: create / read / update / soft-delete

### Redis
- Auth: JWT blacklist + сессии (`setex`, `exists`, `sadd`…)
- Message: кэш `last_msg` + Pub/Sub для WebSocket между репликами

### RabbitMQ — публикация и потребление
- Publish при создании/удалении сообщения (`publish_message_event`)
- Фоновый consumer в message-service читает очередь `message_events`
- Логи consumer:
  ```bash
  docker compose logs message-service | grep -i consumed
  ```
- Эндпоинт `/mq/stats` возвращает `processed_events` и `last_event`

---

## Надёжность

### PostgreSQL (primary + replica)
```bash
# Backup
./scripts/backup.sh

# Restore
./scripts/restore.sh ./backups/messages_db_XXXX.sql

# Failover (primary down → promote replica)
./scripts/promote_replica.sh
```
После promote message-service всё ещё указывает на старый primary hostname.  
Полный возврат: `docker compose down -v && docker compose up -d --build`.

### RabbitMQ HA Cluster (2 узла)
- `rabbitmq` + `rabbitmq2` образуют кластер (Erlang cookie + `join_cluster`)
- Durable очереди
- Клиент использует `aio_pika.RobustConnection` → переподключение при сбое узла

```bash
docker exec sm-rabbitmq rabbitmqctl cluster_status
./scripts/demo_mq_failover.sh
./scripts/backup_mq.sh
```

### Параметры обслуживания
| Метрика | Значение | Обоснование |
|---------|----------|-------------|
| **SLO** | 99.9 % availability | Допустимо ~8.7 ч простоя в год |
| **MTTR** | ≤ 15 мин | auto-failover + ручная проверка |
| **MTBF** | ~720 ч | оценка на основе типичных сбоев |
| **RPO** | 5 мин | частота WAL / периодических бэкапов |
| **RTO** | 10–15 мин | время promote + переключение трафика |

---

## Балансировка и нагрузка

1. Несколько реплик: `--scale auth-service=2 --scale message-service=2`
2. HAProxy: round-robin + health-check + server-template (DNS discovery)
   - Stats: http://localhost:8404/stats (admin/admin)
3. Locust: http://localhost:8089
   - Users: 20–50, Spawn rate: 5, Host: `http://nginx`
4. Resource limits (CPU 0.5 / Memory 256M) на сервисах

```bash
docker compose up -d --build --scale auth-service=2 --scale message-service=2
```

---

## Проверка работоспособности

1. `docker compose up -d --build --scale auth-service=2 --scale message-service=2`
2. `docker compose ps` + `docker network ls | grep sm-`
3. Frontend http://localhost:8080 — регистрация, чат, сообщения
4. БД / кэш / брокер:
   - `docker exec -it sm-postgres-messages-primary psql -U msg_user -d messages_db`
   - `docker exec sm-redis redis-cli KEYS '*'`
   - RabbitMQ Management UI + логи consumer
   - `/api/messages/mq/stats` — `processed_events` растёт
5. Сети: `./scripts/demo_network_isolation.sh`
6. Надёжность:
   - `./scripts/backup.sh`
   - `./scripts/promote_replica.sh`
   - `./scripts/demo_mq_failover.sh` + `rabbitmqctl cluster_status`
7. Балансировка: HAProxy stats + Locust (30 users)

---

## Структура

```
secure-messenger/
├── docker-compose.yml          # сервисы, сети, volumes
├── nginx/                      # gateway + static
├── haproxy/                    # балансировщик
├── frontend/                   # HTML/JS/CSS
├── services/
│   ├── auth/                   # FastAPI + users DB + Redis (JWT)
│   └── message/                # FastAPI + messages DB + Redis + RabbitMQ
├── db/
│   ├── users/init.sql
│   ├── messages/               # init + replication scripts
│   └── rabbitmq/join-cluster.sh
├── scripts/
│   ├── backup.sh / restore.sh / promote_replica.sh
│   ├── backup_mq.sh
│   ├── demo_network_isolation.sh
│   └── demo_mq_failover.sh
└── loadtest/                   # Locust
```


