# STEM 题目审核系统前端

面向教研、内容运营和审核人员的题目管理界面。它将题目导入、异步审核、人工复核和结果追溯放在同一工作流中，帮助团队在不牺牲可解释性的前提下提升题库审核效率。

## 业务价值

- **批量沉淀题库**：支持导入题干、答案、难度、知识点等结构化数据，降低人工录入成本。
- **让审核可追溯**：按题目查看版本、审核状态、模型答案和校验结论，便于定位问题与复核。
- **缩短审核等待**：前端创建审核任务后订阅进度事件，不需要让浏览器持续等待模型调用完成。
- **保障协作边界**：基于 HttpOnly 会话登录；普通用户只看到自己的题目，管理员可管理账号与全量题库。

## 核心能力

- 题目列表、搜索、筛选、详情与版本记录。
- Excel 导入预览与字段校验。
- 审核任务状态展示与实时进度更新。
- 难度、答案、LaTeX 和疑似 AI 生成痕迹等审核结果展示。
- KaTeX 数学公式渲染；模型返回的最终答案会以简洁公式形式展示。

## 技术要点

- **Next.js 16 + React 19 + TypeScript**：使用 App Router 构建页面和路由。
- **Ant Design 6**：提供一致的后台管理交互与表格、表单能力。
- **SWR**：处理题目、审核任务等服务端数据缓存和重取。
- **SSE 容错订阅**：通过 `src/lib/check-runs.ts` 订阅审核事件，同时保留轮询/重取路径。
- **同源 API 代理**：浏览器始终请求相对路径 `/api/*`；`next.config.ts` 将请求重写到后端，保留 Cookie 与同源策略。

## 本地运行

前置条件：Node.js 20+，以及已启动的后端 API。

```bash
npm ci
cp .env.example .env.local
npm run dev
```

默认访问地址为 `http://localhost:3000`。若端口已被占用，Next.js 会选择其他可用端口。

### 环境变量

| 变量 | 说明 | 示例 |
| --- | --- | --- |
| `BACKEND_API_URL` | FastAPI 服务地址，由 Next.js 转发 `/api/*` 请求 | `http://localhost:8000` |

不要在前端环境变量中放入模型 API Key、数据库密码或会话密钥。

## 常用命令

```bash
npm run dev    # 开发服务器
npm run lint   # ESLint 检查
npm run build  # 生产构建验证
npm run start  # 启动已构建的应用
```

## 相关服务

- API 与数据管理：[stem-system-backend](https://gitlab.bodenai.com/agi-project/stem-system-backend)
- 异步审核与模型调度：[stem-system-worker](https://gitlab.bodenai.com/agi-project/stem-system-worker)

## 开发约定

- 浏览器请求只使用 `/api/...` 相对地址，不直接访问后端绝对 URL。
- 涉及 API 字段变化时，同步维护 `src/types/index.ts`、调用方与后端契约。
- UI 采用既有 Ant Design 风格；数学内容使用 `LatexRenderer`，不要直接注入不可信 HTML。
