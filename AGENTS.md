# STEM 题目审核系统：代理工作指南

## 项目概览

这是一个题目导入、异步 AI 审核和结果查看系统：

- 前端：Next.js 16、React 19、TypeScript、Ant Design、SWR、KaTeX。
- 后端：FastAPI、SQLAlchemy async、Alembic、PostgreSQL、Redis。
- 异步任务：Redis 队列由独立 Worker 消费；审核进度通过 SSE 推送。
- 部署：开发环境由 Next.js 将同域 `/api/*` 重写至 FastAPI；生产环境由 Nginx 转发并透传 SSE。

## 目录与职责

活代码位于三个独立仓库（启动方式见“本地开发”）；根仓库 `backend/` 是历史单体快照，`src/`、`deploy/` 已不存在，均非活代码：

| 路径 | 职责 |
| --- | --- |
| `stem-system-frontend/app/src/app/` | App Router 页面、布局及全局样式。 |
| `stem-system-frontend/app/src/components/` | 可复用的前端交互组件，例如导入、账号管理和公式渲染。 |
| `stem-system-frontend/app/src/lib/check-runs.ts` | 审核任务创建、查询与 SSE 订阅客户端。 |
| `stem-system-frontend/app/src/types/index.ts` | 前端共享类型。修改接口字段时同步更新。 |
| `stem-system-backend/app/app/main.py` | FastAPI 生命周期、路由、鉴权入口和 SSE API。 |
| `stem-system-backend/app/app/models.py` / `schemas.py` / `services.py` | SQLAlchemy 数据模型、Pydantic 契约、审核编排与序列化。 |
| `stem-system-backend/app/alembic/versions/` | 仅追加的数据库迁移。 |
| `stem-system-worker/app/app/worker.py` / `services.py` | Redis 队列消费循环、模型调用、重试与熔断。 |

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
| 后端 API | `stem-system-backend/app` | `env DATABASE_URL='postgresql+asyncpg://pikachu@localhost:5432/stem_audit' REDIS_URL='redis://localhost:6379/0' /opt/anaconda3/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000` |
| Worker | `stem-system-worker/app` | `env DATABASE_URL='postgresql+asyncpg://pikachu@localhost:5432/stem_audit' REDIS_URL='redis://localhost:6379/0' /opt/anaconda3/bin/python -m app.worker` |

- 本项目日常本机调试**不使用 Docker**，前端固定使用 `3000` 端口；如端口已被占用，应先确认并停止原有进程，不要改为 `3001`。
- 启动顺序为：后端 API、Worker、前端；三项分别在独立终端运行。前端访问地址为 `http://127.0.0.1:3000`，后端文档为 `http://127.0.0.1:8000/docs`。
- 后端和 Worker 使用 Conda 的 `/opt/anaconda3/bin/python` 及本机 PostgreSQL 数据库 `stem_audit`、本机 Redis。后端与 Worker 的 `DATABASE_URL` 必须指向同一个库（曾发生 Worker 照旧命令连到空库 `stem`、任务全部排队不动的故障）；排障"任务一直排队"时先核对两端进程环境变量中的库名是否一致。API Key 仅从各组件本地忽略的环境变量文件读取，禁止写入命令、代码、日志或提交。
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
- 题目详情页停留期间必须自动更新任务状态：Worker 的分级评测进度事件不带 `checkType`（只含 `difficultyStatus`/`currentLayer`/`detail`），SSE 回调不能只处理带 `checkType` 的 progress，否则评测卡片头部状态永远停在旧值；收到此类事件及 cancelled/paused/resumed 时应 `mutate()` 重取题目数据，并在存在活跃任务时开启 SWR 轮询兜底（详情页为 5 秒）。
- 数学内容通过 `LatexRenderer` 呈现。修改公式处理时运行 Worker 的 LaTeX 测试（`stem-system-worker/app/tests/test_latex.py`），并注意不可信题干的渲染安全。
- UI 保持 Ant Design 现有风格；避免无关的全局 CSS 改动和大范围重排。antd 的 Space 组件须使用 `orientation` 属性，`direction` 已弃用会触发控制台警告。
- 同路由内的查询参数导航（顶部标签页 `/?view=...`、项目卡片 `/?projectId=...`、面包屑返回 `/`）统一用 `window.history.pushState`，Next.js 会同步 `useSearchParams`；不要用 `router.push`：纯 query 变更的 `router.push` 需要 RSC 往返并参与导航队列，服务端繁忙或存在未完成导航时会偶发点击无响应（跨路由跳转如 `/login`、`/questions/[id]` 仍用 `router.push/replace`）。

### API、鉴权与数据

