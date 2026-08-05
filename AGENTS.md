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

### 当前本机启动方式（不使用 Docker）

本仓库由三个彼此独立的 Git 仓库组成，实际可执行代码分别位于：

| 组件 | 目录 | 启动命令 |
| --- | --- | --- |
| 前端 | `stem-system-frontend/app` | `npm run dev -- --hostname 127.0.0.1 --port 3000` |
| 后端 API | `stem-system-backend/app` | `env DATABASE_URL='postgresql+asyncpg://pikachu@localhost:5432/stem' REDIS_URL='redis://localhost:6379/0' /opt/anaconda3/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000` |
| Worker | `stem-system-worker/app` | `env DATABASE_URL='postgresql+asyncpg://pikachu@localhost:5432/stem' REDIS_URL='redis://localhost:6379/0' /opt/anaconda3/bin/python -m app.worker` |

- 本项目日常本机调试**不使用 Docker**，前端固定使用 `3000` 端口；如端口已被占用，应先确认并停止原有进程，不要改为 `3001`。
- 启动顺序为：后端 API、Worker、前端；三项分别在独立终端运行。前端访问地址为 `http://127.0.0.1:3000`，后端文档为 `http://127.0.0.1:8000/docs`。
- 后端和 Worker 使用 Conda 的 `/opt/anaconda3/bin/python` 及本机 PostgreSQL 数据库 `stem`、本机 Redis。API Key 仅从各组件本地忽略的环境变量文件读取，禁止写入命令、代码、日志或提交。
- 每次启动或重启前，先确认 `3000`、`8000` 端口和 Redis/PostgreSQL 的实际状态；停止服务时在对应终端使用 `Ctrl+C`，不要误杀无关进程。

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

### 代码提交与推送（三个独立 GitLab 仓库）

- 自动提交与推送：每次完成修改或功能开发并验证通过后，须自动进入对应的组件仓库（前端、后端 API 或 Worker）执行 `git status` 检查，完成规范的 `git add`、有明确中文说明的 `git commit` 以及 `git push origin main`，确保代码变更实时同步至 GitLab。
- `stem-system-frontend/app`、`stem-system-backend/app`、`stem-system-worker/app` 均是独立仓库，分别提交和推送；它们的 `origin` 指向各自的 GitLab 仓库，默认分支为 `main`。
- 提交前必须进入对应组件目录并检查变更：

  ```bash
  git status --short
  git add <本次明确需要提交的文件>
  git commit -m "中文提交说明"
  git push origin main
  ```

- 后端的 `reports/` 只保留在本机，**不得提交**；后端提交时必须逐个列出文件 `git add`，不要使用 `git add .` 或 `git add -A`。同样不得提交 `.env*`、密钥、Cookie、数据库导出、构建缓存或其他用户已有的无关改动。
- 推送是外部状态变更：确认目标仓库和分支后才可执行，失败时保留本地提交并报告原因；不得使用 `--force`、不得修改三个 GitLab 仓库的远程地址。
- 根目录 GitHub 聚合仓库（`peteryipikachu-cpu/stem-system`）：包含全局配置与 `AGENTS.md` 知识库。当更新 `AGENTS.md` 或根目录规则/文档后，须同步在根目录执行提交并推送至 GitHub 的 `main` 分支。内部三个 GitLab 子仓库保持独立提交推送。

## 实现约定

### 前端

- 页面和涉及浏览器状态、SWR、事件流的组件须保留正确的 Client Component 边界；不要把交互逻辑迁入未经确认的 Server Component。
- API 调用应检查非成功响应并给出可用错误信息；接口返回变化时同步更新 `src/types/index.ts`、调用端和后端 schema。
- 审核执行采用 `src/lib/check-runs.ts`：创建任务后订阅 `/api/check-runs/{id}/events`，同时保留轮询/重取数据的容错路径。不要将长时审核阻塞在浏览器请求中。
- 数学内容通过 `LatexRenderer` 呈现。修改公式处理时运行 Worker 的 LaTeX 测试（`stem-system-worker/app/tests/test_latex.py`），并注意不可信题干的渲染安全。
- UI 保持 Ant Design 现有风格；避免无关的全局 CSS 改动和大范围重排。antd 的 Space 组件须使用 `orientation` 属性，`direction` 已弃用会触发控制台警告。
- 同路由内的查询参数导航（顶部标签页 `/?view=...`、项目卡片 `/?projectId=...`、面包屑返回 `/`）统一用 `window.history.pushState`，Next.js 会同步 `useSearchParams`；不要用 `router.push`：纯 query 变更的 `router.push` 需要 RSC 往返并参与导航队列，服务端繁忙或存在未完成导航时会偶发点击无响应（跨路由跳转如 `/login`、`/questions/[id]` 仍用 `router.push/replace`）。

