# douyin-sparkflow

这个仓库是一个围绕 `DouYinSparkFlow/` 组织的部署仓库，用于保存核心应用源码、代理配置和容器编排文件，方便在本地或服务器上统一维护。当前整理目标是“私有内用、可放入 GitHub、避免提交运行态和敏感数据”。

## 仓库结构

- `DouYinSparkFlow/`: 核心应用源码，包含 Web UI、任务调度、账号登录与消息发送逻辑。
- `proxy/`: 代理容器配置目录，当前包含 `mihomo` 配置文件。
- `docker-compose.yml`: 统一的容器编排入口。
- `refresh_proxy.sh`: 代理刷新脚本。

## 运行方式概览

- 支持本地运行核心应用，也支持通过 `docker-compose.yml` 在服务器上部署。
- Web 管理入口、交互式登录桌面和定时任务都由仓库内现有脚本与配置驱动。
- 定时发送、账号状态和消息模板属于运行时行为，不在本仓库中直接携带账号数据。

## 配置与敏感文件

以下内容不会进入当前 Git 仓库，需要在实际部署环境中自行补齐：

- `.env`
- `state/`
- `logs/`
- `DouYinSparkFlow/usersData.json`
- `DouYinSparkFlow/webui_settings.json`
- `DouYinSparkFlow/.im_sdk_cache/`

如果需要复现运行环境，建议在目标机器上重新生成这些文件，而不是从仓库恢复。

## 使用边界

- 本仓库按内部项目资料整理，主要用于源码管理、部署维护和环境迁移。
- 使用者需要自行评估平台规则、账号风险和运行后果。
- 不建议把真实账号数据、浏览器登录态、日志或运行缓存提交到仓库。

## 许可证

核心应用当前采用 MIT 协议，许可证文件位于 [DouYinSparkFlow/LICENSE](DouYinSparkFlow/LICENSE)。

如需查看源码级说明，请优先阅读 [DouYinSparkFlow/README.md](DouYinSparkFlow/README.md)。