- 在 `main.py` 添加或调整接口时，使用 Pydantic schema 作为契约；明确状态码、错误语义和认证要求。
- 受保护接口使用 `get_current_user`；仅管理员操作使用 `require_admin`。题目和审核任务查询必须维持所有者范围，管理员才可跨用户查看。
- 全局功能开关统一存放 `system_settings` 的 `feature_flags` 键（缺省视为开启），管理员经 `GET/PUT /api/admin/feature-flags` 读写，改后即时生效无需重新部署。相似度检测总开关（`similarityDetection`）关闭后：上传/保存新版本不再触发异步相似题检测（直接标 clear）、质检不受相似校验状态锁定、巡检卡死修复直接解锁；上传同步 n-gram 拦截（纯本地去重）不受开关影响。
- 项目管理员账号管理（`/api/project-users`）：项目管理员仅能查看、下发、编辑其所在项目内的普通用户；下发固定 `role="user"`，项目分配仅限请求者所在项目（越权 403）；编辑时保留请求者范围外的项目归属，非普通用户或不在其范围内的账号不可见（404）。角色升降仍为管理员专属；管理员全量账号管理继续走 `/api/users`。项目逻辑删除：管理员可删任意项目，项目管理员可删自己所在的项目（`DELETE /api/projects/{id}`，后端按 `project_scope` 校验）。用量分析（`GET /api/admin/project-costs`）：管理员可见全部项目与全部用户的调用成本汇总；项目管理员同样可访问，但按 `project_scope` 仅返回其所属项目的汇总，用户汇总也仅统计这些项目范围内的请求（无相关请求的用户不展示）；普通用户仍返回 403。前端“我的项目”页的“调用成本”按钮与项目卡片累计成本对 admin 和 project_admin 均展示。
- 个人题目上传上限：`User.question_upload_limit`（null=不限制），可在 `/api/users` 与 `/api/project-users` 的下发/编辑接口设置；`POST /api/questions` 按提交者名下题目总数门控，达上限或单次提交超出剩余配额时返回 409 并提示剩余可上传数量。账号列表接口附带 `questionCount`（已上传数）供前端展示消耗。设置上限时（`/api/users` 与 `/api/project-users` 的下发/编辑）会校验项目全部成员上限总和不超过项目题目目标（仅目标 > 0 的项目，未设上限的成员不计入），超出返回 409。
- 生效配额：项目管理员可在 `/api/project-users` 下发/编辑时设置 `dailyRequestLimit`、`monthlyRequestLimit`、`monthlyBudgetCny`（留空继承全局调用治理默认值）；列表与编辑响应均携带 `effectiveQuota`（含 user/global 来源标记），角色升降仍为管理员专属。
- 批量上传用户：`POST /api/users/batch`（管理员）与 `POST /api/project-users/batch`（项目管理员，项目仅限自己所在项目），Excel 固定表头仅“用户名、手机号”，日上限/月上限/月预算作为弹窗内统一输入框对本批次所有账号生效（留空继承全局默认值），初始密码统一为后端常量 `BATCH_DEFAULT_PASSWORD`（当前 12345678），账号固定 `role="user"`；用户名已存在或文件内重复的行跳过并在 `skipped` 中逐条说明，不阻断其余创建。手机号持久化在 `User.phone`（可空），下发（`/api/users`、`/api/project-users`）与编辑接口的 `phone` 字段同样支持填写与清空（传 null 清除）。
- 登录通过 HttpOnly session Cookie 工作。不要改为把令牌放入 localStorage，也不要在日志或响应中泄露 token、密码或上游 API key。
- 登录失败限流（安全测试 SEC-01 修复）：后端 `POST /api/auth/login` 对失败次数按账号（默认 5 次）与 IP（默认 20 次）双维度计数（Redis `stem:login_attempts:*`，窗口 `login_lock_minutes` 默认 15 分钟），达阈后任何登录（含正确密码）返回 429 + `Retry-After`，成功登录清零计数，Redis 不可用时降级放行；锁定文案不得区分维度（防账号枚举）。新增登录失败分支不得绕过计数。
- `auth_secret` 部署红线（安全测试 SEC-04）：代码内置默认密钥仅限本地开发，不得用于任何对外环境；`environment=production` 时 `config.py` 已强制要求经环境变量注入 ≥ 32 位密钥否则启动失败。生产部署必须 `openssl rand -hex 32` 生成并覆盖 `AUTH_SECRET`，否则攻击者可用仓库中的默认密钥伪造任意账号（含管理员）会话令牌。不要把“移除默认值/启动即失败”改回宽松行为，除非产品明确要求。
- 令牌版本号吊销机制（`User.token_version`，迁移 `20260806_22`）：解决无状态 HMAC 令牌签发后登出/改密无法失效的缺陷。签发 Token 时在 Payload 写入 `ver`（默认为用户 `token_version`）；在 `get_current_user` 校验时比对 `token_ver == user.token_version`。用户登出（`POST /api/auth/logout`）、管理员重置密码（`PUT /api/users/{user_id}/password`）或更新账号密码/激活状态/角色时，在 DB 中使 `token_version += 1` 并提交，使该账号所有已有 Token（包含泄露的旧令牌）立即全部失效，防止越权或旧会话复用。
- 题目和审核 API 由 `services.py` 的 `question_json`、`check_result_json` 等函数统一序列化；新增字段时避免在路由中重复拼装不一致的 JSON。
- 模型调用、重试、并发和限流集中在 `services.py` 与 `config.py`。新增审核类型应经过队列、依赖激活、结果落库、完成状态和事件发送的完整链路。
- 相似题检测分两层：上传时同步拦截 + 上传后异步模型判定。上传拦截（`POST /api/questions` 的 `filter_similar_uploads`）只做本地 n-gram 词汇召回，阈值 `SIMILARITY_UPLOAD_BLOCK_THRESHOLD=0.5`（高于异步召回线 0.18，仅拦近乎相同者，同批次已接受项也在内存互查），被拦条目不导入并随响应 `skippedSimilar` 返回（`skipped` 仍仅统计精确重复；上传去重是精确文本匹配，仅空白差异的同题靠拦截/相似题检测兜底）；改写幅度较大的同题不拦，由上传后异步模型判定标记。召回依赖 `question_ngrams` n-gram 索引，功能上线前入库的历史题目曾无索引导致召回为空直接判 `clear` 的盲区（题目 127 与题目 8 同题未发现）；用 `stem-system-backend/app/scripts/backfill_question_ngrams.py` 幂等补齐索引，`--rescan <题目id>` 可清理旧 similarity 任务（idempotency_key 冲突，关联记录级联删除）后重新召回判定；重扫时 `CheckRun.requested_by_user_id` 须传真实用户或 None，外键不接受不存在的 id。`similarity_status` 四种终态语义：`checking`（进行中）→ 成功写 `clear`/`suspected`，工作项进 manual_review/dead 写 `failed`（Worker `complete_run_if_ready` 必须覆盖失败路径，否则题目永久卡 checking 无法质检）；三种非 clear 态都锁定质检，管理员可经 `PUT /api/questions/{id}/similarity-resolution` 解除，检测失败另可复位工作项（清 error/completed_at/租约并同步重入 Redis 公平队列四个索引）重跑。
- 已定级且合格的题目禁止重新质检（后端 `require_recheck_allowed`，判定复用 `question_is_qualified`：当前版本基础检测通过且分级完成、难度 ≥ L2）：单题质检（`POST /api/questions/{id}/check`）、难度分级（`/difficulty-assessments`，force 不绕过）与队列重试（`/api/admin/queue/check-runs/{id}/retry`）命中直接 409；批量质检（`POST /api/check-batches`）自动剔除合格题目继续执行剩余部分，被剔除题目随响应 `skippedQuestionIds` 返回，全部命中则 409。仅 `role == "admin"` 豁免，项目管理员与普通用户一律拦截。改版本天然放行：保存新版本使 `current_version += 1`，新版本无检测结果即不再合格，无需额外重置逻辑。上传/保存新版本自动触发的相似题检测不经过这些入口，不受影响。前端须同步门控：详情页分级卡“重新检测”按钮禁用并 tooltip 说明，批量确认框提示将跳过的合格题数量、提交后按 `skippedQuestionIds` 提示并修正 run 映射/进度总数。