### API、鉴权与数据

- 在 `main.py` 添加或调整接口时，使用 Pydantic schema 作为契约；明确状态码、错误语义和认证要求。
- 受保护接口使用 `get_current_user`；仅管理员操作使用 `require_admin`。题目和审核任务查询必须维持所有者范围，管理员才可跨用户查看。
- 项目管理员账号管理（`/api/project-users`）：项目管理员仅能查看、下发、编辑其所在项目内的普通用户；下发固定 `role="user"`，项目分配仅限请求者所在项目（越权 403）；编辑时保留请求者范围外的项目归属，非普通用户或不在其范围内的账号不可见（404）。角色升降仍为管理员专属；管理员全量账号管理继续走 `/api/users`。项目逻辑删除：管理员可删任意项目，项目管理员可删自己所在的项目（`DELETE /api/projects/{id}`，后端按 `project_scope` 校验）。用量分析（`GET /api/admin/project-costs`）：管理员可见全部项目与全部用户的调用成本汇总；项目管理员同样可访问，但按 `project_scope` 仅返回其所属项目的汇总，用户汇总也仅统计这些项目范围内的请求（无相关请求的用户不展示）；普通用户仍返回 403。前端“我的项目”页的“调用成本”按钮与项目卡片累计成本对 admin 和 project_admin 均展示。
- 个人题目上传上限：`User.question_upload_limit`（null=不限制），可在 `/api/users` 与 `/api/project-users` 的下发/编辑接口设置；`POST /api/questions` 按提交者名下题目总数门控，达上限或单次提交超出剩余配额时返回 409 并提示剩余可上传数量。账号列表接口附带 `questionCount`（已上传数）供前端展示消耗。设置上限时（`/api/users` 与 `/api/project-users` 的下发/编辑）会校验项目全部成员上限总和不超过项目题目目标（仅目标 > 0 的项目，未设上限的成员不计入），超出返回 409。
- 生效配额：项目管理员可在 `/api/project-users` 下发/编辑时设置 `dailyRequestLimit`、`monthlyRequestLimit`、`monthlyBudgetCny`（留空继承全局调用治理默认值）；列表与编辑响应均携带 `effectiveQuota`（含 user/global 来源标记），角色升降仍为管理员专属。
- 批量上传用户：`POST /api/users/batch`（管理员）与 `POST /api/project-users/batch`（项目管理员，项目仅限自己所在项目），Excel 固定表头仅“用户名、手机号”，日上限/月上限/月预算作为弹窗内统一输入框对本批次所有账号生效（留空继承全局默认值），初始密码统一为后端常量 `BATCH_DEFAULT_PASSWORD`（当前 12345678），账号固定 `role="user"`；用户名已存在或文件内重复的行跳过并在 `skipped` 中逐条说明，不阻断其余创建。手机号持久化在 `User.phone`（可空），下发（`/api/users`、`/api/project-users`）与编辑接口的 `phone` 字段同样支持填写与清空（传 null 清除）。
- 登录通过 HttpOnly session Cookie 工作。不要改为把令牌放入 localStorage，也不要在日志或响应中泄露 token、密码或上游 API key。
- 题目和审核 API 由 `services.py` 的 `question_json`、`check_result_json` 等函数统一序列化；新增字段时避免在路由中重复拼装不一致的 JSON。
- 模型调用、重试、并发和限流集中在 `services.py` 与 `config.py`。新增审核类型应经过队列、依赖激活、结果落库、完成状态和事件发送的完整链路。

### 队列与 SSE

