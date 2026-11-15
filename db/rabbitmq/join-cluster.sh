#!/bin/bash
# Join second RabbitMQ node to the cluster
set -e

echo "Waiting for rabbitmq (primary) to be ready..."
until rabbitmqctl -n rabbit@rabbitmq await_startup 2>/dev/null; do
  # fallback: just sleep and check port
  sleep 3
  if rabbitmq-diagnostics -q ping 2>/dev/null; then
    break
  fi
done

# Даём primary немного времени подняться полностью
sleep 5

echo "Stopping rabbitmq app on this node to join cluster..."
rabbitmqctl stop_app || true

echo "Resetting node (clean join)..."
rabbitmqctl reset || true

echo "Joining cluster rabbit@rabbitmq ..."
# Имя узла primary = rabbit@<hostname>, hostname задан как rabbitmq
rabbitmqctl join_cluster rabbit@rabbitmq || {
  echo "join_cluster failed, trying again after delay..."
  sleep 5
  rabbitmqctl join_cluster rabbit@rabbitmq
}

echo "Starting app..."
rabbitmqctl start_app

echo "Cluster status:"
rabbitmqctl cluster_status

echo "RabbitMQ secondary node joined the cluster successfully."