### 队列与 SSE

- API 只创建 `CheckRun`/批次并入队，Worker 负责实际执行。保持两者可独立重启和幂等。
- 后端仓库不得复制 Worker 的模型 HTTP 调用、Prompt、租约恢复、重试、熔断或消费入口；后端仅保留工作项建模、入队、查询和 SSE，执行逻辑统一位于 `stem-system-worker/app`。
- 对可能重复提交的启动接口保留 `Idempotency-Key` 行为。
- 修改事件格式时同时检查后端 `run_events`/`emit`、Nginx 的 SSE 缓冲配置，以及前端订阅逻辑。
- SSE 历史重放防陈旧 complete：`run_events` 回放历史事件时，任务仍处活跃状态（queued/running/cancelling/paused）的 `complete` 事件必为陈旧残留（旧判败后任务被恢复继续执行），必须跳过；前端收到 `complete` 事件也要先用 `getCheckRun` 持久化状态校准，任务仍活跃则忽略。否则陈旧 complete 会触发“任务失败”提示 → mutate → 订阅重建 → 重连全量重放的提示循环（题目 39 案例）；前端详情页订阅 effect 依赖须用 run id 集合字符串（`activeRunKey`），不能用 SWR 每次刷新都会变化的 `activeRuns` 数组引用。
- 不要以同步 HTTP 等待外部 AI 完成为替代队列；超时、重试和租约恢复是系统可靠性的一部分。
- Worker 健康判定不能只看心跳：心跳由独立协程写入，只证明进程存活。调度主循环每轮向 `stem:workers:loop:{worker-id}` 写拍点，心跳协程校验拍点新鲜度，超过 `WORKER_LOOP_WATCHDOG_SECONDS`（默认 120s，<=0 关闭）即判定主循环冻结并自杀，由容器/守护进程重启策略拉起，新进程自动回收租约续跑。`asyncio.wait` 带 5 秒超时，保证合法忙碌时拍点不饥饿。
- Worker 心跳键与工作项 `lease_owner` 必须同为 `worker-id:进程号` 格式（启动时在 `worker.py` 追加 PID）；两者不一致会导致 `recover_expired_leases` 把存活 Worker 的运行中任务误判为租约失效并重新入队，造成同一工作项被反复派发、调用台账出现多条同 attempt 的残留 `running` 记录。修改心跳或租约标识格式时，必须同步两端并运行 `test_worker_heartbeat.py` 的一致性用例。
- Worker 心跳（`stem:workers:heartbeat:*`，TTL 15 秒）必须由独立后台任务周期性刷新，不能只在调度主循环迭代时写入；否则所有并发槽位被长耗时模型调用占满、`asyncio.wait` 长时间不返回时心跳会断档，队列监控误报 Worker 离线。诊断 Worker 状态时先查该 Redis 键的 TTL，再看 `pg_stat_activity` 与 `check_work_items.updated_at` 是否仍在推进。
- Worker 完成事务必须校验工作项终态：重启/恢复可能造成同一工作项被重复执行，迟到的完成不得覆盖结果或再次触发比对/收尾级联（完成前检查 `status == running` 且 `completed_at` 为空）。同理 `update_assessment_progress` 等进度回执不得把已由比对定稿（pass/fail）的层级倒回 `equivalent=None` 的中间态，否则前端会永久显示“待比对”。
- 任务取消统一走后端 `cancel_check_run_core`（用户端 `/api/check-runs/{id}/cancel` 与队列监控 `/api/admin/queue/check-runs/{id}/cancel` 共用）：任务置 `cancelled`，queued/blocked/running 工作项作废并清租约，题目无其他活跃任务时复位 `pending`，并 emit `cancelled` 事件；对 completed/cancelled 幂等。若取消的正是题目当前分级任务（`question.difficulty_run_id == run.id`），还须把 `difficulty_status` 置 `cancelled`、清空 `difficulty_level`，并把该 run 的分级 `check_result` 明细落 `cancelled`，否则详情页永远停在“评测中”且重新检测按钮永久置灰；前端分级状态文案/颜色/筛选须支持 `cancelled`。管理端入口权限与“重新检测”一致（项目管理员仅限自己所在项目，越权 403/404）。
- 取消与晋级/执行的竞态防护（Worker 两道兜底，不得移除）：① 取消时晋级事务可能尚未提交（如 L1 比对定稿在取消后才创建下一层工作项并入队）；② 取消时 running 的作答跑完后迟到返回。因此 Worker 派发时必须检查 `run.status == "cancelled"` 则作废工作项不发起调用；完成事务在工作项终态校验通过后还须再查 run 是否已取消，是则工作项置 `cancelled`、丢弃结果（台账照常结算）且不触发比对/晋级级联。两端 `db.py` 均为 `expire_on_commit=False`，长耗时模型调用返回后的事务块必须先 `session.expire_all()` 再重读工作项/任务行，否则 identity map 里的过期内存对象会绕过终态校验，把取消期间已置 cancelled 的任务翻回 completed（回归用例 Worker `tests/test_stale_identity_map.py`）。
- 取消不能中断已发起的上游流式调用，其迟到结果由 Worker 完成事务的终态校验丢弃；丢弃结果不代表消耗未发生，对应 `ModelRequestLedger` 仍要经 `settle_discarded_ledger` 照常结算 Token 与成本（一行台账 = 一次真实上游请求），否则用量与成本统计偏低。`complete_run_if_ready` 对 `cancelled` 任务必须直接短路返回，防止迟到完成把已取消任务翻回 completed/manual_review。Redis 公平队列中残留的已取消条目无需清理，派发入口已有 `status != "queued"` 丢弃逻辑。
- 删除题目（`DELETE /api/questions/{id}`）须先对该题所有活跃任务逐个执行 `cancel_check_run_core`（作废未终态工作项、清租约、emit cancelled）再物理删除，不得直接级联了事。`ModelRequestLedger` 对题目/任务/工作项的三个外键均为 SET NULL（迁移 20260806_21）：删题只置空关联不删台账，在途上游调用的成本在迟到返回时照常结算；Worker 完成事务与异常路径必须对工作项行不存在判空兜底（完成丢弃结果并结算台账、异常将台账落为 failed），不得对 None 解引用。
- 任务暂停/恢复统一走后端 `pause_check_run_core`/`resume_check_run_core`（队列监控 `POST /api/admin/queue/check-runs/{id}/pause`、`/resume`，权限与取消一致）：暂停时 run 置 `paused`，queued/blocked 工作项置 `paused`（清租约、不写 completed_at）；running 工作项不中断，跑完结果照常落库保留（上游调用本就无法中断），题目保持 `checking`；恢复时 paused 工作项回 `queued` 并按原 `available_at` 重新入队，仅 paused 可恢复（否则 409），暂停对 paused/completed/cancelled 幂等。暂停期间禁止对同题新建质检/改版本（后端活跃任务判定与冲突检测集合均包含 paused），暂停中的任务被取消时题目照常复位；Redis 公平队列中残留的已暂停条目同样无需清理，由派发门控的 `status != "queued"` 丢弃。
- 暂停期间 Worker 激活路径全部冻结：任何“置 queued + 入队”路径（下游激活、下一层入队、可重试退避重入队、租约过期回收、派发前探测）必须检查 run.status，暂停时经 `schedule_or_hold` 把工作项置 `paused` 且不入队；`recover_ready_dependencies` 与 stuck 恢复跳过 paused 任务；`complete_run_if_ready` 对 paused 短路返回、未完成集合包含 paused，`reconcile_orphaned_runs` 活跃工作集合包含 paused，防止暂停任务被误收尾。新增任何工作项入队路径时必须同步补上暂停门控。
- 台账行必须落到终态：Worker 关闭（CancelledError 路径）就地经 `mark_ledger_interrupted` 把被中断调用的台账结算为 `failed/error_code=execution_interrupted`（记输入估算、打估算标记）；进程被硬杀留下的孤儿行由 `settle_orphaned_ledgers` 在每轮恢复循环（含启动首轮）回收——条件为台账 running 且对应工作项不再 running。排查“台账出现多条同 attempt 记录”时，一条中断 + 一条重跑是重启的预期形态。