- API 只创建 `CheckRun`/批次并入队，Worker 负责实际执行。保持两者可独立重启和幂等。
- 后端仓库不得复制 Worker 的模型 HTTP 调用、Prompt、租约恢复、重试、熔断或消费入口；后端仅保留工作项建模、入队、查询和 SSE，执行逻辑统一位于 `stem-system-worker/app`。
- 对可能重复提交的启动接口保留 `Idempotency-Key` 行为。
- 修改事件格式时同时检查后端 `run_events`/`emit`、Nginx 的 SSE 缓冲配置，以及前端订阅逻辑。
- 不要以同步 HTTP 等待外部 AI 完成为替代队列；超时、重试和租约恢复是系统可靠性的一部分。
- Worker 心跳键与工作项 `lease_owner` 必须同为 `worker-id:进程号` 格式（启动时在 `worker.py` 追加 PID）；两者不一致会导致 `recover_expired_leases` 把存活 Worker 的运行中任务误判为租约失效并重新入队，造成同一工作项被反复派发、调用台账出现多条同 attempt 的残留 `running` 记录。修改心跳或租约标识格式时，必须同步两端并运行 `test_worker_heartbeat.py` 的一致性用例。
- Worker 心跳（`stem:workers:heartbeat:*`，TTL 15 秒）必须由独立后台任务周期性刷新，不能只在调度主循环迭代时写入；否则所有并发槽位被长耗时模型调用占满、`asyncio.wait` 长时间不返回时心跳会断档，队列监控误报 Worker 离线。诊断 Worker 状态时先查该 Redis 键的 TTL，再看 `pg_stat_activity` 与 `check_work_items.updated_at` 是否仍在推进。
- Worker 完成事务必须校验工作项终态：重启/恢复可能造成同一工作项被重复执行，迟到的完成不得覆盖结果或再次触发比对/收尾级联（完成前检查 `status == running` 且 `completed_at` 为空）。同理 `update_assessment_progress` 等进度回执不得把已由比对定稿（pass/fail）的层级倒回 `equivalent=None` 的中间态，否则前端会永久显示“待比对”。
- 任务取消统一走后端 `cancel_check_run_core`（用户端 `/api/check-runs/{id}/cancel` 与队列监控 `/api/admin/queue/check-runs/{id}/cancel` 共用）：任务置 `cancelled`，queued/blocked/running 工作项作废并清租约，题目无其他活跃任务时复位 `pending`，并 emit `cancelled` 事件；对 completed/cancelled 幂等。管理端入口权限与“重新检测”一致（项目管理员仅限自己所在项目，越权 403/404）。
- 取消不能中断已发起的上游流式调用，其迟到结果由 Worker 完成事务的终态校验丢弃；丢弃结果不代表消耗未发生，对应 `ModelRequestLedger` 仍要经 `settle_discarded_ledger` 照常结算 Token 与成本（一行台账 = 一次真实上游请求），否则用量与成本统计偏低。`complete_run_if_ready` 对 `cancelled` 任务必须直接短路返回，防止迟到完成把已取消任务翻回 completed/manual_review。Redis 公平队列中残留的已取消条目无需清理，派发入口已有 `status != "queued"` 丢弃逻辑。
- 任务暂停/恢复统一走后端 `pause_check_run_core`/`resume_check_run_core`（队列监控 `POST /api/admin/queue/check-runs/{id}/pause`、`/resume`，权限与取消一致）：暂停时 run 置 `paused`，queued/blocked 工作项置 `paused`（清租约、不写 completed_at）；running 工作项不中断，跑完结果照常落库保留（上游调用本就无法中断），题目保持 `checking`；恢复时 paused 工作项回 `queued` 并按原 `available_at` 重新入队，仅 paused 可恢复（否则 409），暂停对 paused/completed/cancelled 幂等。暂停期间禁止对同题新建质检/改版本（后端活跃任务判定与冲突检测集合均包含 paused），暂停中的任务被取消时题目照常复位；Redis 公平队列中残留的已暂停条目同样无需清理，由派发门控的 `status != "queued"` 丢弃。
- 暂停期间 Worker 激活路径全部冻结：任何“置 queued + 入队”路径（下游激活、下一层入队、可重试退避重入队、租约过期回收、派发前探测）必须检查 run.status，暂停时经 `schedule_or_hold` 把工作项置 `paused` 且不入队；`recover_ready_dependencies` 与 stuck 恢复跳过 paused 任务；`complete_run_if_ready` 对 paused 短路返回、未完成集合包含 paused，`reconcile_orphaned_runs` 活跃工作集合包含 paused，防止暂停任务被误收尾。新增任何工作项入队路径时必须同步补上暂停门控。
- 台账行必须落到终态：Worker 关闭（CancelledError 路径）就地经 `mark_ledger_interrupted` 把被中断调用的台账结算为 `failed/error_code=execution_interrupted`（记输入估算、打估算标记）；进程被硬杀留下的孤儿行由 `settle_orphaned_ledgers` 在每轮恢复循环（含启动首轮）回收——条件为台账 running 且对应工作项不再 running。排查“台账出现多条同 attempt 记录”时，一条中断 + 一条重跑是重启的预期形态。

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

