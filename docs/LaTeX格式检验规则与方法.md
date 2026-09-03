# LaTeX 格式检验规则与方法

> 适用范围：STEM 题目审核系统当前实现。本文以代码为准，重点说明题目、参考答案、解题思路及修改说明中的 LaTeX 检验；规则实现位于 Worker 的 `app/services.py` 中的 `latex_check` 与 `difficulty_markdown_check`。

## 1. 检验目的与总体原则

系统的格式检验不是通用 TeX 编译器，而是一套面向题库数据质量的本地规则校验。它的目标是：

- 让题目、答案和解题思路能被前端 KaTeX 稳定展示；
- 及时发现常见的公式录入、OCR 和模型输出错误；
- 保障后续模型作答、答案等价判定与导出的一致性；
- 在调用 L1–L3 模型前尽早拦截不合格的内容，避免无效模型消耗。

公式检验遵循“宁可给出明确修正提示，也不静默吞错”的原则。当前版本中，**所有格式问题（包括标记为 `warning` 的启发式提示）都会阻断 L0 晋级**；`warning` 仅表示该问题存在一定语义歧义，不表示可以继续定级。

## 2. 校验入口与执行链路

### 2.1 上传与保存新版本时：同步规范拦截

后端 `upload_rule_violations` 在上传题目、保存题目新版本时执行可确定的规则。与 LaTeX 直接相关的规则包括：

- 参考答案按剥离 LaTeX 定界符、命令和排版符号后的可见文本计算长度，最长 40 个字符；
- 答案中的公式必须完整使用 `$...$`、`\(...\)` 或 `\[...\]` 包裹；定界符外残留 LaTeX 命令、`^` 或 `_` 会被判为“裸公式”；
- 参考答案只能放最终结果，不可混入多行推导或明显的解题过程；
- 近似值（如 `≈`、`\approx`、`约等于`）不允许作为最终答案；
- 答案不得为 `0`、`1`、`-1`。

这一步是导入时的快速门槛，并不取代完整格式校验。

### 2.2 难度分级的 L0：权威格式结论

每次难度分级都会先创建 L0 的本地规则工作项 `assessment_format`。Worker 对下列字段逐一调用 `latex_check`：

1. 题目；
2. 参考答案；
3. 解题思路；
4. 修改说明。

所有字段会合并为上下文，以便识别“本文其他位置已有规范下标写法”的情况。L0 同时等待 AI 合成题检测完成；格式检查没有错误时，才会进入 L1 作答。任一格式问题都会把当前分级任务置为 `format_failed`，不再调用 L1–L3 模型。

旧的独立 `latex` 检测任务仍兼容保留；它只校验“题目 + 参考答案”。当前页面和统计应优先采用最新难度分级任务 L0 的格式结论。

### 2.3 质检员更正答案时：浏览器预检 + L0 复检

质检任务详情页在提交“参考答案错误”的纠正答案、纠正解题思路前，会在浏览器进行快速预检，检查定界符、环境、花括号、公式外裸命令和公式内中文等明显问题。

该预检用于及时反馈，**不是最终权威结论**。管理员确认更正后会创建题目新版本；新版本仍须经过 Worker 的 L0 完整格式检验和后续流程。

### 2.4 前端展示：KaTeX 渲染容错

前端 `LatexRenderer` 使用 KaTeX 渲染 `$...$`、`$$...$$`、`\(...\)`、`\[...\]`。渲染失败时会以红色原文呈现，而不会执行 HTML 注入。渲染容错只影响展示；是否可以进入难度定级，仍以 L0 规则结果为准。

## 3. 数学公式的识别范围

完整校验器会识别四种数学定界形式：

- 行内：`$...$`；
- 块级：`$$...$$`；
- 行内：`\(...\)`；
- 块级：`\[...\]`。

转义后的 `\$` 视为普通货币符号。`\left[ ... \right]`、`\left( ... \right)` 等大小定界符不会被误认为 `\[...\]` 或 `\(...\)` 的公式边界。