### 健康巡检

- 入口：管理员顶部导航“健康巡检”（`/?view=health`，仅全局 admin），前端 `HealthPage` 30 秒 SWR 轮询 `GET /api/admin/health`；人工修复走 `POST /api/admin/health/repair`（body `{category, id}`，id 语义随类别变化：题目 ID 或工作项 ID）。
- 异常为实时扫描不建表：后端 `scan_health` 每次请求现查 DB+Redis，返回异常清单（含 severity/summary/detail）与三项应为 0 的指标（未定稿任务、未结算台账、Worker 心跳）。
- 自动/人工处置边界：`similarity_stuck`（checking 但无活跃 similarity 工作项）Worker 每轮巡检自动写回 `failed`，人工“重新校验”经 `create_similarity_run` 重跑；`lost_queue_item`（queued 到期但不在 Redis 公平队列）由既有 `recover_queued_work` 全量幂等补投自动修复，无需重复实现；`redis_orphan_entry`（队列成员在 DB 已终态/不存在）Worker `cleanup_orphaned_queue_entries` 自动 ZREM；`stalled_blocked`/`long_queued`（超 30 分钟）仅报告不自动修复，避免误伤 quota 等待（long_queued 扫描排除 `error_code='quota_exceeded'`）与依赖编排。
- 人工修复分发器（后端 `repair_health_anomaly`）必须带状态守卫：similarity 仅接受 checking/failed 题；重入队仅接受 queued；stalled_blocked 重置前须确认同 run+check_type 无未完成作答工作项（依赖未满足拒绝，防误触发模型调用）；相似度重跑传 `requested_by_user_id=None`（管理员修复不占用户配额）。新增巡检/修复路径必须幂等，可重复执行不产生副作用。

