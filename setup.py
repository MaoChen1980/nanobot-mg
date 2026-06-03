"""nanobot-mg 一键安装脚本"""
import os
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


def _check_python_version():
    if sys.version_info < (3, 10):
        print(f"错误：nanobot 需要 Python 3.10+，当前是 {sys.version_info.major}.{sys.version_info.minor}")
        print("请通过 Homebrew 安装 Python 3：brew install python")
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


def _pick_install_dir() -> Path | None:
    """找一个 PATH 里可写的目录放 wrapper。"""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = Path(d)
        if p.exists() and os.access(str(p), os.W_OK):
            return p
    # Fallback
    fallback = Path.home() / ".local" / "bin"
    fallback.mkdir(parents=True, exist_ok=True)
    if str(fallback) in os.environ.get("PATH", ""):
        return fallback
    return None


def _install_wrapper(target: Path) -> bool:
    """在 target 位置创建 shell wrapper：exec <当前 Python> -m nanobot。"""
    script = f"""\
#!/bin/bash
exec {sys.executable} -m nanobot "$@"
"""
    try:
        target.write_text(script, encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return True
    except OSError as e:
        print(f"   无法写入 {target}：{e}")
        return False


def main():
    _check_python_version()
    print(f"正在安装 nanobot-mg（Python {sys.version_info.major}.{sys.version_info.minor}）...")

    for mirror in MIRRORS:
        label = mirror or "PyPI 官方"
        if _install(mirror) == 0:
            break
        print(f"镜像 {label} 失败，尝试下一个...")
    else:
        sys.exit(1)

    # 创建 wrapper
    install_dir = _pick_install_dir()
    if install_dir is None:
        print("\n⚠️  安装完成，但找不到 PATH 目录来放 'nanobot' 命令。")
        print(f"   请手动将 {sys.executable} 所在的 bin 目录加入 PATH。")
        sys.exit(0)

    target = install_dir / "nanobot"
    if _install_wrapper(target):
        print(f"安装完成！命令位置：{target}")
        # 验证
        r = subprocess.run([str(target), "--help"], capture_output=True, text=True)
        if r.returncode == 0:
            print("验证通过 ✓")
        else:
            print(f"⚠️  安装完成，但运行异常：{r.stderr[:200]}")
    else:
        print(f"\n⚠️  安装完成，但无法创建 {target}")


if __name__ == "__main__":
    main()
