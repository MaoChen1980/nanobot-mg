@echo off
REM nanobot-mg 一键安装（Windows）
REM 双击运行，不用操心镜像配置

echo 正在安装 nanobot-mg 依赖...

pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple && goto :done
pip install -e . -i https://mirrors.aliyun.com/pypi/simple && goto :done
pip install -e . -i https://pypi.douban.com/simple && goto :done
pip install -e . && goto :done

echo 安装失败，请检查网络后重试
pause
exit /b 1

:done
echo 安装完成！
pause
exit /b 0
