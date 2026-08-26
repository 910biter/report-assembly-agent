# Report Assembly Agent

面向多来源材料的事实可追溯报告整编系统。运行架构、交互流程和模板学习说明位于 [`docs/`](docs/)。

## 更换模型或硬件前的联动检查

上下文不是单一参数。以下配置共同构成推理资源契约，调整显卡、模型量化、KV Cache、并发数或 vLLM `--max-model-len` 时必须作为一组重新标定。

| 约束 | 配置 | 主要使用位置 | 联动关系 |
|---|---|---|---|
| 物理上下文 | `IRA_MODEL_CONTEXT_WINDOW_TOKENS` | `app/context.py`、Evidence、Writer、Graph | 必须等于服务端 `--max-model-len` |
| 通用输出预留 | `IRA_GENERATION_RESERVE_TOKENS` | Gateway 默认生成 | 不得超过物理上下文剩余容量 |
| 结构化输出 | `IRA_STRUCTURED_OUTPUT_TOKENS`、`IRA_COMPARISON_OUTPUT_TOKENS`、`IRA_STYLE_*_OUTPUT_TOKENS` | Planner、Analysis、QA、增量对比、模板学习 | 影响 JSON 完整性与输入可用空间 |
| 最终规划输出 | `IRA_FINAL_PLANNER_OUTPUT_TOKENS` | Final Planner | 章节/小节越多，所需输出越大 |
| Evidence 输出 | `IRA_EVIDENCE_OUTPUT_TOKENS` | Evidence 装箱 | `输入预算 = 窗口 - 输出 - Prompt 开销 - 安全余量` |
| Writer 输出 | `IRA_WRITER_OUTPUT_TOKENS` | 小节写作 | 同时限制单次可见正文规模与证据输入容量 |
| Graph 输出 | `IRA_GRAPH_OUTPUT_TOKENS` | 关系抽取 | 与 `IRA_GRAPH_FACTS_PER_BATCH` 联动，截断时自动拆批 |
| 固定开销与余量 | `IRA_PROMPT_OVERHEAD_TOKENS`、`IRA_SAFETY_MARGIN_TOKENS` | 所有动态装箱 | 模型/Chat Template 变化后必须复测 |
| Writer 文本上限 | `IRA_MAX_CONTEXT_CHARS` | Writer Prompt 装箱 | 是保守字符边界，不应大于派生 Token 容量 |
| 交互上下文 | `IRA_INTERACTIVE_*_TOKENS` | 协作审阅 | 输入、历史、输出之和必须低于物理窗口 |
| 向量编码长度 | `IRA_EMBEDDING_MAX_LENGTH` | Embedding | 与生成模型窗口无关，受 Embedding 模型上限约束 |
| 并发与批处理 | `IRA_LLM_CONCURRENCY`、`IRA_EVIDENCE_BATCH_CONCURRENCY`、`IRA_GRAPH_BATCH_CONCURRENCY` | vLLM 调度 | 并发提高会增加 KV Cache/显存压力，可能迫使上下文缩短 |

换硬件时按以下顺序操作：先确定模型精度和单序列最大上下文，再确定最大并发；随后校准各 Stage 输出预留和批大小；最后用 P50/P95 真实 workload 验证截断率、TTFT、Decode、显存峰值和报告质量。不要只修改 `MODEL_CONTEXT_WINDOW_TOKENS`。

详细公式、当前 24K 基线和验收标准见 [`docs/MODEL_RUNTIME_ARCHITECTURE.md`](docs/MODEL_RUNTIME_ARCHITECTURE.md)。

### 代码中的上下文敏感点

下表是硬件迁移时必须联合复核的运行位置。这里的数量边界只负责资源安全和上下文压缩，不应承担领域判断；提高窗口后也不应直接全部放大，而应以召回覆盖、JSON 完整率和成文质量复测结果为准。

| Stage | 代码位置 | 当前容量机制 | 换硬件后的检查项 |
|---|---|---|---|
| Material Understanding / Evidence | `app/context.py` | 按材料覆盖装箱，输入预算由 Evidence 输出预留派生；相关 Unit 采用混合检索和缺口补检 | Unit 覆盖率、批次数、截断率、Fact 产出稳定性 |
| Analysis / Final Planner | `app/context.py`、`app/planning/planner.py` | Facts、Inferences 与规划产物按最终规划输出空间装箱 | 后部事实是否被遗漏、目录完整性、结构化输出截断率 |
| Narrative Plan | `app/planning/narrative.py` | 单小节可写规模受 Writer 单次输出容量约束 | 小节数量、目标字数分配、Narrative Plan 完整率 |
| Writer | `app/writing/writer.py`、`app/planning/structure.py` | 当前小节证据 + 压缩 Report Memory；正文输入预算由 Writer 输出预留派生 | 目标字数完成率、Fact/Inference 利用率、跨章重复率 |
| QA | `app/qa/qa.py`、`app/quality.py` | 确定性全量检查与有限语义抽样结合 | 长报告后半部覆盖、问题召回率/误报率、QA 耗时 |
| Graph | `app/graph/service.py` | 全量 Fact 分批；截断后递归拆分；成功子批独立落库 | 初始批大小、拆分次数、终端失败 Fact、关系召回率 |
| Style Learning | `app/memory/style.py`、`app/memory/style_profile.py` | 从多位置抽取代表样例，不把整份历史报告直接送入模型 | 样例覆盖、软风格稳定性、画像 JSON 完整率 |
| Interaction | `app/interaction.py` | 当前对象、依据、确认变更和近期会话分别分配预算 | 多轮连续性、直接证据保留率、交互 TTFT |
| Incremental Comparison | `app/material_comparison.py` | 新旧事实候选分批比较，独立输出预留 | 变化召回率、错误关联率、单批截断率 |

代码中用于 UI 摘要、数据库字段长度或审计预览的字符串切片不属于模型上下文边界，不应随硬件放大。真正的资源边界应优先从 `app/config.py` 和上述 Stage 容量公式派生；若新增模型调用，必须同时登记到本表。
