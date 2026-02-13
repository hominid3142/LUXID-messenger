#!/bin/bash

# Stop execution if any command fails
set -e

echo ">> [1/3] Pulling latest code from Git..."
git pull

# Prune to free up space/memory before build
docker system prune -af

echo ">> [2/3] Rebuilding and restarting containers..."
# --build ensures any changes to Dockerfile or requirements are picked up
# -d runs in detached mode (background)
# Limit parallelism to reduce memory usage during build
COMPOSE_PARALLEL_LIMIT=1 docker compose up -d --build

echo ">> [3/3] Pruning unused images (optional cleanup)..."
docker image prune -f

echo ">> SUCCESS: System updated and running."
