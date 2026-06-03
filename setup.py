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


def _try_install(extra_flags, mirror):
    root = Path(__file__).resolve().parent
    cmd = [sys.executable, "-m", "pip", "install", *extra_flags, "-e", str(root)]
    if mirror:
        cmd += ["-i", mirror]
    return subprocess.run(cmd, capture_output=True, text=True)


def _install(mirror):
    """Try pip install with automatic fallback.

    Strategy:
    1. Normal install → entry point lands in PATH (e.g. /opt/homebrew/bin/)
    2. Permission denied → --user (entry point may not be in PATH)
    3. PEP 668 → --break-system-packages
    """
    # Strategy 1: normal — entry point goes to system bin (already in PATH)
    result = _try_install([], mirror)
    if result.returncode == 0:
        return 0

    stderr = (result.stderr or "") + (result.stdout or "")

    # Strategy 2: permission error → user install
    if "Permission denied" in stderr or "Operation not permitted" in stderr:
        result = _try_install(["--user"], mirror)
        if result.returncode == 0:
            return 0
        stderr = (result.stderr or "") + (result.stdout or "")

    # Strategy 3: PEP 668 → --break-system-packages
    if _is_pep668(stderr):
        result = _try_install(["--break-system-packages"], mirror)
        if result.returncode == 0:
            return 0

    return result.returncode


def _ensure_entry_point():
    """Verify 'nanobot' is usable after install; warn if not."""
    import shutil
    path = shutil.which("nanobot")
    if not path:
        print("\n⚠️  安装完成，但 'nanobot' 命令不在 PATH 中。")
        print("   请将以下目录加入 PATH：")
        candidate = Path.home() / ".local" / "bin"
        if candidate.exists():
            print(f"   export PATH=\"{candidate}:$PATH\"")
        candidate = Path.home() / "Library" / "Python" / f"{sys.version_info.major}.{sys.version_info.minor}" / "bin"
        if candidate.exists():
            print(f"   export PATH=\"{candidate}:$PATH\"")
        return False

    # Quick smoke test
    result = subprocess.run([path, "--help"], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"\n⚠️  找到 nanobot 但运行异常：{path}")
        return False

    print(f"   路径：{path}")
    return True


def main():
    print("正在安装 nanobot-mg 依赖...")
    for mirror in MIRRORS:
        label = mirror or "PyPI 官方"
        if _install(mirror) == 0:
            print("安装完成！")
            _ensure_entry_point()
            return 0
        print(f"镜像 {label} 失败，尝试下一个...")
    return 1


if __name__ == "__main__":
    sys.exit(main())
