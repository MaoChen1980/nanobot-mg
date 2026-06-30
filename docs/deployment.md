# 部署指南

## 目录

- [Docker 部署](#docker-部署)
- [Systemd 服务（Linux）](#systemd-服务linux)
- [macOS LaunchAgent](#macos-launchagent)
- [配置与数据持久化](#配置与数据持久化)
- [端口说明](#端口说明)

---

## Docker 部署

### 镜像构建

项目根目录包含 `Dockerfile`，基于 `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` 构建：

- 安装运行时依赖：`git`、`bubblewrap`、`openssh-client`
- 使用 `uv pip install` 以非 root 用户（`nanobot`, uid 1000）安装项目
- 默认暴露端口 `18790`
- 入口点为 `entrypoint.sh`，该脚本检查 `~/.nanobot` 目录是否可写，然后执行 `nanobot "$@"`

### docker-compose

项目提供 `docker-compose.yml`，定义两个服务：

#### nanobot-gateway

核心服务，运行 `nanobot gateway`。监听 `18790` 端口。

```yaml
services:
  nanobot-gateway:
    container_name: nanobot-gateway
    command: ["gateway"]
    restart: unless-stopped
    ports:
      - 18790:18790
```

#### nanobot-cli

CLI 模式的附加服务，仅在明确指定 `--profile cli` 时启动。

```yaml
  nanobot-cli:
    profiles:
      - cli
    command: ["status"]
    stdin_open: true
    tty: true
```

### 启动方式

```bash
# 启动 gateway（推荐）
docker compose up nanobot-gateway -d

# 启动所有服务
docker compose up -d

# 同时启动 CLI 交互
docker compose --profile cli run nanobot-cli
```

### 资源限制

docker-compose 中预设了资源限制：

| 服务 | CPU 限制 | 内存限制 | CPU 预留 | 内存预留 |
|------|---------|---------|---------|---------|
| nanobot-gateway | 1 核 | 1G | 0.25 核 | 256M |

### 安全配置

```yaml
cap_drop:
  - ALL
cap_add:
  - SYS_ADMIN
security_opt:
  - apparmor=unconfined
  - seccomp=unconfined
```

`SYS_ADMIN` 是执行沙箱命令（bubblewrap）所需的最低权限。

---

## Systemd 服务（Linux）

以下为将 nanobot gateway 注册为 systemd 服务的配置模板。

创建 `/etc/systemd/system/nanobot-gateway.service`：

```ini
[Unit]
Description=nanobot gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=your-username
ExecStart=%h/.local/bin/nanobot gateway
Restart=on-failure
RestartSec=5

# 安全硬化
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=read-only
ReadWritePaths=/home/your-username/.nanobot

[Install]
WantedBy=default.target
```

启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable nanobot-gateway
sudo systemctl start nanobot-gateway

# 查看状态
sudo systemctl status nanobot-gateway

# 查看日志
sudo journalctl -u nanobot-gateway -f
```

---

## macOS LaunchAgent

创建 `~/Library/LaunchAgents/com.nanobot.gateway.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.nanobot.gateway</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/nanobot</string>
        <string>gateway</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/nanobot-gateway.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/nanobot-gateway.log</string>
</dict>
</plist>
```

加载并启动：

```bash
launchctl load ~/Library/LaunchAgents/com.nanobot.gateway.plist

# 手动启停
launchctl start com.nanobot.gateway
launchctl stop com.nanobot.gateway

# 卸载
launchctl unload ~/Library/LaunchAgents/com.nanobot.gateway.plist
```

---

## 配置与数据持久化

### 数据目录

所有配置和数据存储在 `~/.nanobot/` 目录下：

| 路径 | 说明 |
|------|------|
| `~/.nanobot/config.json` | 主配置文件 |
| `~/.nanobot/nanobot.db` | SQLite 数据库（会话、记忆） |
| `~/.nanobot/workspace/` | 工作区（记忆文件、技能、任务等） |
| `~/.nanobot/logs/` | 日志文件 |

### Docker 持久化

```yaml
volumes:
  - ~/.nanobot:/home/nanobot/.nanobot
```

容器内的 `nanobot` 用户（uid 1000）需要能读写宿主机的 `~/.nanobot` 目录。如果遇到权限问题：

```bash
# 修复目录所有者
sudo chown -R 1000:1000 ~/.nanobot

# 或以当前用户 UID 运行容器
docker run --user $(id -u):$(id -g) ...
```

### entrypoint.sh 权限检查

容器启动时 `entrypoint.sh` 会检查 `~/.nanobot` 是否可写，若不可写则输出错误信息并退出。

---

## 端口说明

| 端口 | 服务 | 说明 | 默认值 |
|------|------|------|--------|
| 18790 | Gateway HTTP | 主服务端口，提供 WebUI 和 REST API | 18790 |
| 18791 | Hub TCP | gateway 内部用于与 proxy 进程通信的 TCP 端口（gateway_port + 1） | 18791 |

### Gateway 端口

`nanobot gateway` 是唯一的主服务命令，内嵌了 WebUI 文件服务和 REST API。默认监听 `0.0.0.0:18790`。

可通过以下方式修改端口：

```bash
nanobot gateway --port 9000
```

或在配置文件中设置 `gateway.port`。

### Hub 端口

Hub TCP 端口用于 gateway 与 proxy 进程（如飞书、钉钉等渠道的机器人进程）之间的内部通信。端口号为 gateway 端口 + 1，不可独立配置。