### 数据库迁移

- 变更 `models.py` 中的持久化结构时，创建新的 Alembic revision，禁止修改已提交的迁移文件。
- 迁移应能从空库顺序执行，并同时考虑 API 与 Worker 可能并发运行的兼容性。
- 使用 `alembic upgrade head` 验证迁移；不要执行会删除真实数据的操作，除非用户明确授权。
- 上线流水线没有人工执行 SQL 的环节：**配置数据类**的一次性修复（如网关模型 ID 改名同步 `system_settings`）不建 Alembic revision，而是放在后端启动初始化阶段幂等执行（参考 `migrate_legacy_gateway_model_ids`），无旧数据时不写库；历史任务快照保留旧值属设计预期。

## 验证与交付

根据改动范围运行最小且充分的检查：

| 改动 | 至少执行 |
| --- | --- |
| 前端 TypeScript/样式/组件 | `npm run lint`；涉及构建、路由或配置时再执行 `npm run build`（cwd `stem-system-frontend/app`）。 |
| 后端业务、模型调用或 LaTeX | 在 `stem-system-backend/app` 或 `stem-system-worker/app` 执行 `pytest` 和 `ruff check .`。 |
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
- 难度分级评测：新题导入或保存新版本后会创建独立的 `difficulty_assessment` 队列任务，按 L0（本地 Markdown/LaTeX 规则校验 + AI 合成题检测，两者均到达终态后才晋级）→L1（过易筛选）→L2（难度复核）→L3（终极定级）运行。各层判定统一为“答对门槛”（Worker `assessment_condition` 比较答对次数、`assessment_final_level` 决定停止时的定级）：满足 = 答对次数在门槛之内，题目配得上该层难度，晋级下一个启用层，最高启用层满足即定级该层；不满足 = 题目过易，降一级定级（L1→L0、L2→L1、L3→L2）。每层“启用”开关可停用该层（后端 `normalize_difficulty_policy` 强制启用层为自 L1 起的连续前缀）：停用层不产生作答任务、晋级跳过停用层，仅影响之后创建的新任务（快照语义）。旧“过易线”（命中=降级、未命中=晋级）与更旧“L2/L3 命中=定级入库”语义均已废弃，不得重新引入。策略在创建时写入 `CheckRun.model_versions` 快照，运行中不得读取或改用新的全局策略。分层策略的答对门槛阈值不得大于该层模型运行总次数（后端 `normalize_difficulty_policy` 强制校验，前端同步提示并禁止保存）。答对门槛操作符支持 `>=`、`>`、`=`、`<=`、`<` 五种，统一以“答对次数”为口径比较阈值（Worker `assessment_condition`）；旧“不等价次数”口径已于 2026-08 完成等价换算迁移（全局策略与在途快照），不得重新引入。整层判败（`fail_difficulty_assessment`）时必须把该层层汇总同步置 `status="failed"` 并带失败原因，前端据此显示红色“评测失败/已中止”，不得把已判败的层留在“作答中/待比对”展示。
- 分级评测空答案作答：完成但无结果（如输出超限被截断）的作答按答错计、正常进入比对，不得因此整层判败；比对 payload 只含有结果的作答（按 attempt 排序），且必须经 `equivalence_solve_query` 按层级过滤（分级比对只取本层作答，带入其他层作答会因标志数不一致判“比对未返回完整判定”整任务判败），定稿时空答案位补 False。整层作答全部无有效结果时比对项照常入队，Worker 执行段检测到 payload answers 为空即短路跳过上游调用（不写台账，遵守“一行台账 = 一次真实上游请求”），以 `{"equivalences": [], "skipped": "no_valid_answers"}` 完成，0 答对原样代入该层 K 判定：满足门槛晋级、不满足降一级定级，layer_summary 带 `allAnswersEmpty: true` 供前端区分展示。历史遗留的卡“待比对”任务用 Worker `scripts/repair_stuck_equivalence.py` 修复；全空被整层判败的历史任务用 `scripts/repair_all_empty_layer.py` 修复（含把存量快照的旧 `>=` operator 同步为当前全局策略）。复位工作项时必须同时清空 `completed_at`，否则完成事务的终态校验会把 completed_at 非空的 running 工作项当作迟到重复完成而丢弃新结果，永远卡 running。
- L0 双检测门控：后端创建评测时同时生成 `assessment_format`（规则）与 `assessment_synthesis`（synthesis 模型取项目级配置优先、未配置时兜底 `deepseek-v4-flash`）两个 L0 工作项；Worker 的 `finalize_assessment_l0` 在两者均到达终态后才创建首个作答层。所有 LaTeX 格式错误（包含 warning 级提示与确定性 error 级，均通过 `blocking_format_errors` 阻断）均将评测终定为 `format_failed`，不再展示“仅提醒，不阻断分级”；错误条目统一携带 `ruleId`/`severity`/`line`（行号由字符偏移换算，`difficulty_markdown_check` 另附 `field`），同类命中按 findall 全量报告并去重；AI 合成题检测失败（含重试耗尽）不阻塞分级，在 detail 的 L0 层记录 `synthesis.result="error"` 后继续晋级。前端不再有“全量质检”入口，也不再展示独立的 LaTeX/合成题质检卡片，题目详情页质检结果区只保留难度分级评测卡片（L0 层内含两项检测结果），按钮为“开始 L0 检测”；版本质检汇总（checkSummary）以分级评测结果为准，历史独立的 latex/synthesis 结果仍兼容纳入汇总判定。合格判定同口径：前端 `isQualified`、后端 `services.question_is_qualified`（列表 `qualified` 筛选）与学科分布 `qualifiedCount` 均把当前版本 `difficulty_assessment` 结果 pass 视为 LaTeX+合成题基础检测通过（历史独立双 pass 兼容），不得要求同一 run 同时存在独立 latex/synthesis 记录（新体系下永远匹配不到，会全部误判“未检测”）。
- LaTeX 格式校验为 Worker 纯规则引擎（`latex_check`，不调模型），以《LaTeX 数学公式编写与校验规范》六条强制红线为基准（环境不闭合、命令拼写错误、参数缺失、非法字符、嵌套违规必须拦截；推荐风格项不报错）：分隔符配对（忽略 `\$` 与 `\left[\right]`）、begin/end 栈式配对（拦截未闭合、孤立 `\end` 与同层环境交叉嵌套）、公式内变量与数字直接相邻（如 `x1` 应写 `x_1`；字母前有其他字母也检测，如 `dx2` 实为 `dx^2` 丢上标；数字在字母前的系数写法如 `3x2` 不误报）、公式内中文/全角字符（`\text{}` 豁免）、未知或拼写错误命令（amsmath+amssymb 白名单 `_COMMON_LATEX_COMMANDS` 为基准，error 级阻断，新增合法命令时须同步扩充）、`\frac` 系命令花括号感知参数校验（`\frac{3}` 缺分母拦截；`\sqrt[n]{x}` 可选根指数合法）、花括号不成对、`\left/\right` 不配对（用带词边界正则 `\\left\b`/`\\right\b` 计数，避免 `\rightarrow`/`\leftarrow` 中的 right/left 子串误计）、正文裸写 LaTeX 数学命令（如 `\boxed`/`\dfrac`/`\sqrt` 等未用 `$` 包裹，触发 `latex-unwrapped-math-command` 阻断错误）、相邻双花括号组 `{3}{4}` 疑似丢失 `\frac`（`latex-missing-frac`）、跨字段丢下标提示（`latex-subscript-letter`，本文存在规范写法 `j_i` 时裸写 `ji` 才提示）、公式内裸单词检测（`latex-bare-math-word`，如 `sigmaa` 实为 `\sigma` 丢反斜杠、`sina` 实为 `\sin a`，error 级阻断）：匹配字典为模块级常量 `_BARE_GREEK_WORDS` + `_BARE_MATH_FUNCTION_WORDS`（全等或前缀匹配，长度降序优先最长词），合法裸写词经 `_BARE_WORD_EXCEPTIONS` 豁免（当前仅 sinc），新增例外须同步扩充；正文未转义 `%`/`&`/`~`（正文为 Markdown，`#` 是合法标题语法不拦截；公式内半角 `~` 与全角波浪号 `～` 均合法，带圈数字 ①-⑳ 合法）；修改规则后须用存量题库回归，避免误伤规范写法。
- 分级版本隔离：`CheckRun.question_version` 与工作项 payload 的 `questionVersion` 固化题目版本；旧版本尚未完成的评测只能写入该历史版本的结果，不能覆盖当前题目的 `difficulty_level`、`difficulty_status` 或当前分级引用。
- 重新分级/重新检测增量复用：创建新任务或触发重试时只把失败、超时或未完成的作答工作项重新入队；历史已完成且 result 非空的作答与 L0 检测工作项直接继承（置 `completed`、拷贝结果与耗时、payload 标记 `inheritedFrom`），跳过 Redis 入队以节省上游配额；例外：`assessment_format` 失败（含错误条目）的历史结果不继承——L0 规则校验结果随规则版本变化且重跑零成本，必须用当前规则重新检测。复用源从最近一次历史 `CheckRun` 向前追溯（限同题同版本、最多回看 5 次），跨 run 累计、同键取最新 run 的结果；空答案作答（输出截断）同样继承，按“空答案=答错”口径计入判定。匹配键为（层级, 模型, 该模型在本层的第几次作答）：payload 有 `modelAttempt` 直接用，旧数据回退为同层同模型内按 attempt 升序的序号，策略里模型增减/顺序变化后仍能对齐；序号必须在包含失败/取消项的全量集合内计算，`fetch_reusable_solves` 返回已带序号的元组，调用方不得重新编号，否则与展开侧 attempt/modelAttempt 错位失配。若所有作答均被继承，比对任务（`equivalence`/`assessment_equivalence`）由后端或 Worker 直接唤醒，不得遗留永久阻塞。
- APIRoute 模型接入：新增 OpenAI-compatible 网关模型时，须同步更新前端、后端与 Worker 的 `audit_models` 目录；统一使用 `provider="apiroute"`，共享 `APIROUTE_API_KEYS` 和 APIRoute 并发/限流通道。未提供专属规则时，默认 Pass@K 3、难度答对阈值 ≤2，深度思考参数保持关闭。模型 ID 以网关实际注册名为准：kimi 于 2026-08-07 改为带厂商前缀的 `kimi/kimi-k3`（旧 ID `kimi-k3` 已无可用渠道），改名须同步三端目录、思考参数匹配、治理厂商/价目表与 DB `system_settings` 的 `difficulty_policy`/`model_governance`。
- 网关连接排障：`network_error: ConnectError` 表示连接层在获得 HTTP 响应前失败，属于可重试错误；Worker 会按指数退避重新进入公平队列。可用 `stem-system-worker/app/scripts/probe_apiroute_synthesis.py --smoke` 发送不含题目内容的流式探针，记录 DNS、响应头与首个流式片段耗时，且不会输出密钥。
- 网关 403 排障：请求约 100ms 内返回 `403 Forbidden` 时，必须读取响应 body 判定具体原因：`insufficient_user_quota`（如“用户额度不足，剩余额度: ¥-x”）表示 APIRoute 账户余额耗尽，所有模型调用（含不含题目内容的 `--smoke` 探针）都会被拒，需充值或更换 `APIROUTE_API_KEYS`，不是内容审核或代码问题。切勿仅凭“秒回 403”就归因为内容审核。
- 错误文案面向用户：Worker `provider_error` 通过 `friendly_http_message` 解析上游网关 JSON 错误体，把 `insufficient_user_quota`、鉴权失败等错误翻译成可操作的中文说明后才落库，前端（如“分级评测失败”提示）不应出现 httpx 原始信息（状态码 + 内部 URL）；新增上游错误码时须在该映射中同步补充。注意 httpx 流式响应不会自动读体，`raise_for_status()` 前必须对非 2xx 响应先 `aread()`，否则错误翻译读不到响应体。

