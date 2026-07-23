# STEM 题目审核系统：代理工作指南

## 项目概览

这是一个题目导入、异步 AI 审核和结果查看系统：

- 前端：Next.js 16、React 19、TypeScript、Ant Design、SWR、KaTeX。
- 后端：FastAPI、SQLAlchemy async、Alembic、PostgreSQL、Redis。
- 异步任务：Redis 队列由独立 Worker 消费；审核进度通过 SSE 推送。
- 部署：开发环境由 Next.js 将同域 `/api/*` 重写至 FastAPI；生产环境由 Nginx 转发并透传 SSE。

## 目录与职责

| 路径 | 职责 |
| --- | --- |
| `src/app/` | App Router 页面、布局及全局样式。 |
| `src/components/` | 可复用的前端交互组件，例如导入、账号管理和公式渲染。 |
| `src/lib/check-runs.ts` | 审核任务创建、查询与 SSE 订阅客户端。 |
| `src/types/index.ts` | 前端共享类型。修改接口字段时同步更新。 |
| `backend/app/main.py` | FastAPI 生命周期、路由、鉴权入口和 SSE API。 |
| `backend/app/models.py` | SQLAlchemy 数据模型。 |
| `backend/app/schemas.py` | API 请求/响应 Pydantic schema。 |
| `backend/app/services.py` | 审核编排、队列、模型调用与结果序列化。 |
| `backend/app/worker.py` | Redis 队列消费循环。 |
| `backend/alembic/versions/` | 仅追加的数据库迁移。 |
| `deploy/nginx/stem-audit.conf` | 生产反向代理与 SSE 配置。 |

## 先读再改

<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

- 改动前端时，先阅读与任务相关的 Next.js 16 文档（位于 `node_modules/next/dist/docs/`），并遵守其中的弃用提示。
- 改动既有文件前先检查 `git status --short`。工作区可能含有用户的未提交改动；不得覆盖、还原或格式化无关文件。
- 先沿用附近的代码风格和类型定义。前端路径别名为 `@/*`，后端使用 async SQLAlchemy。
- 不要提交密钥、Cookie、数据库转储或真实供应商配额；`.env.backend` 仅供本地使用。

## 本地开发

### 依赖与服务

```bash
npm ci
cp .env.backend.example .env.backend
docker compose up --build
npm run dev
```

- 前端：`http://localhost:3000`
- 后端 Swagger：`http://localhost:8000/docs`
- 默认容器服务名为 `postgres`、`redis`、`api`、`worker`。
- 不要假设已有本地服务可安全复用；启动或重启服务前确认端口、容器和用户意图。

`next.config.ts` 中的重写将浏览器的 `/api/:path*` 转到 `BACKEND_API_URL`（默认 `http://localhost:8000`）。前端请求必须使用相对 `/api/...` 路径，避免绕过同源 Cookie、开发重写或生产 Nginx。

### 后端单独运行

在 `backend/` 中安装开发依赖后可执行：

```bash
uvicorn app.main:app --reload --port 8000
python -m app.worker
pytest
ruff check .
```

单独运行时，按实际数据库和 Redis 地址设置 `DATABASE_URL`、`REDIS_URL` 等环境变量。容器默认地址中的 `postgres`、`redis` 主机名不能直接用于宿主机。

## 实现约定

### 前端

- 页面和涉及浏览器状态、SWR、事件流的组件须保留正确的 Client Component 边界；不要把交互逻辑迁入未经确认的 Server Component。
- API 调用应检查非成功响应并给出可用错误信息；接口返回变化时同步更新 `src/types/index.ts`、调用端和后端 schema。
- 审核执行采用 `src/lib/check-runs.ts`：创建任务后订阅 `/api/check-runs/{id}/events`，同时保留轮询/重取数据的容错路径。不要将长时审核阻塞在浏览器请求中。
- 数学内容通过 `LatexRenderer` 呈现。修改公式处理时运行后端 LaTeX 测试，并注意不可信题干的渲染安全。
- UI 保持 Ant Design 现有风格；避免无关的全局 CSS 改动和大范围重排。

### API、鉴权与数据

- 在 `main.py` 添加或调整接口时，使用 Pydantic schema 作为契约；明确状态码、错误语义和认证要求。
- 受保护接口使用 `get_current_user`；仅管理员操作使用 `require_admin`。题目和审核任务查询必须维持所有者范围，管理员才可跨用户查看。
- 登录通过 HttpOnly session Cookie 工作。不要改为把令牌放入 localStorage，也不要在日志或响应中泄露 token、密码或上游 API key。
- 题目和审核 API 由 `services.py` 的 `question_json`、`check_result_json` 等函数统一序列化；新增字段时避免在路由中重复拼装不一致的 JSON。
- 模型调用、重试、并发和限流集中在 `services.py` 与 `config.py`。新增审核类型应经过队列、依赖激活、结果落库、完成状态和事件发送的完整链路。

### 队列与 SSE

- API 只创建 `CheckRun`/批次并入队，Worker 负责实际执行。保持两者可独立重启和幂等。
- 对可能重复提交的启动接口保留 `Idempotency-Key` 行为。
- 修改事件格式时同时检查后端 `run_events`/`emit`、Nginx 的 SSE 缓冲配置，以及前端订阅逻辑。
- 不要以同步 HTTP 等待外部 AI 完成为替代队列；超时、重试和租约恢复是系统可靠性的一部分。

### 数据库迁移

- 变更 `models.py` 中的持久化结构时，创建新的 Alembic revision，禁止修改已提交的迁移文件。
- 迁移应能从空库顺序执行，并同时考虑 API 与 Worker 可能并发运行的兼容性。
- 使用 `alembic upgrade head` 验证迁移；不要执行会删除真实数据的操作，除非用户明确授权。

## 验证与交付

根据改动范围运行最小且充分的检查：

| 改动 | 至少执行 |
| --- | --- |
| 前端 TypeScript/样式/组件 | `npm run lint`；涉及构建、路由或配置时再执行 `npm run build`。 |
| 审核 API 客户端或性能脚本 | `npm run benchmark`（需要可用后端时）。 |
| 后端业务、模型调用或 LaTeX | 在 `backend/` 执行 `pytest` 和 `ruff check .`。 |
| 路由、鉴权或响应契约 | 运行相关测试，并用已认证与未认证场景验证状态码和权限边界。 |
| 数据模型或迁移 | `alembic upgrade head`，再运行相关后端测试。 |
| SSE、队列或 Worker | 使用 API 创建任务，确认 Worker 消费、事件送达、结果持久化与最终状态。 |

完成时说明：修改了哪些文件、运行了哪些验证及结果，以及未运行的检查和原因。不要把无关的 `.qoder/`、构建产物、缓存或用户已有改动混入提交。
