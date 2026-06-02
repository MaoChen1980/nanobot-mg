"""nanobot-mg 一键安装脚本（自动 venv + 试镜像）"""
import ensurepip
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


def _ensure_pip(python: str) -> None:
    """Ensure pip is installed in the venv (macOS may lack ensurepip)."""
    result = subprocess.run([python, "-m", "pip", "--version"], capture_output=True, text=True)
    if result.returncode == 0:
        return
    print("pip 未安装，尝试安装...")
    try:
        ensurepip.bootstrap()
        subprocess.run([python, "-m", "pip", "install", "--upgrade", "pip"], capture_output=True)
        print("pip 已安装。")
    except Exception as e:
        print(f"ensurepip 不可用 ({e})，尝试 get-pip.py...")
        import urllib.request
        url = "https://bootstrap.pypa.io/get-pip.py"
        try:
            urllib.request.urlretrieve(url, "/tmp/get-pip.py")
            subprocess.run([python, "/tmp/get-pip.py"], check=True)
        except Exception as e2:
            print(f"自动安装 pip 失败: {e2}")
            print("请手动安装 pip，然后重新运行 setup.sh")
            sys.exit(1)


def create_venv_and_relaunch() -> None:
    print(f"检测到系统 Python (PEP 668 保护)")
    print(f"创建虚拟环境: {VENV_DIR}")
    venv.EnvBuilder(with_pip=True, upgrade_deps=True).create(str(VENV_DIR))
    venv_python = get_venv_python()
    _ensure_pip(venv_python)
    print("虚拟环境已创建。")
    # 重新用 venv 里的 python 执行本脚本
    raise SystemExit(subprocess.call([venv_python, str(Path(__file__).resolve())]))


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
