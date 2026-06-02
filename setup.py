"""nanobot-mg 一键安装脚本"""
import subprocess
import sys
from pathlib import Path

MIRRORS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.douban.com/simple",
    None,  # PyPI 官方
]


def _is_pep668(text):
    return "externally-managed-environment" in text or "PEP 668" in text


def _install(mirror):
    """Try pip install. If PEP 668 blocks, retry with --break-system-packages."""
    root = Path(__file__).resolve().parent
    base = [sys.executable, "-m", "pip", "install", "--user"]
    if mirror:
        base += ["-i", mirror]
    base += ["-e", str(root)]

    result = subprocess.run(base, capture_output=True, text=True)
    if result.returncode == 0:
        return 0

    stderr = (result.stderr or "") + (result.stdout or "")
    if _is_pep668(stderr):
        retry = base.copy()
        retry.insert(retry.index("--user") + 1, "--break-system-packages")
        result = subprocess.run(retry)

    return result.returncode


def main():
    print("正在安装 nanobot-mg 依赖...")
    for mirror in MIRRORS:
        label = mirror or "PyPI 官方"
        if _install(mirror) == 0:
            print("安装完成！")
            return 0
        print(f"镜像 {label} 失败，尝试下一个...")
    return 1


if __name__ == "__main__":
    sys.exit(main())
