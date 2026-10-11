# 模型运行与上下文约定

本文说明运行机制，不复制易漂移的阶段 token 参数表。默认值见 **app/config.py**，各类工作负载的输入预算和批次策略见 **app/runtime_profiles.py**；部署环境变量可覆盖代码默认值。

## 模型与调度

- 文本生成经一个 OpenAI-compatible vLLM 服务；交互助手与工作流共用模型实例，不各自加载权重。
- 应用侧为工作流和协作助手分别设置有界并发入口。协作助手通过 review_copilot 通道；其他生成调用进入工作流通道。二者仍竞争同一模型的 GPU 资源。
- 应用侧调用队列、信号量与 vLLM 服务端 batching 是不同层次；增加应用并发不等于增加模型吞吐，需结合目标硬件压测。
- Embedding 由应用内本地 Transformers CPU runtime 执行；文档解析统一走 Docling，默认设备配置见 app/config.py。

## 上下文预算

各阶段共享一个物理上下文窗口，但不是共享同一种输入/输出比例。应用配置的默认物理窗口为 18,432 tokens；生产运行值以部署环境的 IRA_MODEL_CONTEXT_WINDOW_TOKENS 为准。

```text
阶段可用输入
  = 物理上下文窗口
  - 当前阶段输出预留
  - prompt 开销预留
  - 安全余量
```

Runtime Profile 可进一步限制输入预算，并指定批次策略和延迟类别。Evidence、Analysis、Narrative、Writer、Graph、交互助手等工作负载使用不同 profile；不得从某一阶段的预算推断另一阶段的输入/输出比例。Writer 的规模和生成单元由报告计划及 subsection 预算控制，不等价于单次模型输出上限。

app/config.py 中的值是默认配置，不代表某次远端服务的有效配置。复测或迁移硬件时同时记录：服务端 max-model-len、应用实际窗口、各 Stage 的输入/输出预留、模型/量化版本、并发、TTFT、Prefill/Decode、端到端耗时与资源占用。

## 截断、拆批与观测

- 单次输出上限与物理上下文是两个不同边界；提高其一不会自动解决另一边的预算不足。
- 拆批由具体阶段实现的 batch policy 决定。不能假定任意 JSON 截断都会自动重试或安全拆分；结构化阶段应核对该阶段的失败与恢复路径。
- 报告规模同时记录目标、有效最低字数、章节预算和实际篇幅；规模不足应作为可见偏差处理，不能靠无依据补写消除。
- LLM 调用记录见 app/token_monitor.py；任务 workload/token-efficiency 接口见 app/api/routes.py。性能字段是否可得取决于推理服务采集能力，不能仅凭应用调用统计推断 Prefill/Decode。

## 调参原则

1. 先读取部署环境的有效配置和 vLLM 启动参数，不凭文档默认值推断远端正在使用的数值。
2. 固定模型版本、精度、窗口、并发和 baseline 输入，再逐项改变参数。
3. 长输入阶段重点看输入 tokens、Prefill/TTFT、吞吐与内存；长输出阶段重点看输出 tokens、Decode/TPOT、超时和输出截断。
4. 每次调大输出预留，都同步核对可用输入预算；每次增加并发，都核对 KV Cache 峰值和排队时延。
5. 性能收益必须与 JSON 完整率、证据溯源、报告篇幅和人工质量评估一起比较。
