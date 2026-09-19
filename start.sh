#!/usr/bin/env bash
# 一键启动：PostgreSQL(用户态) + Django 后端 + 已构建的 React 前端
set -e
cd "$(dirname "$0")"

PGBIN=/workspace/.pgsql/bin
PGDATA=/workspace/.pgdata

# 1. 启动 PostgreSQL（如未运行）
if ! "$PGBIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; then
  echo "==> 启动 PostgreSQL..."
  "$PGBIN/pg_ctl" -D "$PGDATA" -l /workspace/.pgdata.log -o "-p 5432 -k /tmp" -w start
fi

# 2. 数据库迁移
echo "==> 应用数据库迁移..."
python3 backend/manage.py migrate --run-syncdb -v 0

# 3. 可选：重置演示数据  ./start.sh --seed
if [[ "$1" == "--seed" ]]; then
  echo "==> 重建演示数据..."
  python3 backend/manage.py seed_demo
fi

# 4. 可选：重新构建前端  ./start.sh --build
if [[ "$1" == "--build" || "$2" == "--build" ]]; then
  echo "==> 构建前端..."
  (cd frontend && npm run build)
fi

echo "==> 启动 Django (http://127.0.0.1:8000) ..."
exec python3 backend/manage.py runserver 0.0.0.0:8000
