"""nanobot-mg 一键安装脚本"""
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path

MIRRORS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.douban.com/simple",
    None,  # PyPI 官方
]

# 绝对不往里写的系统目录
_WINDOWS_SYSTEM_DIRS = {"windows", "program files", "program files (x86)"}
_UNIX_SYSTEM_DIRS = {"/bin", "/sbin", "/usr/bin", "/usr/sbin"}


def _check_python_version():
    if sys.version_info < (3, 10):
        print(f"错误：nanobot 需要 Python 3.10+，当前是 {sys.version_info.major}.{sys.version_info.minor}")
        print("请安装 Python 3.10+：https://python.org")
        sys.exit(1)


def _is_pep668(text):
    return "externally-managed-environment" in text or "PEP 668" in text


def _install(mirror):
    """pip install nanobot-mg 到当前 Python 环境。"""
    root = Path(__file__).resolve().parent
    cmd = [sys.executable, "-m", "pip", "install", "-e", str(root)]
    if mirror:
        cmd += ["-i", mirror]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return 0
    stderr = (result.stderr or "") + (result.stdout or "")
    if _is_pep668(stderr):
        result = subprocess.run(cmd + ["--break-system-packages"], capture_output=True, text=True)
        if result.returncode == 0:
            return 0
    return result.returncode


def _is_system_dir(p: Path) -> bool:
    """Is *p* a system-owned directory we should not write to?"""
    if platform.system() == "Windows":
        parts = p.resolve().parts
        if len(parts) >= 2 and parts[1].lower() in _WINDOWS_SYSTEM_DIRS:
            return True
        return False
    resolved = p.resolve()
    return any(str(parent) in _UNIX_SYSTEM_DIRS for parent in [resolved, *resolved.parents] if parent)


def _pick_install_dir() -> Path | None:
    """找一个 PATH 里可写的用户目录放入口点（跳过系统目录）。"""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        p = Path(d)
        if not p.exists():
            continue
        if _is_system_dir(p):
            continue
        if os.access(str(p), os.W_OK):
            return p
    # Fallback — 建一个用户目录加到 PATH 是最后手段
    return None


def _install_entry_point(target: Path) -> bool:
    is_windows = platform.system() == "Windows"
    if is_windows:
        content = f'@"{sys.executable}" -m nanobot %*\n'
    else:
        content = f'#!/bin/bash\nexec {sys.executable} -m nanobot "$@"\n'
    try:
        target.write_text(content, encoding="utf-8")
        if not is_windows:
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return True
    except OSError as e:
        print(f"   无法写入 {target}：{e}")
        return False


def _is_already_installed() -> bool:
    """Check if nanobot is already installed as editable from this directory."""
    try:
        import nanobot  # type: ignore[import-untyped]
        here = Path(__file__).resolve().parent
        nb_path = Path(nanobot.__file__).resolve().parent
        if nb_path == (here / "nanobot").resolve():
            print(f"✓ nanobot-mg 已安装（可编辑模式），跳过安装步骤")
            return True
    except (ImportError, AttributeError, Exception):
        pass
    return False


def _check_running_process() -> bool:
    """Warn if nanobot.exe is running (would block pip from overwriting the entry point)."""
    if platform.system() != "Windows":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq nanobot.exe", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        return "nanobot.exe" in (result.stdout or "")
    except Exception:
        return False


def _ensure_entry_point() -> None:
    """Make sure `nanobot` command is available on PATH."""
    existing = shutil.which("nanobot")
    if existing:
        r = subprocess.run([existing, "--help"], capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            print(f"命令位置：{existing}")
            return

    install_dir = _pick_install_dir()
    if install_dir is None:
        print("\n⚠️  PATH 中没有合适的目录放 'nanobot' 命令。")
        print(f"   将以下目录加入 PATH：{Path(sys.executable).parent / 'Scripts'}")
        return

    is_windows = platform.system() == "Windows"
    target = install_dir / ("nanobot.cmd" if is_windows else "nanobot")
    if not _install_entry_point(target):
        return

    print(f"命令位置：{target}")
    try:
        r = subprocess.run([str(target), "--help"], capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            print("验证通过 ✓")
    except Exception:
        pass


def main():
    _check_python_version()

    if _is_already_installed():
        _ensure_entry_point()
        return

    running = _check_running_process()
    if running:
        print("⚠️  nanobot.exe 正在运行，pip 无法更新入口点。")
        print("   请先关闭所有 nanobot 终端窗口，再重新运行 setup.bat")
        print("   或者手动结束进程后重试。")
        print()

    print(f"正在安装 nanobot-mg（Python {sys.version_info.major}.{sys.version_info.minor}）...")

    for mirror in MIRRORS:
        label = mirror or "PyPI 官方"
        if _install(mirror) == 0:
            break
        print(f"镜像 {label} 失败，尝试下一个...")
    else:
        if running:
            sys.exit(1)
        print()
        print("所有镜像均不可达，可能没有网络连接。")
        print("但 nanobot-mg 的依赖可能已缓存在本地。")
        print("尝试不指定镜像重新安装...")
        if _install(None) == 0:
            print("离线安装成功！")
        else:
            sys.exit(1)

    _ensure_entry_point()


if __name__ == "__main__":
    main()
