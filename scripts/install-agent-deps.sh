#!/usr/bin/env bash
# Install agent extras with mirror fallback for China networks.
# Usage: bash scripts/install-agent-deps.sh
set -e

pip install "nanobot-ai[agent]" -i https://pypi.tuna.tsinghua.edu.cn/simple && exit 0
pip install "nanobot-ai[agent]" -i https://mirrors.aliyun.com/pypi/simple && exit 0
pip install "nanobot-ai[agent]" -i https://pypi.douban.com/simple && exit 0
pip install "nanobot-ai[agent]" -i https://pypi.org/simple && exit 0

echo "All mirrors failed" >&2
exit 1
