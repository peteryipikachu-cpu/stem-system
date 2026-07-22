# STEM 题目审核 Worker

本仓库独立消费审核队列、调用模型并将结果写回 PostgreSQL。它必须与 STEM 审核后端使用同一 PostgreSQL、Redis 和模型配置。

## 本地启动

```bash
cd app
cp .env.example .env
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
python -m app.worker
```

后端负责创建审核任务与执行数据库迁移；Worker 只消费已入队任务。可通过多个独立进程启动 Worker 进行水平扩展。

豆包支持以 `DOUBAO_API_KEYS` 配置逗号分隔的 Key 池。每把 Key 在 Redis 中拥有独立的并发、RPM、TPM 与熔断桶；将 `WORKER_CONCURRENCY` 调整到各供应商可用并发总和，即可安全利用多 Key 的吞吐。

## 检查

```bash
cd app
pytest
ruff check .
```
