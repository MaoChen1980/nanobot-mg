"""nanobot-mg 一键安装脚本（pipx 优先，兼容器/User 安装）"""
import shutil
import subprocess
import sys

MIRRORS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.douban.com/simple",
    None,  # PyPI 官方
]


def _pip_cmd() -> list[str]:
    """返回可用的 pip 命令。pipx 优先（自动隔离，命令进 PATH），退而求其次用 pip --user。"""
    if shutil.which("pipx"):
        return ["pipx", "install"]
    # macOS (Homebrew) / Linux 的 PEP 668 保护
    return [sys.executable, "-m", "pip", "install", "--break-system-packages", "--user"]


def main() -> int:
    use_pipx = shutil.which("pipx")
    if not use_pipx:
        print("提示: 安装 pipx (brew install pipx) 可自动隔离环境，命令直接可用。")
    print("正在安装 nanobot-mg 依赖...")
    base = _pip_cmd()
    for mirror in MIRRORS:
        cmd = [*base, "-e", "."]
        if mirror:
            cmd.extend(["-i", mirror])
            label = mirror
        else:
            label = "PyPI 官方"

        result = subprocess.run(cmd, cwd=ROOT)
        if result.returncode == 0:
            print("安装完成！")
            return 0
        print(f"镜像 {label} 失败，尝试下一个...")
    return 1


if __name__ == "__main__":
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent
    sys.exit(main())
