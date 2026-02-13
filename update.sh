#!/bin/bash

# Stop execution if any command fails
set -e

echo ">> [1/3] Pulling latest code from Git..."
git pull

echo ">> [2/3] Rebuilding and restarting containers..."
# --build ensures any changes to Dockerfile or requirements are picked up
# -d runs in detached mode (background)
docker-compose up -d --build

echo ">> [3/3] Pruning unused images (optional cleanup)..."
docker image prune -f

echo ">> SUCCESS: System updated and running."
