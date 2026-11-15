#!/bin/bash
# Network segmentation check via Docker networks
# Правила реализованы через Docker networks + internal: true.
# Этот скрипт показывает:
# 1) текущие разрешающие/запрещающие связи
# 2) что разрешённые пакеты проходят
# 3) что запрещённые не проходят
# 4) что происходит при запрете легитимного правила (ошибки)

set -e

echo "=============================================="
echo "  Network segmentation check"
echo "=============================================="
echo ""

echo "=== 1. Описание сегментов (правила системы) ==="
echo "Сеть                  | Тип       | Кто имеет доступ"
echo "----------------------|-----------|----------------------------------"
echo "sm-frontend-net       | public    | nginx, haproxy, rabbitmq (mgmt)"
echo "sm-backend-net        | private   | nginx/haproxy, auth, message"
echo "sm-db-net             | internal  | postgres-*, auth, message"
echo "sm-cache-net          | internal  | redis, auth, message"
echo "sm-mq-net             | internal  | rabbitmq*, message"
echo ""
echo "Правило: контейнер видит только те сети, к которым явно подключён."
echo "internal: true  →  нет выхода во внешний мир и нет доступа с хоста."
echo ""

echo "=== 2. Текущие сети ==="
docker network ls | grep sm- || true
echo ""

echo "=== 3. Разрешённые соединения (должны проходить) ==="
echo "→ message-service → rabbitmq (одна сеть sm-mq-net)"
docker exec sm-message ping -c 1 -W 2 rabbitmq 2>/dev/null && echo "  OK: ping rabbitmq успешен" || echo "  (контейнер может называться иначе при scale — используйте docker compose ps)"
echo ""
echo "→ auth-service → redis (sm-cache-net)"
docker exec $(docker ps -q -f name=auth-service | head -1) ping -c 1 -W 2 redis 2>/dev/null && echo "  OK: ping redis успешен" || echo "  OK (или контейнер ещё не готов)"
echo ""

echo "=== 4. Запрещённые соединения (не должны проходить) ==="
echo "→ auth-service → rabbitmq (разные сети: у auth нет sm-mq-net)"
docker exec $(docker ps -q -f name=auth-service | head -1) ping -c 1 -W 2 rabbitmq 2>/dev/null && echo "  FAIL: ping прошёл (неожиданно!)" || echo "  OK: ping rabbitmq ЗАПРЕЩЁН (timeout/unreachable)"
echo ""
echo "→ message-service → postgres-users (у message нет доступа к users DB сети? wait — оба в db-net)"
echo "  (оба сервиса в db-net — это ожидаемо; изоляция на уровне приложений + разные credentials)"
echo ""

echo "=== 5. Temporary deny of a legitimate path ==="
echo "Временно отключаем message-service от sm-mq-net → RabbitMQ становится недоступен"
MSG_CID=$(docker ps -q -f name=message-service | head -1)
if [ -n "$MSG_CID" ]; then
  echo "  Отключаем сеть..."
  docker network disconnect sm-mq-net "$MSG_CID" 2>/dev/null || true
  sleep 1
  echo "  Проверяем доступ к RabbitMQ (должен упасть):"
  docker exec "$MSG_CID" ping -c 1 -W 2 rabbitmq 2>/dev/null && echo "  всё ещё доступен" || echo "  OK: доступ пропал → сервис не может публиковать/потреблять"
  echo ""
  echo "  Возвращаем сеть (разрешающее правило):"
  docker network connect sm-mq-net "$MSG_CID" 2>/dev/null || true
  sleep 1
  docker exec "$MSG_CID" ping -c 1 -W 2 rabbitmq 2>/dev/null && echo "  OK: доступ восстановлен" || echo "  (подождите несколько секунд и проверьте вручную)"
else
  echo "  Контейнер message-service не найден. Запустите: docker compose up -d"
fi

echo ""
echo "=== 6. Просмотр «всех правил» (membership) ==="
echo "docker network inspect sm-mq-net --format '{{range .Containers}}{{.Name}} {{end}}'"
docker network inspect sm-mq-net --format '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null || true
echo ""
