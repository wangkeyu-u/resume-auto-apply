#!/usr/bin/env bash
# 简历自动投递助手 - 启动脚本
set -e
cd "$(dirname "$0")"

VENV=/Users/wangkeyu/.workbuddy/binaries/python/envs/default
PY=/Users/wangkeyu/.workbuddy/binaries/python/versions/3.13.12/bin/python3

if [ ! -d "$VENV" ]; then
  "$PY" -m venv "$VENV"
fi
if [ ! -x "$VENV/bin/uvicorn" ]; then
  "$VENV/bin/pip" install -r requirements.txt
fi

export PYTHONPATH="$PWD"
exec "$VENV/bin/uvicorn" app.main:app --host 0.0.0.0 --port 8011 --reload
