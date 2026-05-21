#!/bin/bash
# Start Hermes Kanban Dashboard
set -e

cd /Users/reidar/Projectos/hermes-kanban-dashboard

# Ensure deps are installed
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

exec .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8089
