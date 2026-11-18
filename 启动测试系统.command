#!/bin/zsh
set -eu
cd "$(dirname "$0")"
TEST_PYTHON="/Users/oule/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [[ ! -x "$TEST_PYTHON" ]]; then TEST_PYTHON="$(command -v python3)"; fi
if curl -fsS http://127.0.0.1:8765/health 2>/dev/null | "$TEST_PYTHON" -c 'import sys,json; assert json.load(sys.stdin)["data"]["mode"]=="LOCAL_TEST"' 2>/dev/null; then
  echo '测试系统已在运行，正在打开浏览器。'
  open http://127.0.0.1:8765
  exit 0
fi
"$TEST_PYTHON" -c 'import cryptography,openpyxl' || { echo '缺少依赖，请按README安装。'; read; exit 1; }
mkdir -p data
nohup "$TEST_PYTHON" server.py --port 8765 > data/server.log 2>&1 < /dev/null &
echo $! > data/server.pid
for TEST_ATTEMPT in {1..20}; do
  if curl -fsS http://127.0.0.1:8765/health >/dev/null 2>&1; then
    open http://127.0.0.1:8765
    echo '已打开测试系统。账号 admin，测试密码 Demo2026!'
    exit 0
  fi
  sleep 0.25
done
echo '启动未成功，请查看 data/server.log。'
read
