#!/usr/bin/env bash
# nanobot-mg 一键安装（Linux / Mac）
# bash setup.sh 即可，不用操心镜像配置
set -e

echo "正在安装 nanobot-mg 依赖..."

pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple && exit 0
pip install -e . -i https://mirrors.aliyun.com/pypi/simple && exit 0
pip install -e . -i https://pypi.douban.com/simple && exit 0
pip install -e . && exit 0

echo "安装失败，请检查网络后重试" >&2
exit 1
