"""nanobot-mg 一键安装脚本（自动 venv + 试镜像）"""
import subprocess
import sys
import venv
from pathlib import Path

MIRRORS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.douban.com/simple",
    None,  # PyPI 官方
]

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"


def in_venv() -> bool:
    return sys.prefix != sys.base_prefix


def get_venv_python() -> str:
    if sys.platform == "win32":
        return str(VENV_DIR / "Scripts" / "python.exe")
    return str(VENV_DIR / "bin" / "python")


def create_venv_and_relaunch() -> None:
    print(f"检测到系统 Python (PEP 668 保护)")
    print(f"创建虚拟环境: {VENV_DIR}")
    venv.EnvBuilder(with_pip=True, upgrade_deps=True).create(str(VENV_DIR))
    print("虚拟环境已创建。")
    # 重新用 venv 里的 python 执行本脚本
    raise SystemExit(subprocess.call([get_venv_python(), str(Path(__file__).resolve())]))


def try_install() -> bool:
    print("正在安装 nanobot-mg 依赖...")
    for mirror in MIRRORS:
        cmd = [sys.executable, "-m", "pip", "install", "-e", "."]
        if mirror:
            cmd.extend(["-i", mirror])
            label = mirror
        else:
            label = "PyPI 官方"

        result = subprocess.run(cmd)
        if result.returncode == 0:
            print("安装完成！")
            return True
        print(f"镜像 {label} 失败，尝试下一个...")
    return False


def main() -> int:
    # 不在 venv 且 (macOS 或 Linux): 自动建 venv
    if not in_venv() and (sys.platform == "darwin" or sys.platform == "linux"):
        create_venv_and_relaunch()
    return 0 if try_install() else 1


if __name__ == "__main__":
    sys.exit(main())