## 4. 规则清单

| 规则 ID | 级别 | 检查内容 | 常见修正方式 |
| --- | --- | --- | --- |
| `latex-delimiter-dollar` | error | 未转义 `$` 数量不成对 | 补齐 `$...$` 或改为 `\(...\)` |
| `latex-delimiter-bracket` | error | `\[` 与 `\]`、`\(` 与 `\)` 数量不匹配 | 补齐对应的公式定界符 |
| `latex-env-mismatch` | error | `\begin{...}` / `\end{...}` 缺失、名称不一致或交叉嵌套 | 保持环境名与嵌套顺序一致 |
| `latex-brace-balance` | error | 公式内 `{`、`}` 不成对 | 补齐或删除多余花括号 |
| `latex-left-right` | error | `\left` 与 `\right` 数量不匹配 | 补齐配对定界符 |
| `latex-unknown-command` | error | 不在命令白名单内的控制序列，如 `\fraq` | 更正命令拼写或采用受支持命令 |
| `latex-command-arg` | error | `\frac`、`\sqrt`、`\vec` 等命令缺少必要参数，或可选 `[]` 参数未闭合 | 使用完整参数形式，例如 `\frac{a}{b}` |
| `latex-missing-frac` | error | 相邻 `{...}{...}` 高度疑似遗漏 `\frac` | 确认并改为 `\frac{...}{...}` 等完整命令 |
| `latex-cjk-in-math` | error | 公式中直接出现中文或全角字符 | 移到公式外，或写作 `\text{中文}` |
| `latex-bare-math-word` | error | 公式中出现裸写希腊字母名、数学函数名，如 `sigma`、`sina`、`limx` | 使用 `\sigma`、`\sin a`、`\lim x` 等规范写法 |
| `latex-escape-char` | error | 公式外未转义的 `%`、`&`、`~` | 转义为 `\%`、`\&`，或把内容移进公式；`~` 可改为 `\textasciitilde` |
| `latex-unwrapped-math-command` | error | 散文正文中出现未被公式定界符包裹的数学命令，如 `\boxed{9}` | 改为 `$\boxed{9}$`；整行纯公式是例外 |
| `latex-subscript-digit` | warning | 如 `x1`、`dx2`，疑似遗漏下标或上标 | 按语义改为 `x_{1}`、`dx^{2}`，或明确乘积关系 |
| `latex-subscript-letter` | warning | 本文其他位置已有 `j_i`，本处却出现 `ji` | 改为 `j_{i}`；若确为乘积可人工确认 |

### 4.1 参数检查的兼容写法

为兼容真实题库与公式编辑器输出，下列单记号参数是允许的：

- `\frac 12`、`\frac13`；
- `\sqrt x`、`\sqrt[3]{x}`；
- `\mathbb F_2^4`、`\hat x`、`\vec v`。

但是 `\frac{3}`、`\frac 1` 等缺少分母的写法仍会报错。

### 4.2 中文与文本模式

公式中的中文、全角括号等会被拦截；文本内容应使用 `\text{...}`。下列文本/字体命令会被正确识别为文本模式：

`\text`、`\textrm`、`\textup`、`\mathrm`、`\mathbf`、`\mathit`、`\mathsf`、`\mathtt`、`\mathcal`、`\mathbb`、`\operatorname`。

公式内的全角波浪号 `～` 以及带圈数字 `①`–`⑳` 是特例，当前允许；半角 `~` 在公式外仍需转义或改写。

## 5. 命令白名单

未知命令检查以 Worker 中 `_COMMON_LATEX_COMMANDS` 为权威集合。白名单按功能覆盖以下类别：

