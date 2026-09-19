#!/usr/bin/env bash
# 一键启动：PostgreSQL + Django(8000) + Vite(5173)
set -e
export PATH=/home/node/miniforge3/bin:/home/node/.local/bin:$PATH

pg_ctl -D /home/node/pgdata -l /home/node/pg.log -o "-p 5432 -k /tmp" start || true

cd /workspace/backend
python3 manage.py migrate
python3 manage.py seed_demo
python3 manage.py runserver 0.0.0.0:8000 &
DJANGO_PID=$!

cd /workspace/frontend
npm run dev &
VITE_PID=$!

echo "后端: http://127.0.0.1:8000/api/orders/"
echo "前端: http://127.0.0.1:5173/"
wait $DJANGO_PID $VITE_PID
