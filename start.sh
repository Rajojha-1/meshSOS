#!/bin/bash
set -e

echo "=== Stopping existing container ==="
sudo docker rm -f meshsos-backend 2>/dev/null || true

echo "=== Building Docker image with all dependencies ==="
sudo docker build -t meshsos .

echo "=== Running MeshSOS Backend ==="
sudo docker run -d -p 8000:8000 --restart always --name meshsos-backend meshsos

echo "=== Waiting 3 seconds for server startup ==="
sleep 3

echo "=== Testing API Endpoint ==="
curl http://localhost:8000/api/stats

echo ""
echo "=== Success! Docker status ==="
sudo docker ps