### 高吞吐调度与成本治理

- 所有外部模型调用先同时获取 APIRoute 全局与厂商两层额度。默认全局为并发 12、RPM 36、TPM 480,000；厂商默认额度见管理员“模型管理 / 调用治理与成本”，仅允许在有上游配额依据时调整。
- 队列调度单位是单个工作项，优先级内按“项目 → 用户 → 工作项”轮转。同一用户每轮只派发一个工作项；存在其他等待者时，单项目最多占用 8 个外部槽位、单用户最多占用 3 个。无竞争时允许借用空闲槽位。
- 解题/分级作答、AI 合成题检测、答案或相似题比对的 TPM 输出预留分别为 32,768、8,192、2,048；只用于令牌预算，严禁作为 `max_tokens` 或 `max_completion_tokens` 传给上游。例外：解题/分级作答的输出上限由治理配置 `models[].solveMaxTokens` 管理（管理员“模型管理”页可手动设置或清空），默认值 deepseek-v4-flash/pro=393216（官方 384K 输出上限，1M 上下文）、qwen3.7-plus=131072（官方 128K），其余模型默认留空不传；目的是避免长思考耗尽输出预算导致答案被截断，仅对 APIRoute 网关模型生效。
- 每次真实上游请求（包括重试）会写入 `model_request_ledgers`，固化 Token、耗时、状态、错误、价格、汇率和成本快照。优先记录上游 usage；缺失时回退本地估算并标记“估算”，输出估算须把响应中的思考文本（`reasoning_content`）一并计入，否则思考模型成本会被严重低估。思考 Token 单列展示，不重复计入输出计费。
- 分段计价：治理配置 `models[*].priceTiers` 按 `maxInputTokens` 升序定义多档单价（默认 doubao-seed-2-0-pro-260215 三档：≤32K 3.2/16、≤128K 4.8/24、≤256K 9.6/48 元/M；gemini-3.1-pro-preview 两档 USD：≤200K 2/12、≤1M 4/18；qwen3.7-plus 两档：≤256K 1.6/6.4（缓存命中 0.32）、≤1M 4.8/19.2 元/M），扁平 `inputPricePerMillion/outputPricePerMillion` 仅作无分档兜底。结算时按真实输入 token 选档（首个 `maxInputTokens ≥ 输入` 的档，超出末档按末档），并把命中档单价回写台账 `input/output_price_per_million` 两列；上游 usage 含 `prompt_tokens_details.cached_tokens` 时命中部分按档内 `cacheHitPricePerMillion` 计价（缺省回退档内输入价），缓存存储价按时长计费、无法归属单次请求，不纳入请求成本。
- 项目级模型设置：`project_model_settings` 表按项目存检查项模型（`audit_models`）与分层策略（`difficulty_policy`）的规范化快照；行不存在即跟随全局（`system_settings` 的 `audit_model`/`difficulty_policy`），保存为整份 copy-on-write，之后全局修改不再影响该项目，DELETE 删行恢复跟随全局。所有任务创建入口（单题/批量质检、难度分级、队列重试重检、上传与改版本自动相似题、管理员修复相似题）按题目 `project_id` 经 `project_audit_models`/`project_difficulty_policy` 解析，生效配置仍在任务创建瞬间固化进 `check_runs.model_versions` 快照，Worker 只读快照派发与结算、零改动。调用治理（并发/RPM/TPM、单价、分档、solveMaxTokens、配额）保持全局唯一，不按项目拆分（价格是厂商成本，按项目改会破坏成本口径）。权限复用 `require_project_manager`：admin 可配任意项目，project_admin 仅本项目，越权 403/404；前端入口为模型管理页“配置范围”切换（项目管理员锁定在自己的项目且不显示全局卡片）。L0 合成题检测模型取项目配置的 synthesis，项目未配置时兜底 `SYNTHESIS_AUDIT_MODEL_ID` 常量。
- 仅 `408/429/500/502/503/504`、连接/超时/协议中断等临时网络错误以及 PostgreSQL 死锁/序列化失败（SQLSTATE `40P01`/`40001`，Worker `provider_error` 归为可重试的 `db_transient_error`）可重试；最多 5 次重试，退避 `2/4/8/16/32` 秒并加 `0~1` 秒抖动。`400/401/403/404/405/409/410/413/415/422`、无效模型/参数/响应结构和缺失密钥直接失败；退避期间不占任何执行槽位。数据库瞬态错误不得落入不可重试的 `execution_error`：一次偶发死锁若直接判作答失败，会在比对定稿时触发“该层存在未成功完成的模型作答”整层判败（题目 39 首次评测即因此失败）。
- 账号配额强制生效：Worker 在派发每个模型调用前执行 `user_quota_violation` 门控，统计任务数时必须排除当前工作项所属任务（`exclude_run_id`：该任务创建时已通过预检，若把自己计入，上限为 1 时当天唯一任务会被自己的名额永久卡死）；日/月上限按用户发起的质检任务数统计（1 个 `CheckRun` = 1 次质检，无论内部多少次模型调用），排除上传自动触发的相似题检测（`check_types == ["similarity"]`），已取消的任务同样计入（防反复取消重提绕过）；月预算仍按 `model_request_ledgers` 归属用户的当月累计成本。生效值 = 账号专属值优先，未设置取治理配置 `userQuotaDefaults`（在管理员“模型管理 / 调用治理”的“全局账号配额默认值”区块维护），0 表示不限制。超额时工作项保持 `queued` 并标记 `quota_exceeded`，每 60 秒重试一次，不记失败、不转人工，额度恢复（次日/下月/调高配置）后自动继续。规则类阶段（如 L0 LaTeX）不经门控。发起入口另有预检（后端 `require_quota_available`）：单题质检、难度分级、批量质检按同口径校验，超限直接 409，避免任务创建成功后工作项被静默挂起；批量质检在合格剔除后按 `已用任务数 + 本次题目数 > 上限` 整批拒绝并提示剩余额度（每道题各创建 1 个任务），上限不再统计模型请求次数。

