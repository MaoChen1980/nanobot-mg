#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# 找 Python 3.10+（nanobot 需要）
if command -v /opt/homebrew/bin/python3 &>/dev/null; then
    PY=/opt/homebrew/bin/python3
elif command -v python3 &>/dev/null; then
    PY=python3
else
    echo "未检测到 Python，正在安装..."
    if ! command -v brew &>/dev/null; then
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    brew install python
    PY=/opt/homebrew/bin/python3
fi

# 检查版本
VER=$("$PY" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "使用 Python $VER：$PY"

exec "$PY" setup.py "$@"
