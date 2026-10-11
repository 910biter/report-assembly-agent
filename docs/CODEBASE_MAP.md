# 当前代码地图

本页用于快速定位当前实现，不是历史审计报告，也不复制函数清单或行号。业务语义以 [当前产品契约](CURRENT_PRODUCT_CONTRACT.md) 为准；阶段协议以代码和测试为准。源码重构后先更新本页的模块边界与链路，不维护易漂移的逐函数统计。

## 系统结构

```text
Vue SPA (frontend/) ── HTTP API (app/api/)
                              │
                       Task / TaskRun
                              │
                    Workflow Queue / Controller
          ┌───────────────────┼────────────────────┐
     Parsing/Docling     Evidence → Analysis     Planning
          │                    │                    │
          └────────────── Writer / QA / Export ─────┘
                              │
                    PostgreSQL (canonical)
                    Qdrant (optional vector backend)
                    Graph projection (optional)
```

## 主流程

唯一持久阶段协议位于 [app/workflow/stages.py](../app/workflow/stages.py)，阶段定义位于 [app/models/enums.py](../app/models/enums.py)。执行入口为 [WorkflowController.run_to_review](../app/workflow/controller.py)：

```text
parsing → material_analysis → [requirement_review]
→ planning → evidence → conflict → analysis → final_plan
→ [directory_review] → narrative → writing → qa → review
```

方括号阶段是用户选择协作模式后才出现的检查点。文档解析模式在材料理解后结束；材料对比模式在新增材料证据与冲突分析后形成只读对比结果，不进入普通报告写作。

知识图谱不是主流程阶段。Evidence 完成后主流程检查可复用图谱的覆盖情况；缺失部分会标记待构建，不因建图阻塞报告。图谱构建入口在任务 API，通过 PostgreSQL 持久任务队列执行。

## 模块职责

| 关注点 | 主要入口 |
|---|---|
| HTTP API、任务/报告/材料/模板端点 | [app/api/routes.py](../app/api/routes.py) |
| 阶段协议与主编排 | [app/workflow/stages.py](../app/workflow/stages.py)、[app/workflow/controller.py](../app/workflow/controller.py) |
| 主任务队列、暂停/恢复、阶段签名、检查点 | [app/workflow/queue.py](../app/workflow/queue.py)、[app/workflow/checkpoints.py](../app/workflow/checkpoints.py)、[app/workflow/stage_state.py](../app/workflow/stage_state.py) |
| 文档解析与材料结构化 | [app/parsing/docling_adapter.py](../app/parsing/docling_adapter.py)、[WorkflowController.analyze_materials](../app/workflow/controller.py) |
| 事实提取、来源绑定、冲突核验 | [app/evidence/extractor.py](../app/evidence/extractor.py) |
| 事实整合、关系账本、推论 | [app/intelligence/](../app/intelligence/)、[app/analysis/analyzer.py](../app/analysis/analyzer.py) |
| 计划、报告规模、叙事组织 | [app/planning/](../app/planning/) |
| 检索、上下文预算与模型调度 | [app/context.py](../app/context.py)、[app/retrieval/](../app/retrieval/)、[app/runtime_profiles.py](../app/runtime_profiles.py)、[app/gateway.py](../app/gateway.py)、[app/llm_scheduler.py](../app/llm_scheduler.py) |
| 写作、质量检查、标题和 Word 导出 | [app/writing/writer.py](../app/writing/writer.py)、[app/quality.py](../app/quality.py)、[app/export/docx.py](../app/export/docx.py) |
| 对话工具、提案与影响传播 | [app/interaction.py](../app/interaction.py)、[app/control_agent.py](../app/control_agent.py)、[app/interaction_policy.py](../app/interaction_policy.py) |
| 报告快照、版本差异与恢复 | [app/report_versions.py](../app/report_versions.py) |
| 新材料对比 | [app/material_comparison.py](../app/material_comparison.py) |
| 用户触发的图谱构建与查询 | [app/graph_jobs.py](../app/graph_jobs.py)、[app/graph/service.py](../app/graph/service.py) |
| 数据库 Schema 与 ORM 表定义 | [app/infrastructure/orm.py](../app/infrastructure/orm.py)、[app/db.py](../app/db.py) |
| 模板编译、学习与 DOCX 一致性 | [app/template_engine/](../app/template_engine/)、[app/memory/style.py](../app/memory/style.py) |
| UI 页面与共享组件 | [frontend/src/](../frontend/src/)、[web/spa/](../web/spa/) |

## 任务、报告和版本边界

- 一个报告任务可有多个 TaskRun；Run 的 revision 标识执行轮次，运行状态会变化。
- 报告通过 reports.task_id 归属任务。增量更新复用原任务并创建新 Run；材料对比使用独立的 cmp 任务并引用基线报告/版本。
- ReportVersion 是不可变快照；报告正文是当前可编辑状态。交互或增量重算不使用强制验收的候选工作区，完成后保存版本；捕获到运行异常时队列尝试恢复基线。
- 图谱由用户发起构建。主任务队列是进程内队列；服务重启后未完成任务会被置为暂停。图谱构建队列与主报告队列分开持久化。

## 验证入口

- 工作流检查点与阶段状态：`tests/test_workflow_checkpoints.py`、`tests/test_workflow_stage_state.py`、`tests/test_workflow_stage_contract.py`
- 交互提案与任务激活：`tests/test_interaction_runtime.py`、`tests/test_interaction_task_activation.py`
- 版本快照、差异与恢复：`tests/test_version_architecture.py`
- Evidence、Analysis 与上下文：对应 `tests/test_evidence_*.py`、`tests/test_analysis_*.py`、`tests/test_context*.py`
- 前端源码与编译产物：在 `frontend/` 执行 `npm run build`

深度配置分别见 [模型运行与上下文约定](MODEL_RUNTIME_ARCHITECTURE.md)、[vLLM 生成服务](VLLM_DEPLOYMENT.md) 和 [模板与软风格学习架构](template-style-learning.md)。产品承诺与实现边界见 [当前产品契约](CURRENT_PRODUCT_CONTRACT.md)。
