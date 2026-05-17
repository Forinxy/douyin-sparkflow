# DouYinSparkFlow

这里是核心应用源码目录，包含：

- `core/`: 任务执行、协议发送、浏览器自动化等核心逻辑
- `webui/`: Web 管理界面与相关后端处理
- `utils/`: 配置、日志和通用辅助逻辑
- `scripts/`: 运行和登录辅助脚本

## 本地开发入口

- 安装依赖：`requirements.txt` / `requirements-web.txt`
- 应用入口：`main.py`
- 容器构建参考：`Dockerfile.server`

运行时账号数据、Web 管理设置、浏览器缓存和日志文件不随仓库提供，需要在目标环境中自行生成。

仓库级说明、部署结构和敏感文件约定请查看上级目录的 [README.md](../README.md)。
