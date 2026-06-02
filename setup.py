"""nanobot-mg 一键安装脚本（pip install --user + 试镜像）"""
import subprocess
import sys

MIRRORS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.douban.com/simple",
    None,  # PyPI 官方
]


def main() -> int:
    print("正在安装 nanobot-mg 依赖...")
    for mirror in MIRRORS:
        cmd = [sys.executable, "-m", "pip", "install", "--user", "-e", "."]
        if mirror:
            cmd.extend(["-i", mirror])
            label = mirror
        else:
            label = "PyPI 官方"

        result = subprocess.run(cmd)
        if result.returncode == 0:
            print("安装完成！")
            return 0
        print(f"镜像 {label} 失败，尝试下一个...")
    return 1


if __name__ == "__main__":
    sys.exit(main())
