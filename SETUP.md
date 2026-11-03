# 本地运行指南

项目介绍见 [README](README.md)。本页用于安装、配置和启动，不需要先理解内部实现。

## 首次启动

准备 Python 3.12+、Node.js 20+、pnpm 和已运行的 Docker Desktop。在项目根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
make web-install
cp .env.example .env
```

只在第一次复制 `.env`，不要覆盖已有配置。填写 API Key 后：

```bash
make db-up
make migrate
make seed
make run-api
```

另开终端，在同一目录执行 `make run-web`，访问 http://127.0.0.1:5173 。

## 配置说明

- `MODEL_PROVIDER=live` 使用真实模型；`fake` 只用于离线流程演示。
- `MODEL_API_KEY` 填真实密钥，`MODEL_API_BASE` 和 `MODEL_IDENTIFIER` 填服务商支持的地址与模型名称。
- `INLINE_WORKER=1` 是默认本地方式，后台任务随 API 一起运行。
- 修改端口时同步检查 `APP_PORT`、`WEB_PORT`、`WEB_API_TARGET` 和 `PUBLIC_ORIGINS`。
- 数据库连接使用 `DATABASE_URL`；容器内连接使用 `DOCKER_DATABASE_URL`。
- `MAX_SESSION_COST` 必须设置；当前费用控制仍是项目估算，不等于服务商最终账单。

启动时会显示实际模型模式和名称；本地启动若选择 live 但没有密钥，会直接报错。

环境变量优先于 `.env`。如果配置写 live、启动却显示 fake，请在启动终端执行 `unset MODEL_PROVIDER` 后重启。不要在日志或 Issue 中粘贴密钥。

## 日常启动与停止

已有依赖和配置时，运行 `make db-up`，再在两个终端分别运行 `make run-api` 和 `make run-web`，无需再次复制配置或初始化示例数据。

代码更新包含数据库迁移时，先执行 `make migrate`。在运行服务的终端按 `Ctrl+C` 停止服务；`make db-down` 停止容器，默认保留数据卷。不要为了解决配置问题随意删除数据卷。

若要单独运行 Worker，设置 `INLINE_WORKER=0`，重启后端，再另开终端运行 `make run-worker`。

## Docker 方式

Docker API 和 Worker 读取同一份根目录 `.env`，前端仍在本机运行：

```bash
docker compose --env-file .env -f infra/compose/docker-compose.yml up -d --build
docker compose --env-file .env -f infra/compose/docker-compose.yml exec api alembic upgrade head
docker compose --env-file .env -f infra/compose/docker-compose.yml exec api python -m db.seed
make run-web
```

容器方式与本机方式不要同时占用相同端口。API 映射到 `APP_PORT`，数据库映射到 `POSTGRES_PORT`。已有数据库卷修改配置中的密码，不会自动更改库内账号密码，需要同步处理。

## 验证与排障

- 后端健康检查：http://127.0.0.1:8000/health/ready 。
- 端口被占用时，先确认是否已有一套服务运行，不要重复启动。
- 修改模型配置后重启后端；检查实际启动模式，而不只看 `.env` 内容。
- 训练生成是异步过程，等待页面显示结果；失败时页面会展示错误并允许重试。

自动化测试优先使用 `make test-agents`：该入口创建隔离临时 PostgreSQL，使用模拟模型，结束后清理。

```bash
make test-unit
make test-agents
```

直接运行 integration/recovery 测试会重建测试数据库，必须把 `DATABASE_URL` 指向专用测试库，不能使用保存个人面试记录的业务库。

## 密钥与公开部署

只提交 `.env.example`，真实 `.env`、运行数据、个人面试文档和日志已加入忽略规则。API Key 不要命名为 `VITE_*`，否则可能进入浏览器。

GitHub 网页拖拽不会自动按 `.gitignore` 过滤；不要公开 `docker compose config` 完整输出，它可能包含密钥。已经泄露的密钥需要撤销并更换，单纯删除文件不能清除提交历史。

默认配置仅面向本地开发。公开部署前关闭 `INTERVIEWLAB_DEV_AUTH`，更换数据库密码与随机 `SESSION_SECRET`，配置访问控制、来源限制和 HTTPS。
