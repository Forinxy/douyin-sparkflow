# Douyin SparkFlow

Douyin SparkFlow 是一个个人自用的抖音火花维护工具，包含 Web 管理后台、扫码登录桌面、定时发送任务和代理配置模板。项目默认使用 Docker Compose 部署。

本项目基于 [2061360308/DouYinSparkFlow](https://github.com/2061360308/DouYinSparkFlow) 二次开发，补充了 Web 管理、Docker 部署、登录桌面、发送可靠性和运行维护能力。

## 功能

- Web 面板管理账号、目标好友、发送窗口和日志。
- 浏览器扫码登录并保存登录状态。
- 按每日时间窗口自动发送，也支持手动补发未成功或未发送目标。
- 通过 Mihomo/Clash 代理配置访问网络。
- 登录桌面、Web 后台、定时器、任务容器和代理容器统一编排。
- 发送确认、好友列表扫描、账号级临时故障冷却等可靠性保护。

## 免责声明

本项目仅用于个人学习、研究和自用场景，不是抖音、字节跳动或相关平台的官方工具，也未获得其授权、背书或关联。

使用本项目时，请遵守所在地法律法规、平台规则和相关服务协议。请勿用于商业营销、批量骚扰、刷量引流、规避平台风控，或任何可能损害平台、他人账号及第三方权益的行为。

请妥善保管 `.env`、登录状态、Cookie、代理配置、运行日志和账号数据，不要提交到公开仓库，也不要分享给不可信第三方。继续部署、运行或修改本项目，即表示你理解并接受以上说明。

## 服务器一键安装

推荐在服务器上使用 Git clone 方式安装，适合 `raw.githubusercontent.com` 访问较慢的环境：

```bash
apt update && apt install -y git curl gnupg ca-certificates && rm -rf /opt/douyin-sparkflow && git clone --depth=1 https://github.com/halfwaystudent/douyin-sparkflow.git /opt/douyin-sparkflow && cd /opt/douyin-sparkflow && bash deploy/install-server.sh
```

指定安装目录或代理订阅：

```bash
APP_ROOT=/opt/douyin-sparkflow PROXY_SUB_URL='你的 Mihomo/Clash 订阅链接' bash -c 'apt update && apt install -y git curl gnupg ca-certificates && rm -rf "$APP_ROOT" && git clone --depth=1 https://github.com/halfwaystudent/douyin-sparkflow.git "$APP_ROOT" && cd "$APP_ROOT" && bash deploy/install-server.sh'
```

如果服务器能稳定访问 raw GitHub，也可以使用：

```bash
curl -fsSL https://raw.githubusercontent.com/halfwaystudent/douyin-sparkflow/main/deploy/install-server.sh | bash
```

常用环境变量：

```bash
APP_ROOT=/opt/douyin-sparkflow
BRANCH=main
WEB_PORT=8787
LOGIN_DESKTOP_WEB_PORT=8788
PROXY_SUB_URL='你的 Mihomo/Clash 订阅链接'
DEFAULT_SCHEDULE='10:00-18:00/20m'
```

安装完成后检查容器：

```bash
cd /opt/douyin-sparkflow
docker compose ps
```

正常应看到：

- `douyin-web`：Web 管理后台。
- `login-desktop`：扫码登录桌面。
- `douyin-scheduler`：读取 `state/cron/root` 的定时器。
- `mihomo`：代理服务。
- `douyin-task`：一次性任务容器，未常驻是正常的。

访问地址：

- Web 面板：`http://服务器IP:8787`
- 登录桌面：Web 面板中的“登录桌面”入口，默认端口 `8788`

如果外网打不开，请在云服务器安全组、系统防火墙或 1Panel 防火墙中放行：

- `8787/tcp`：Web 管理后台。
- `8788/tcp`：扫码登录桌面。

## 无损更新

更新代码和容器，但保留运行数据：

```bash
cd /opt/douyin-sparkflow
ACTION=update bash deploy/install-server.sh
```

更新不会覆盖这些运行态文件：

- `.env`
- `state/`
- `proxy/config.yaml`
- `DouYinSparkFlow/logs/`
- `DouYinSparkFlow/usersData.json`
- `DouYinSparkFlow/config.json`
- `DouYinSparkFlow/webui_settings.json`

## 本地运行

Windows 需要先启动 Docker Desktop：

```powershell
.\deploy\install-local.ps1
```

带代理订阅：

```powershell
.\deploy\install-local.ps1 -ProxySubUrl "你的 Mihomo/Clash 订阅链接"
```

Linux/macOS：

```bash
./deploy/install-local.sh
```

本地脚本会创建 `.env`、`state/cron/root`、`state/login-profile`、`proxy/config.yaml` 和 `DouYinSparkFlow/logs`，然后执行 `docker compose up -d --build`。

## 配置

`.env.example` 是部署模板。首次安装时会复制为 `.env`。常用配置：

```bash
WEB_PORT=8787
LOGIN_DESKTOP_WEB_PORT=8788
PROXY_SUB_URL=
DEFAULT_SCHEDULE=10:00-18:00/20m
PLAYWRIGHT_BASE_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/mcr.microsoft.com/playwright/python:v1.56.0-jammy
```

如果 `.env` 中的 `PROXY_SUB_URL` 为空，项目会使用 `proxy/config.example.yaml` 生成直连代理配置。

默认定时任务写入 `state/cron/root`：

```cron
*/20 10-17 * * * cd /app && python main.py --doTask >> /app/logs/app.log 2>&1
0 18 * * * cd /app && python main.py --doTask >> /app/logs/app.log 2>&1
20 18 * * * cd /app && env SPARKFLOW_MANUAL_RUN=1 SPARKFLOW_MANUAL_UNSENT_ONLY=1 PYTHONUNBUFFERED=1 python main.py --doTask >> /app/logs/app.log 2>&1
```

你也可以在 Web 面板里修改发送窗口，Web 会更新同一个 cron 文件。

## 常用命令

```bash
docker compose ps
docker compose logs -f web
docker compose logs -f scheduler
docker compose up -d --build
docker compose down
./refresh_proxy.sh
```

手动跑一次发送任务：

```bash
docker compose run --rm task
```

## 项目结构

- `DouYinSparkFlow/`：核心应用、Web UI、登录桌面和发送任务。
- `docker-compose.yml`：容器编排入口。
- `deploy/`：服务器和本地部署脚本。
- `proxy/config.example.yaml`：直连代理配置模板。
- `.env.example`：部署环境变量模板。

## 运行时文件

以下文件由部署或运行生成，通常不纳入版本控制：

- `.env`
- `state/`
- `logs/`
- `proxy/config.yaml`
- `DouYinSparkFlow/logs/`
- `DouYinSparkFlow/usersData.json`
- `DouYinSparkFlow/config.json`
- `DouYinSparkFlow/webui_settings.json`
- `DouYinSparkFlow/.im_sdk_cache/`

## 许可

核心应用采用 MIT 协议，详见 [DouYinSparkFlow/LICENSE](DouYinSparkFlow/LICENSE)。
