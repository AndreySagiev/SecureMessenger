#!/bin/bash
# RabbitMQ cluster failover check
set -e

echo "=============================================="
echo "  RabbitMQ cluster failover"
echo "=============================================="
echo ""

echo "=== 1. Статус кластера (до аварии) ==="
docker exec sm-rabbitmq rabbitmqctl cluster_status 2>/dev/null || echo "Primary недоступен"
echo ""

echo "=== 2. Имитация аварии primary-узла ==="
echo "Останавливаем sm-rabbitmq..."
docker stop sm-rabbitmq || true
sleep 3

echo ""
echo "=== 3. Проверяем, что второй узел жив ==="
docker exec sm-rabbitmq2 rabbitmqctl cluster_status 2>/dev/null || {
  echo "Второй узел тоже недоступен — проверьте логи: docker logs sm-rabbitmq2"
  exit 1
}

echo ""
echo "=== 4. Клиент (message-service) использует RobustConnection ==="
echo "aio-pika RobustConnection автоматически переподключается к доступному узлу."
echo "Проверьте логи message-service — не должно быть длительных ошибок."
echo "  docker compose logs -f message-service | head -20"
echo ""
echo "Можно отправить новое сообщение через UI (http://localhost:8080) —"
echo "оно должно опубликоваться, когда соединение восстановится на rabbitmq2"
echo "(для полного auto-discovery в проде используют list of nodes в URL)."
echo ""

echo "=== 5. Восстановление primary ==="
echo "Запускаем primary обратно..."
docker start sm-rabbitmq
sleep 10
echo "Статус после восстановления:"
docker exec sm-rabbitmq rabbitmqctl cluster_status 2>/dev/null || true

echo ""
echo "и что /mq/stats продолжает увеличивать processed_events."
