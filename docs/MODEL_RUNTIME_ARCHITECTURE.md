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

工作流上下文由各 Stage 按“输入证据 + 固定提示 + 输出预留 + 安全余量”计算，不允许用完整报告历史无限累积。Writer 只接收当前章节或小节所需的覆盖证据、章节关系、已使用事实标识和压缩后的 Report Memory。

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
```
