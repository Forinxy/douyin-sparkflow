# douyin-sparkflow

Douyin SparkFlow 是一个用于自动续火花的 Web 管理版部署包。推荐用 Docker Compose 部署，启动后在浏览器里完成管理员密码、扫码登录、目标好友勾选和发送窗口设置。

## 一键部署到服务器

适合 Ubuntu/Debian/CentOS 类服务器：

```bash
curl -fsSL https://raw.githubusercontent.com/halfwaystudent/douyin-sparkflow/main/deploy/install-server.sh | bash
```

也可以指定安装目录或代理订阅：

```bash
curl -fsSL https://raw.githubusercontent.com/halfwaystudent/douyin-sparkflow/main/deploy/install-server.sh | APP_ROOT=/opt/douyin-sparkflow PROXY_SUB_URL='你的 Mihomo/Clash 订阅链接' bash
```

部署完成后打开：

- Web 面板：`http://服务器IP:8787`
- 扫码登录桌面：Web 面板里的“登录桌面”入口，默认端口 `8788`

首次使用流程：

1. 创建管理员账号密码。
2. 打开登录桌面，扫码登录抖音。
3. 保存登录态。
4. 刷新好友列表，勾选要续火花的目标好友。
5. 设置发送窗口，例如 `10:00-18:00/10m`。

## 本地部署

Windows 本地需要先启动 Docker Desktop，然后运行：

```powershell
.\deploy\install-local.ps1
```

带代理订阅：

```powershell
.\deploy\install-local.ps1 -ProxySubUrl "你的 Mihomo/Clash 订阅链接"
```

脚本会创建 `.env`、运行态目录和默认代理配置，然后执行 `docker compose up -d --build` 并打开 Web 面板。

Linux/macOS 本地可以运行：

```bash
./deploy/install-local.sh
```

## 常用命令

```bash
docker compose ps
docker compose logs -f web
docker compose logs -f scheduler
docker compose up -d --build
docker compose down
```

刷新代理订阅：

```bash
./refresh_proxy.sh
docker compose restart proxy
```

如果 `.env` 里的 `PROXY_SUB_URL` 为空，系统会使用 `proxy/config.example.yaml` 生成一个直连配置。

首次构建会拉取较大的 Playwright 基础镜像。默认 `.env.example` 使用国内同步源以加速下载；如果同步源不可用，可以把 `.env` 里的 `PLAYWRIGHT_BASE_IMAGE` 改回：

```bash
PLAYWRIGHT_BASE_IMAGE=mcr.microsoft.com/playwright/python:v1.56.0-jammy
```

## 目录结构

- `DouYinSparkFlow/`：核心应用、Web UI、登录桌面和发送任务。
- `docker-compose.yml`：统一容器编排入口，包含 Web、登录桌面、定时器、任务和代理服务。
- `deploy/`：服务器和本地一键部署脚本。
- `proxy/config.example.yaml`：安全的代理配置模板。
- `.env.example`：部署环境变量模板。

## 不提交的运行态文件

这些内容包含账号、登录态、日志或本机配置，不应提交到 GitHub：

- `.env`
- `state/`
- `logs/`
- `proxy/config.yaml`
- `DouYinSparkFlow/logs/`
- `DouYinSparkFlow/usersData.json`
- `DouYinSparkFlow/webui_settings.json`
- `DouYinSparkFlow/.im_sdk_cache/`

## 免责声明

本项目只是为了方便个人管理和自用，不是抖音、字节跳动或相关平台的官方工具，也未获得其授权、背书或关联。

使用本项目时，请遵守所在地法律法规、抖音平台规则和相关服务协议。请不要将本项目用于商业用途、批量营销、骚扰他人、刷量引流、规避平台风控，或任何可能损害平台、他人账号及第三方权益的行为。

本项目会在使用者授权登录后，按照使用者配置的账号、好友和发送窗口执行自动化操作。由此产生的账号异常、限流、封禁、数据丢失、服务中断、消息误发、隐私泄露或其他损失，均由使用者自行承担。项目作者和贡献者不对使用本项目产生的任何直接或间接后果负责。

请妥善保管 `.env`、登录态、Cookie、代理配置和运行日志等敏感信息，不要提交到公开仓库，也不要分享给不可信的第三方。如果平台规则、接口页面或风控策略发生变化，请立即停止使用并自行评估风险。

继续部署、运行或修改本项目，即表示你已理解并接受以上说明；如果你不同意，请停止使用本项目。

## 许可

核心应用采用 MIT 协议，详见 [DouYinSparkFlow/LICENSE](DouYinSparkFlow/LICENSE)。
