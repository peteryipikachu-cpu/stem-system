# STEM 题目审核系统 Worker

独立的异步审核执行器。Worker 从 Redis 队列领取后端创建的工作项，调用模型完成审核，并将结果、进度与用量数据写回 PostgreSQL。将耗时的模型调用从 API 中拆出，确保题库操作与审核吞吐可以独立扩缩容。

## 业务价值

- **不阻塞业务操作**：用户提交审核后即可继续管理题目，不必等待长时间模型推理。
- **提高审核吞吐**：多个工作项并发执行，适合批量题目与多次独立求解的审核场景。
- **提升结论可信度**：难度审核会多次独立求解并做答案等价比较，而不是依赖一次模型回复。
- **控制成本与风险**：按模型和 Key 限流、重试、熔断、租约恢复，避免单个上游故障拖垮审核队列。

## 审核流程

| 审核类型 | 执行方式 | 结果 |
| --- | --- | --- |
| `latex` | 规则校验题干与答案中的 LaTeX | 格式通过/失败及错误信息 |
| `difficulty` | 所选模型独立解题 K 次，再由同一模型做语义等价判断 | 答对次数与难度结论 |
| `answer` | 所选模型独立解题 K 次，再由同一模型做语义等价判断 | 答案可信度结论 |
| `synthesis` | 所选模型单次分析题目中的疑似 AI 生成痕迹 | 置信度与可见证据 |

难度和答案审核的独立解题请求会按模型启用深度思考，并限制为只返回最终答案；答案等价比较和 AI 合成题检测关闭或降低思考强度，减少无关推理开销。LaTeX 检测始终是本地规则，不调用模型。

| 模型 | Pass@K | 难度通过条件 |
| --- | ---: | --- |
| `doubao-2.0-pro` / `gemini-3.1-pro` | 8 | 答对 ≤6 次 |
| `doubao-2.1-pro` | 4 | 答对 ≤2 次 |
| `glm-5.2` / `qwen3.7-max` / `kimi-k3` | 3 | 答对 ≤2 次 |

答案校验中至少一次与参考答案语义等价，即判定答案可信。模型选择按任务快照保存，执行中不会被环境变量中的默认模型覆盖。

## 多 Key 并发设计

`APIROUTE_API_KEYS` 支持以逗号分隔的共享 Key 池。所有模型都通过 `APIROUTE_BASE_URL` 访问同一个 OpenAI 兼容网关；每个 Key 都有独立的 Redis 并发、RPM 和 TPM 桶：

- 供应商级并发仍由 `AI_LIMIT_DOUBAO_*`、`AI_LIMIT_GEMINI_*` 与 `AI_LIMIT_APIROUTE_*` 控制；它们限制不同模型的执行槽位，不再代表不同的密钥来源。
- 更多 Key 可以提高多题并发吞吐；单题的提升会受 Pass@K、供应商并发和最后的等价比较阶段限制。
- 如果多个 Key 共享上游账号级配额，实际加速仍会受到上游总限流约束。

不要在文档、日志或提交中写入真实 Key。

## 技术要点

- **Python async + SQLAlchemy Async**：异步读取工作项与持久化结果。
- **Redis 队列与租约**：领取、过期恢复、依赖激活和优先级调度。
- **容量控制**：按供应商、模型阶段和单个 Key 分配并发/RPM/TPM 限额。
- **可靠性**：可重试错误、熔断窗口、取消归队、批处理截止时间与人工复核转移。
- **可观测性**：进度事件、工作项耗时和模型 usage 会持久化，供 API 与前端展示。

## 本地运行

前置条件：Python 3.9+、与后端相同的 PostgreSQL 和 Redis，且后端已完成数据库迁移。

```bash
env DATABASE_URL='postgresql+asyncpg://pikachu@localhost:5432/stem' REDIS_URL='redis://localhost:6379/0' /opt/anaconda3/bin/python -m app.worker
```

Worker 不启动 HTTP 服务；它会持续消费队列。停止时会取消正在执行的任务并归还领取的工作项，避免遗留永久的 `running` 状态。

### 最小配置

```dotenv
DATABASE_URL=postgresql+asyncpg://<user>:<password>@localhost:5432/<database>
REDIS_URL=redis://localhost:6379/0
APIROUTE_API_KEYS=<key-1>,<key-2>,<key-3>,<key-4>
APIROUTE_BASE_URL=https://apiroute.bodenai.net/v1
```

请根据 Key 池规模调整 `WORKER_CONCURRENCY`，使其能够覆盖各 Key 的并发上限总和；完整可调参数见 [`.env.example`](.env.example)。

## 质量检查

```bash
pytest
ruff check .
```

## 相关服务

- API 与数据服务：[stem-system-backend](https://gitlab.bodenai.com/agi-project/stem-system-backend)
- Web 界面：[stem-system-frontend](https://gitlab.bodenai.com/agi-project/stem-system-frontend)

## 开发约定

- 不要把模型调用迁回 API；API 仅创建任务和提供状态/事件查询。
- 变更事件格式时同步检查后端 SSE API 与前端订阅逻辑。
- 新增模型或审核类型时，必须同时设计限流、重试、结果结构、依赖激活和失败语义。
