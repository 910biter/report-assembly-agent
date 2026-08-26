# 模型运行与上下文约定

## 运行基线

系统使用一个常驻 vLLM 模型实例，不为交互助手复制第二份权重。当前生产基线为 24,576 token 物理上下文、2 个服务端序列槽位和 FP16 KV Cache。模型常驻后，Web 服务重启不触发权重重新加载。

## 两条逻辑通道

| 通道 | 任务 | 本地并发 | 输入预算 | 输出预算 | 调度优先级 |
|---|---|---:|---:|---:|---:|
| 交互控制 | 需求讨论、产物解释、修改提案 | 1 | 6,144 token | 1,024 token | 10 |
| 工作流执行 | Evidence、Analysis、Planner、Writer、QA | 由 `IRA_LLM_CONCURRENCY` 控制 | 按 Stage 动态装箱，物理上限 24,576 token | 按 Stage 预留 | 50/90 |

交互请求不进入长工作流的本地队列，而是通过独立并发闸门直接提交给 vLLM。vLLM 在同一模型实例内根据 priority 调度；这改善交互等待时间，但不会虚增模型的物理吞吐。

## 上下文契约

工作流共用一个物理窗口，但不共用一套输入/输出比例。`app/runtime_profiles.py` 为每个 Stage 声明 workload、输出预留、装箱方式和延迟等级，并统一按“输入证据 + 固定提示 + 阶段输出预留 + 安全余量”计算。Writer 只接收当前章节或小节所需的覆盖证据、章节关系、已使用事实标识和压缩后的 Report Memory。

交互上下文只包含当前任务边界、当前产物、直接绑定的 Fact/Inference/Evidence、少量相关内容、最近完整对话和已确认变更。Fact、Inference 和 Evidence 必须通过当前任务的 ID 白名单读取。交互消息不得成为正式事实来源；只有用户接受 ChangeProposal 后，系统才按影响范围生成可审阅候选版本。

## 缓存与并发边界

vLLM prefix cache 复用稳定 system prompt 和重复前缀；业务 Artifact Cache 继续负责工作流断点复用。交互记录保存在 PostgreSQL，不缓存为跨任务知识。单卡只允许一个交互生成并发，工作流并发保持有界，避免大量短对话挤占正式报告生成。

推荐环境变量：

```dotenv
IRA_MODEL_CONTEXT_WINDOW_TOKENS=24576
IRA_LLM_CONCURRENCY=2
IRA_INTERACTIVE_CONCURRENCY=1
IRA_INTERACTIVE_INPUT_TOKENS=6144
IRA_INTERACTIVE_HISTORY_TOKENS=1536
IRA_INTERACTIVE_OUTPUT_TOKENS=1024
IRA_GENERATION_TOKENIZER_PATH=/path/to/the/same/model/tokenizer
IRA_MATERIAL_ANALYSIS_INPUT_TOKENS=4000
IRA_STRUCTURED_OUTPUT_TOKENS=3072
IRA_MATERIAL_ANALYSIS_OUTPUT_TOKENS=2048
IRA_PLANNER_OUTPUT_TOKENS=3072
IRA_FINAL_PLANNER_OUTPUT_TOKENS=4096
IRA_WRITER_OUTPUT_TOKENS=3072
IRA_EVIDENCE_OUTPUT_TOKENS=4096
IRA_CONFLICT_OUTPUT_TOKENS=2048
IRA_ANALYSIS_OUTPUT_TOKENS=3072
IRA_NARRATIVE_OUTPUT_TOKENS=3072
IRA_NARRATIVE_QA_OUTPUT_TOKENS=2048
IRA_QA_OUTPUT_TOKENS=2048
IRA_GRAPH_OUTPUT_TOKENS=4096
IRA_GRAPH_FACTS_PER_BATCH=12
IRA_COMPARISON_OUTPUT_TOKENS=3200
IRA_STYLE_PROBE_OUTPUT_TOKENS=256
IRA_STYLE_PROFILE_OUTPUT_TOKENS=4096
```

## Stage 容量公式

所有 Stage 遵循同一个物理不变量：

`stage_input <= context_window - stage_output - prompt_overhead - safety_margin`

但 `stage_output`、输入装箱方式、批粒度和延迟优先级按 Stage 分别定义；提高窗口时不得绕过 Profile 在局部模块自行放大。

交互通道遵循：

`interaction_input + interaction_history + interaction_output + system_prompt < context_window`

Graph 的 `facts_per_batch` 是输出容量边界，不是语义 Top-K。每条 Fact 都必须进入某个批次；若仍发生 `MODEL_OUTPUT_TRUNCATED`，系统递归拆分该批次，禁止用同一批次原样重试。

任何 Stage 的裁剪都必须记录 `raw/context/budget/truncated` 或等价指标。资源不足时允许缩小单批、增加批次或反馈 underfill，不允许静默丢弃 required facts、证据绑定或报告后半部分。固定 Top-K 只能作为候选召回的资源上限，最终选择应遵循“必需项保留、主题覆盖、缺口补检”。

## 硬件迁移验收

每次更换 GPU/NPU、量化精度、推理框架或并发配置后，使用相同 baseline 数据复测：

1. 所有结构化 Stage 的 JSON 完整率为 100%，输出截断率为 0；
2. Evidence 的 Fact/Evidence 溯源覆盖率不得下降；
3. Writer 的目标规模完成率和最终报告质量不得下降；
4. Graph 报告成功批次、自动拆批、终端失败 Fact 数，不允许静默空图；
5. 记录 P50/P95 的 Context、TTFT、Prefill、Decode、E2E、显存峰值和队列等待；
6. 在目标并发下验证 KV Cache 不触发 OOM，交互请求不会长期饿死工作流。
