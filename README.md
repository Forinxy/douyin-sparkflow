# douyin-sparkflow

Douyin SparkFlow 提供一个面向个人自用场景的抖音火花维护工具。项目包含 Web 管理面板、登录桌面、定时任务和代理配置模板，默认通过 Docker Compose 部署。

## 功能特性

- Web 面板管理账号、目标好友和发送窗口。
- 浏览器扫码登录并保存本地登录态。
- 定时执行续火花任务，支持手动触发和日志查看。
- 可选 Mihomo/Clash 代理订阅配置。
- Docker Compose 编排 Web、登录桌面、定时器、任务和代理服务。

## 免责声明

本项目仅用于个人学习、研究和自用场景，不是抖音、字节跳动或相关平台的官方工具，也未获得其授权、背书或关联。

使用本项目时，请遵守所在地法律法规、抖音平台规则和相关服务协议。请勿将本项目用于商业用途、批量营销、骚扰他人、刷量引流、规避平台风控，或任何可能损害平台、他人账号及第三方权益的行为。

本项目会在使用者授权登录后，按照使用者配置的账号、好友和发送窗口执行自动化操作。由此产生的账号异常、限流、封禁、数据丢失、服务中断、消息误发、隐私泄露或其他损失，均由使用者自行承担。项目作者和贡献者不对使用本项目产生的任何直接或间接后果负责。

请妥善保管 `.env`、登录态、Cookie、代理配置和运行日志等敏感信息，不要提交到公开仓库，也不要分享给不可信的第三方。如果平台规则、接口页面或风控策略发生变化，请立即停止使用并自行评估风险。

继续部署、运行或修改本项目，即表示你已理解并接受以上说明；如果你不同意，请停止使用本项目。

## 快速开始

在服务器上运行：

```bash
curl -fsSL https://raw.githubusercontent.com/halfwaystudent/douyin-sparkflow/main/deploy/install-server.sh | bash
```

指定安装目录或代理订阅：

```bash
curl -fsSL https://raw.githubusercontent.com/halfwaystudent/douyin-sparkflow/main/deploy/install-server.sh | APP_ROOT=/opt/douyin-sparkflow PROXY_SUB_URL='你的 Mihomo/Clash 订阅链接' bash
```

部署完成后访问：

- Web 面板：`http://服务器IP:8787`
- 登录桌面：Web 面板中的“登录桌面”入口，默认端口 `8788`

首次使用：

1. 创建管理员账号。
2. 打开登录桌面并扫码登录抖音。
3. 保存登录态。
4. 刷新好友列表并选择目标好友。
5. 设置发送窗口，例如 `10:00-18:00/10m`。

## 本地运行

Windows 需要先启动 Docker Desktop：

```powershell
.\deploy\install-local.ps1
```

如需配置代理订阅：

```powershell
.\deploy\install-local.ps1 -ProxySubUrl "你的 Mihomo/Clash 订阅链接"
```

Linux/macOS：

```bash
./deploy/install-local.sh
```

本地脚本会创建 `.env`、运行态目录和默认代理配置，并执行 `docker compose up -d --build`。

## 配置说明

如果 `.env` 中的 `PROXY_SUB_URL` 为空，项目会使用 `proxy/config.example.yaml` 生成直连代理配置。

首次构建需要拉取 Playwright 基础镜像。默认 `.env.example` 使用国内同步源；如果同步源不可用，可以将 `.env` 中的 `PLAYWRIGHT_BASE_IMAGE` 改为：

```bash
PLAYWRIGHT_BASE_IMAGE=mcr.microsoft.com/playwright/python:v1.56.0-jammy
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

## 项目结构

- `DouYinSparkFlow/`：核心应用、Web UI、登录桌面和发送任务。
- `docker-compose.yml`：容器编排入口。
- `deploy/`：服务器和本地部署脚本。
- `proxy/config.example.yaml`：代理配置模板。
- `.env.example`：部署环境变量模板。

## 运行时文件

以下路径由应用在部署或运行时生成，通常不纳入版本控制。迁移或备份实例时，可根据实际需要处理这些文件：

- `.env`
- `state/`
- `logs/`
- `proxy/config.yaml`
- `DouYinSparkFlow/logs/`
- `DouYinSparkFlow/usersData.json`
- `DouYinSparkFlow/webui_settings.json`
- `DouYinSparkFlow/.im_sdk_cache/`

## GitHub Star 趋势

[![GitHub Star 趋势图](https://api.star-history.com/svg?repos=halfwaystudent/douyin-sparkflow&type=Date)](https://star-history.com/#halfwaystudent/douyin-sparkflow&Date)

## 友情链接

- [Linux Do](https://linux.do/)

## 许可

核心应用采用 MIT 协议，详见 [DouYinSparkFlow/LICENSE](DouYinSparkFlow/LICENSE)。
