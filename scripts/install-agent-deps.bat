@echo off
REM Install agent extras with mirror fallback for China networks.
REM Usage: scripts\install-agent-deps.bat

pip install nanobot-ai[agent] -i https://pypi.tuna.tsinghua.edu.cn/simple && goto :done
pip install nanobot-ai[agent] -i https://mirrors.aliyun.com/pypi/simple && goto :done
pip install nanobot-ai[agent] -i https://pypi.douban.com/simple && goto :done
pip install nanobot-ai[agent] -i https://pypi.org/simple && goto :done

echo All mirrors failed
exit /b 1

:done
echo OK
exit /b 0