### 模型思考参数

- 唯一实现来源：`stem-system-worker/app/app/services.py`；模型目录以三端各自的 `audit_models.py` 为准。
- L0 包含本地 Markdown/LaTeX 校验（不调用模型）与 AI 合成题检测（项目配置的 synthesis 模型优先，未配置时兜底 `deepseek-v4-flash`；兜底常量由三端 `audit_models.py` 的 `SYNTHESIS_AUDIT_MODEL_ID` 与后端 `DEFAULT_GLOBAL_AUDIT_MODEL_IDS["synthesis"]` 共同决定，修改时须三处同步）；两者均为 `difficulty_assessment` 的 L0 工作项。合成题检测 Prompt 兼任“是否构成正常题目”判定：孤立数字/片段等非题目内容输出 `is_valid_question=false`，L0 门控据此将评测定为 `invalid_question` 阻断定级（前端展示“非正常题目”）；旧检测结果无该字段按有效兼容，检测调用失败不阻塞分级。
- 所有模型均通过 OpenAI-compatible APIRoute 网关请求；逻辑 Provider 仅用于区分参数和限流策略。
- 解题 `max_tokens` 不再硬编码：Worker 派发每个工作项时实时读取治理配置 `solveMaxTokens`，留空则不传；管理员在“模型管理”页修改保存后对新派发的工作项即时生效，无需重启或改代码。