## 项目知识沉淀

- 自动沉淀机制：Agent 在每完成一个独立任务（如功能开发、排障、架构重构、Prompt 调优）后，须自动评估是否有具备长久复用价值的知识或约束，并在任务交付时自动写入落盘到本文件的相关章节中，无需用户重复提醒。
- handover 交接文档同步：每完成一次功能修改，除代码提交外，还须同步更新受影响组件仓库的 `handover.md`（`stem-system-backend/app/handover.md`、`stem-system-frontend/app/handover.md`、`stem-system-worker/app/handover.md`），在对应章节（通常为“关键业务规则”）补充或修正行为变更说明，使交接文档与最新实现保持一致；修正过时描述时只改相关条目，不做无关重排。handover 改动随代码一并提交推送到对应 GitLab 仓库。
- 每次在开发、排障、部署或模型联调中确认了可复用的事实、约束或操作方式，都应在本文件的相应章节补充简洁说明；避免记录密钥、Cookie、个人信息和临时日志。
- 外部 AI 协同上下文沉淀：与 ChatGPT 等外部 AI 对话产生的架构决策、Prompt 调试或新规范，须显式导出为 Markdown 片段并追加沉淀到本文件中，确保各 AI Agent 可跨会话继承上下文记忆。
- 新知识应说明适用组件、行为或限制，以及必要时的验证方式，方便后续维护者直接查阅；过期或被新实现替代的信息应同步更新。
- 已验证的网关并发规则：`APIROUTE_API_KEYS` 可按逗号拆分，但若最终复用同一上游额度，不能提高实际吞吐。所有 APIRoute 模型共享一套全局额度，再叠加厂商额度；额度、价格和公平份额统一由数据库中的 `model_governance` 配置管理，不再使用环境变量 `AI_LIMIT_*`。
- 难度分级评测：新题导入或保存新版本后会创建独立的 `difficulty_assessment` 队列任务，按 L0（本地 Markdown/LaTeX 规则校验 + AI 合成题检测，两者均到达终态后才晋级）→L1（过易筛选）→L2（难度复核）→L3（终极定级）运行。各层判定统一为“答对门槛”（Worker `assessment_condition` 比较答对次数、`assessment_stop_level` 决定定级）：满足 = 答对次数在门槛之内，题目配得上该层难度，晋级下一层，末层满足即定级 L3；不满足 = 题目过易，降一级定级（L1→L0、L2→L1、L3→L2）。旧“过易线”（命中=降级、未命中=晋级）与更旧“L2/L3 命中=定级入库”语义均已废弃，不得重新引入。策略在创建时写入 `CheckRun.model_versions` 快照，运行中不得读取或改用新的全局策略。分层策略的答对门槛阈值不得大于该层模型运行总次数（后端 `normalize_difficulty_policy` 强制校验，前端同步提示并禁止保存）。答对门槛操作符支持 `>=`、`>`、`=`、`<=`、`<` 五种，统一以“答对次数”为口径比较阈值（Worker `assessment_condition`）；旧“不等价次数”口径已于 2026-08 完成等价换算迁移（全局策略与在途快照），不得重新引入。
- 分级评测空答案作答：完成但无结果（如输出超限被截断）的作答按答错计、正常进入比对，不得因此整层判败；比对 payload 只含有结果的作答（按 attempt 排序），定稿时空答案位补 False；仅当整层全部作答都无有效结果时才判败转人工。历史遗留的卡“待比对”任务用 Worker `scripts/repair_stuck_equivalence.py` 修复；复位工作项时必须同时清空 `completed_at`，否则完成事务的终态校验会把 completed_at 非空的 running 工作项当作迟到重复完成而丢弃新结果，永远卡 running。
- L0 双检测门控：后端创建评测时同时生成 `assessment_format`（规则）与 `assessment_synthesis`（固定 `deepseek-v4-flash`）两个 L0 工作项；Worker 的 `finalize_assessment_l0` 在两者均到达终态后才创建首个作答层。LaTeX 格式错误仍将评测终定为 `format_failed`；AI 合成题检测失败（含重试耗尽）不阻塞分级，在 detail 的 L0 层记录 `synthesis.result="error"` 后继续晋级。前端不再有“全量质检”入口，也不再展示独立的 LaTeX/合成题质检卡片，题目详情页质检结果区只保留难度分级评测卡片（L0 层内含两项检测结果），按钮为“开始 L0 检测”；版本质检汇总（checkSummary）以分级评测结果为准，历史独立的 latex/synthesis 结果仍兼容纳入汇总判定。
- LaTeX 格式校验为 Worker 纯规则引擎（`latex_check`，不调模型），规则覆盖：分隔符配对（忽略 `\$` 与 `\left[\right]`）、begin/end 环境名一致、公式内变量与数字直接相邻（如 `x1` 应写 `x_1`）、公式内中文/全角字符（`\text{}` 豁免）、未知命令拼写（内置常用命令白名单，新增合法命令时须同步扩充）、`\frac` 等缺花括号参数、花括号不成对、`\left/\right` 不配对、正文未转义 `%`/`&`/`#`；修改规则后须用存量题库回归，避免误伤规范写法。
- 分级版本隔离：`CheckRun.question_version` 与工作项 payload 的 `questionVersion` 固化题目版本；旧版本尚未完成的评测只能写入该历史版本的结果，不能覆盖当前题目的 `difficulty_level`、`difficulty_status` 或当前分级引用。
- 重新分级/重新检测增量复用：触发重试时只把失败、超时或未完成的作答工作项重新入队；已成功完成且 `result.answer` 有效的作答按 `(level, 模型, modelAttempt)` 匹配后直接继承（置 `completed`、拷贝结果与耗时、payload 标记 `inheritedFrom`），跳过 Redis 入队以节省上游配额。复用范围仅限同题同检查项的最近一次历史 `CheckRun`，且必须满足版本隔离；若所有作答均被继承，比对任务（`equivalence`/`assessment_equivalence`）由后端或 Worker 直接唤醒，不得遗留永久阻塞。
- APIRoute 模型接入：新增 OpenAI-compatible 网关模型时，须同步更新前端、后端与 Worker 的 `audit_models` 目录；统一使用 `provider="apiroute"`，共享 `APIROUTE_API_KEYS` 和 APIRoute 并发/限流通道。未提供专属规则时，默认 Pass@K 3、难度答对阈值 ≤2，深度思考参数保持关闭。
- 网关连接排障：`network_error: ConnectError` 表示连接层在获得 HTTP 响应前失败，属于可重试错误；Worker 会按指数退避重新进入公平队列。可用 `stem-system-worker/app/scripts/probe_apiroute_synthesis.py --smoke` 发送不含题目内容的流式探针，记录 DNS、响应头与首个流式片段耗时，且不会输出密钥。
- 网关 403 排障：请求约 100ms 内返回 `403 Forbidden` 时，必须读取响应 body 判定具体原因：`insufficient_user_quota`（如“用户额度不足，剩余额度: ¥-x”）表示 APIRoute 账户余额耗尽，所有模型调用（含不含题目内容的 `--smoke` 探针）都会被拒，需充值或更换 `APIROUTE_API_KEYS`，不是内容审核或代码问题。切勿仅凭“秒回 403”就归因为内容审核。
- 错误文案面向用户：Worker `provider_error` 通过 `friendly_http_message` 解析上游网关 JSON 错误体，把 `insufficient_user_quota`、鉴权失败等错误翻译成可操作的中文说明后才落库，前端（如“分级评测失败”提示）不应出现 httpx 原始信息（状态码 + 内部 URL）；新增上游错误码时须在该映射中同步补充。注意 httpx 流式响应不会自动读体，`raise_for_status()` 前必须对非 2xx 响应先 `aread()`，否则错误翻译读不到响应体。

