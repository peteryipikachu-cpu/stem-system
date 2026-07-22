# STEM 题目审核系统后端

本仓库保存 FastAPI 审核 API 与数据库迁移；异步审核任务由独立的 Worker 服务消费。Python 应用代码位于 `app/`。

## 本地启动

按环境设置数据库、Redis、CORS 和模型服务凭据；不要将真实凭据提交到版本控制。应用会从环境变量及 `app/.env` 读取配置。

安装锁定的运行与开发依赖：

```bash
cd app
cp .env.example .env
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
```

在已可访问 PostgreSQL 与 Redis 的环境中，直接运行：

```bash
cd app
uvicorn app.main:app --reload --port 8000
```

API 文档默认位于 `http://localhost:8000/docs`。

## 检查

```bash
cd app
pytest
ruff check .
```