| 模型 | 解题 / 难度分级作答 | 答案比对 | AI 合成题检测 | 流式策略 |
| --- | --- | --- | --- | --- |
| doubao-2.0-pro、doubao-2.1-pro | `thinking.type=enabled`；`reasoning.effort=high` | `thinking.type=disabled` | `thinking.type=enabled`；`reasoning.effort=medium` | 三种场景均流式 |
| gemini-3.1-pro | `thinking.type=enabled`；`reasoning.effort=high` | `thinking.type=disabled` | `thinking.type=enabled`；`reasoning.effort=medium` | 当前均非流式 |
| kimi/kimi-k3 | `reasoning_effort=max` | `reasoning_effort=low` | `reasoning_effort=high` | 非流式（上游 APIRoute 网关流式接口返回空 SSE 字节流且 0 Token，须保持非流式保证正常输出） |
| qwen3.7-plus | `enable_thinking=true`；`reasoning_effort=xhigh`；`temperature=0.7`；`max_tokens` 取自治理配置 `solveMaxTokens`（默认 131072，官方 128K 输出上限） | `enable_thinking=false`；`temperature=0.1` | `enable_thinking=true`；`reasoning_effort=medium` | 仅解题流式 |
| qwen3.7-max、qwen3.8-max | `enable_thinking=true`；`reasoning_effort=xhigh`；`temperature=0.7` | `enable_thinking=false`；`temperature=0.1` | `enable_thinking=true`；`reasoning_effort=medium` | 仅解题流式 |
| deepseek-v4-flash、deepseek-v4-pro | `thinking.type=enabled`；`reasoning_effort=max`；`temperature=0.7`；`max_tokens` 取自治理配置 `solveMaxTokens`（默认 393216，官方 384K 输出上限） | `thinking.type=disabled`；`temperature=0.1` | `thinking.type=enabled`；`reasoning_effort=high` | 仅解题流式 |
| claude-sonnet-5 | `thinking.type=adaptive`；`output_config.effort=high` | `thinking.type=disabled` | `thinking.type=adaptive`；`output_config.effort=high` | 仅解题流式 |

- `glm-5.2` 已移除，不得重新加入默认策略或模型目录，除非完成可用性验证并明确授权。
- Claude 系列思考参数按官方 adaptive thinking 格式：必须显式传 `thinking.type="adaptive"` 否则模型不思考，`effort` 放独立顶层 `output_config`（放进 `thinking` 会 400）；Sonnet 仅支持 low/medium/high，`max`/`xhigh` 为 Opus 专属（Sonnet 传 `max` 官方会报错）。2026-08-07 实测 APIRoute 的 claude-sonnet-5 通道：含官方格式在内六组思考参数均无 `reasoning_content` 返回且耗时仅 3~7 秒，判定网关未透传思考参数（探针 `scripts/probe_claude_thinking.py`，支持 `--extra NAME=JSON` 复测）；网关升级后须复测，若思考生效需同步成本估算的思考 Token 口径。