### 高吞吐调度与成本治理

- 所有外部模型调用先同时获取 APIRoute 全局与厂商两层额度。默认全局为并发 12、RPM 36、TPM 480,000；厂商默认额度见管理员“模型管理 / 调用治理与成本”，仅允许在有上游配额依据时调整。
- 队列调度单位是单个工作项，优先级内按“项目 → 用户 → 工作项”轮转。同一用户每轮只派发一个工作项；存在其他等待者时，单项目最多占用 8 个外部槽位、单用户最多占用 3 个。无竞争时允许借用空闲槽位。
- 解题/分级作答、AI 合成题检测、答案或相似题比对的 TPM 输出预留分别为 32,768、8,192、2,048；只用于令牌预算，严禁作为 `max_tokens` 或 `max_completion_tokens` 传给上游。例外：解题/分级作答的输出上限由治理配置 `models[].solveMaxTokens` 管理（管理员“模型管理”页可手动设置或清空），默认值 deepseek-v4-flash/pro=393216（官方 384K 输出上限，1M 上下文）、qwen3.7-plus=131072（官方 128K），其余模型默认留空不传；目的是避免长思考耗尽输出预算导致答案被截断，仅对 APIRoute 网关模型生效。
- 每次真实上游请求（包括重试）会写入 `model_request_ledgers`，固化 Token、耗时、状态、错误、价格、汇率和成本快照。优先记录上游 usage；缺失时回退本地估算并标记“估算”，输出估算须把响应中的思考文本（`reasoning_content`）一并计入，否则思考模型成本会被严重低估。思考 Token 单列展示，不重复计入输出计费。
- 仅 `408/429/500/502/503/504` 和连接/超时/协议中断等临时网络错误可重试；最多 5 次重试，退避 `2/4/8/16/32` 秒并加 `0~1` 秒抖动。`400/401/403/404/405/409/410/413/415/422`、无效模型/参数/响应结构和缺失密钥直接失败；退避期间不占任何执行槽位。
- 账号配额强制生效：Worker 在派发每个模型调用前执行 `user_quota_violation` 门控，按 `model_request_ledgers` 统计归属用户的当日/当月请求数（含重试与失败）和当月累计成本；生效值 = 账号专属值优先，未设置取治理配置 `userQuotaDefaults`（在管理员“模型管理 / 调用治理”的“全局账号配额默认值”区块维护），0 表示不限制。超额时工作项保持 `queued` 并标记 `quota_exceeded`，每 60 秒重试一次，不记失败、不转人工，额度恢复（次日/下月/调高配置）后自动继续。规则类阶段（如 L0 LaTeX）不经门控。