- 希腊字母和变体、常量、微分与积分；
- 分式、根式、组合数、上下标、装饰符和箭头；
- 比较、集合、逻辑、关系、量词、几何和矩阵常用符号；
- `amsmath` / `amssymb` 高频命令，包括 `\coloneqq`、`\eqqcolon`、`\substack`、`\overset`、`\underset` 等；
- 文本、字体、颜色、间距和盒子命令；
- 环境及表格辅助命令；
- 物理、化学、单位常用命令；
- 常见 TeX 内核与旧公式编辑器输出。

其中以下命令已明确纳入白名单：

- `\textcircled`：基础文本带圈命令；
- `\textup{...}`：数学环境中的直立文本；
- `\mathop`、`\mathbin`、`\mathrel` 等 TeX 原子类别命令；
- `\degree`、`\celsius`、`\micro`、`\ohm`、`\angstrom` 等单位符号。

白名单保证“不会被未知命令规则误判”，但不等于所有命令都能由前端 KaTeX 在任何上下文完整渲染。需要新增命令时，应同时评估 Worker 白名单、KaTeX 支持情况和测试覆盖。

## 6. 与 Markdown 的联合检查

L0 格式任务还检查与公式混排密切相关的 Markdown 结构：

- 未闭合的代码块围栏（`` ``` `` 或 `~~~`）；
- 内嵌 HTML；
- 未闭合的 Markdown 链接或图片；
- 多行 Markdown 表格的列数不一致。

检查 Markdown 时，数学片段会被等长遮蔽，避免公式中的 `<`、`>`、`|` 被误判为 HTML 或表格分隔符。Markdown 标题中的 `#` 是允许的。

## 7. 结果结构与状态口径

单字段 `latex_check` 返回：

```json
{
  "errors": [
    {
      "ruleId": "latex-unknown-command",
      "severity": "error",
      "location": "第 2 行 公式 `\\fraq{1}{2}`",
      "description": "未知或拼写错误的 LaTeX 命令 \\fraq",
      "suggestion": "核对命令拼写，命令以 amsmath/amssymb 宏包为基准",
      "line": 2
    }
  ]
}
```

L0 汇总时，每条错误会额外带有 `field`（题目、参考答案、解题思路或修改说明）。

- `errors` 为空：L0 格式通过；
- `errors` 非空：L0 状态为 `format_failed`，题目不进入 L1–L3；
- 格式检查工作项本身异常：任务按“格式校验未完成”处理，不会放行；
- 历史失败的 L0 格式结果不会被复用，重检会按最新规则重新计算。

## 8. 维护约定

1. 修改 `latex_check` 后，必须同步更新 Worker 的 `tests/test_latex.py`；
2. 新增白名单命令时，应新增“允许”用例，避免后续回归误拦截；
3. 新增强制规则时，应区分真实语法错误与可能存在语义歧义的提示；但当前 L0 会阻断两者，新增 `warning` 前也应谨慎评估误报风险；
4. 如改动公式定界、裸公式或结果状态口径，还应检查上传规则、L0 流程、前端 `LatexRenderer` 和质检员更正页的预检是否需要同步。

## 9. 代码索引

| 位置 | 职责 |
| --- | --- |
| `stem-system-worker/app/app/services.py`：`latex_check` | 权威 LaTeX 规则与结构化错误输出 |
| `stem-system-worker/app/app/services.py`：`difficulty_markdown_check` | 四个字段的 L0 汇总及 Markdown 联合检查 |
| `stem-system-worker/app/app/services.py`：`finalize_assessment_l0` | 格式失败时终止分级，格式通过后进入 L1 |
| `stem-system-worker/app/tests/test_latex.py` | LaTeX 规则回归测试 |
| `stem-system-backend/app/app/services.py`：`upload_rule_violations` | 上传/改版时的同步规范拦截 |
| `stem-system-frontend/app/src/components/LatexRenderer.tsx` | KaTeX 渲染与展示容错 |
| `stem-system-frontend/app/src/app/quality-review-tasks/[id]/page.tsx` | 质检员更正答案的浏览器端预检 |

