# Report Assembly Agent

面向多来源材料的可追溯报告整编系统。材料经解析、理解、证据提取与分析后形成报告；用户可在任务检查点和报告审阅中介入。

## 项目文档

- [当前产品契约](docs/CURRENT_PRODUCT_CONTRACT.md)：任务模式、状态流程、交互、版本与证据边界。
- [当前代码地图](docs/CODEBASE_MAP.md)：主链路和模块入口。
- [模型运行与上下文约定](docs/MODEL_RUNTIME_ARCHITECTURE.md)：阶段预算、并发与硬件复测原则。
- [vLLM 生成服务](docs/VLLM_DEPLOYMENT.md)：部署配置和服务启动。
- [模板与软风格学习架构](docs/template-style-learning.md)：版式、风格画像、范例检索与渲染边界。

## 推理资源边界

有效配置以部署环境为准；代码默认值位于 app/config.py。各工作负载的输出预留、输入上限和批次策略由 app/runtime_profiles.py 提供，不能用某一个阶段的 token 比例代表全流程。

```text
阶段可用输入 = 物理上下文窗口 - 阶段输出预留 - Prompt 开销 - 安全余量
```

修改模型、量化、vLLM max-model-len、并发或显卡后，使用相同 benchmark 输入复测 Prefill、Decode、TTFT、端到端耗时、显存和报告质量。具体运行默认值不是远端机器的实际配置。

材料事实必须保持来源链；模型语义判断不得不可逆地删除原始内容。遇到实现与产品契约不一致时，先确认属于代码缺陷还是产品决定变化，再分别修正，不要在多份文档复制同一份流程描述。
