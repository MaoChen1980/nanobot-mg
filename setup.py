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

def _wrapper_script() -> str:
    """Shell wrapper that runs nanobot via the Python used for installation."""
    return f"""\
#!/bin/bash
exec {sys.executable} -m nanobot "$@"
"""


def _is_pep668(text):
    return "externally-managed-environment" in text or "PEP 668" in text


def _remove_stale_entry_points():
    """Delete stale nanobot entry points that can shadow a fresh install."""
    stale_paths = [Path(p) for p in ["/opt/homebrew/bin/nanobot", "/usr/local/bin/nanobot"]]
    for p in stale_paths:
        if p.exists():
            try:
                p.unlink()
                print(f"   清理旧入口点：{p}")
            except OSError as e:
                print(f"   无法删除 {p}：{e}")


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
    2. Permission denied → --user
    3. PEP 668 → --break-system-packages
    """
    # Strategy 1: normal install
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


def _install_wrapper(target: Path) -> bool:
    """Create a shell wrapper at *target* that runs nanobot via ``python3 -m nanobot``."""
    try:
        target.write_text(_wrapper_script(), encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return True
    except OSError as e:
        print(f"   无法写入 {target}：{e}")
        return False


def _ensure_entry_point():
    """Ensure ``nanobot`` command works after install.

    1. Try ``pip``-installed entry point first.
    2. If it doesn't work, create a shell wrapper in a PATH directory.
    3. If nothing works, suggest what to add to PATH.
    """
    import shutil  # noqa: F811

    install_dir: Path | None = None
    pip_bin = shutil.which("nanobot")
    if pip_bin:
        r = subprocess.run([pip_bin, "--help"], capture_output=True, text=True)
        if r.returncode == 0:
            print(f"   路径：{pip_bin}")
            return True
        print(f"   入口点 {pip_bin} 不可用，尝试创建 wrapper ...")
        install_dir = Path(pip_bin).parent

    # Find a writable PATH directory for the wrapper
    if not install_dir:
        path_dirs = os.environ.get("PATH", "").split(os.pathsep)
        candidate = None
        for d in path_dirs:
            p = Path(d)
            if p.exists() and os.access(str(p), os.W_OK):
                candidate = p
                break
        if not candidate:
            # Fallback to ~/.local/bin
            candidate = Path.home() / ".local" / "bin"
            candidate.mkdir(parents=True, exist_ok=True)
        install_dir = candidate

    target = install_dir / "nanobot"
    if _install_wrapper(target):
        print(f"   已创建 wrapper：{target}")
        return True

    # Last resort: tell user what to do
    print("\n⚠️  无法自动创建 nanobot 命令。请手动运行：")
    print(f'   echo \'exec python3 -m nanobot "$@"\' > {install_dir / "nanobot"}')
    print(f"   chmod +x {install_dir / 'nanobot'}")
    return False


def main():
    print("正在安装 nanobot-mg 依赖...")
    _remove_stale_entry_points()
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