### 模型思考参数

- 唯一实现来源：`stem-system-worker/app/app/services.py`；模型目录以三端各自的 `audit_models.py` 为准。
- L0 包含本地 Markdown/LaTeX 校验（不调用模型）与 AI 合成题检测（固定使用 `deepseek-v4-flash`，由三端 `audit_models.py` 的 `SYNTHESIS_AUDIT_MODEL_ID` 与后端 `DEFAULT_GLOBAL_AUDIT_MODEL_IDS["synthesis"]` 共同决定，修改时须三处同步）；两者均为 `difficulty_assessment` 的 L0 工作项。
- 所有模型均通过 OpenAI-compatible APIRoute 网关请求；逻辑 Provider 仅用于区分参数和限流策略。
- 解题 `max_tokens` 不再硬编码：Worker 派发每个工作项时实时读取治理配置 `solveMaxTokens`，留空则不传；管理员在“模型管理”页修改保存后对新派发的工作项即时生效，无需重启或改代码。

| 模型 | 解题 / 难度分级作答 | 答案比对 | AI 合成题检测 | 流式策略 |
| --- | --- | --- | --- | --- |
| doubao-2.0-pro、doubao-2.1-pro | `thinking.type=enabled`；`reasoning.effort=high` | `thinking.type=disabled` | `thinking.type=enabled`；`reasoning.effort=medium` | 三种场景均流式 |
| gemini-3.1-pro | `thinking.type=enabled`；`reasoning.effort=high` | `thinking.type=disabled` | `thinking.type=enabled`；`reasoning.effort=medium` | 当前均非流式 |
| kimi-k3 | `reasoning_effort=max` | `reasoning_effort=low` | `reasoning_effort=high` | 仅解题流式 |
| qwen3.7-plus | `enable_thinking=true`；`reasoning_effort=xhigh`；`temperature=0.7`；`max_tokens` 取自治理配置 `solveMaxTokens`（默认 131072，官方 128K 输出上限） | `enable_thinking=false`；`temperature=0.1` | `enable_thinking=true`；`reasoning_effort=medium` | 仅解题流式 |
| qwen3.7-max、qwen3.8-max | `enable_thinking=true`；`reasoning_effort=xhigh`；`temperature=0.7` | `enable_thinking=false`；`temperature=0.1` | `enable_thinking=true`；`reasoning_effort=medium` | 仅解题流式 |
| deepseek-v4-flash、deepseek-v4-pro | `thinking.type=enabled`；`reasoning_effort=max`；`temperature=0.7`；`max_tokens` 取自治理配置 `solveMaxTokens`（默认 393216，官方 384K 输出上限） | `thinking.type=disabled`；`temperature=0.1` | `thinking.type=enabled`；`reasoning_effort=high` | 仅解题流式 |
| claude-sonnet-5 | `output_config.effort=max` | `thinking.type=disabled` | `output_config.effort=high` | 仅解题流式 |

- `glm-5.2` 已移除，不得重新加入默认策略或模型目录，除非完成可用性验证并明确授权。
