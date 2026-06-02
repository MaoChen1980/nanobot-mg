#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# 新 macOS 可能没有 Python，自动走 Homebrew 安装
if ! command -v python3 &>/dev/null; then
    echo "未检测到 Python，正在安装..."
    if ! command -v brew &>/dev/null; then
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    brew install python
fi

exec python3 setup.py
