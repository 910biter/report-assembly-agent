# 全面重构蓝图(一轮):Evidence-based Intelligence Assembly

参考:用户 28 节方案 + 7 篇论文(Attribute First/FIRE/FER/RAV/Complex Claim/GraphRAG/Survey)

## 目标目录(职责边界)

```
app/
├── parsing/          # 文档智能(现有 docling_adapter 移入)
├── planning/         # task_understanding / evidence_needs / report_planner / narrative
├── retrieval/        # query_compiler / dense / sparse / fusion / reranker / cache
├── evidence/         # extractor / validator / coverage / gap_retrieval
├── intelligence/     # consolidate(FactCluster/关系/时序/冲突) / ledger
├── analysis/         # analyzer(Fact→Inference, 窄职责)
├── writing/          # attribution / writer(薄) / report_ast
├── qa/               # evidence_qa / narrative_qa / style_qa / repair_router
├── infrastructure/   # qdrant / database(Repository) / model_scheduler / parse_scheduler / resource_policy
├── rendering/        # template / docx
└── api/              # routes
```

## 主链(数据流)

Material → Unit → Evidence → Fact → FactCluster → Inference → NarrativeTopic
→ AttributionPlan → ReportSentence(句级 provenance) → QA → Repair

## 现有文件映射

| 现有 | 目标 | 动作 |
|------|------|------|
| app/workflow/controller.py (1905行) | workflow 调度 + 拆分 | 拆分:planning/evidence/intelligence/analysis 逻辑迁出, controller 只留流程编排 |
| app/agents/writer.py (1346行) | writing/writer.py | 变薄:删找Fact/判断重要/补材料/扩写逻辑;只按 Topic+Attribution 成文 |
| app/agents/evidence.py | evidence/extractor.py + coverage.py + gap_retrieval.py | 拆分+主线已重构 |
| app/context.py | retrieval/ (query_compiler + fusion + reranker) | 拆分 |
| app/agents/planner.py | planning/ | needs 结构化(已做) |
| app/agents/narrative.py | planning/narrative.py | 删 [:160] 截断等硬编码 |
| app/agents/analysis.py | analysis/analyzer.py | 窄职责(Fact→Inference) |
| app/memory/style.py | planning/ + rendering/ | 模板分析 |
| app/parser/* | parsing/ | 移入 |
| app/token_monitor.py | infrastructure/ | 保留(调用统计) |
| app/scale.py / business.py | 删除(硬编码预算/覆盖规则) | 删除或并入 planning |
| app/db.py (512行, 31表) | infrastructure/database.py + Repository 接口 | 分层:Repository 接口 → 适配器(SQLite 先留, PG 后换) |
| app/retrieval/store.py | infrastructure/qdrant.py | 统一向量入 Qdrant, 删 SQLite 向量副本 |

## 新增模块

- intelligence/consolidate.py: FactCluster(多源印证/冲突/时序/来源关系)
- intelligence/ledger.py: IntelligenceLedger(needs/confirmed/clusters/gaps/timeline/entities)
- writing/attribution.py: AttributionPlan(Topic→观点→证据绑定)
- qa/: evidence/narrative/style QA(一次调用四类)+ repair_router(问题→路由)
- infrastructure/repository.py: FactRepository/RetrievalService/ReportRepository 接口

## 删除清单(硬编码/冗余)

- controller 内:维度×材料全库重复、固定 Top-K 业务化、[:8]/[:160] 截断、unused_fact 补写、章末 append
- scale.py/business.py 的固定系数逻辑
- SQLite 内向量存储副本(统一 Qdrant)
- dedup_materials 硬编码 0.88(改 LLM 语义去重或仅审计)
- narrative.py [:160] 截断

## 保留(质量不变量)

- quote 校验(bind_sources)程序掌控
- fact_exists 去重 + material_scan 审计
- 统一 LLM 调度(invoke)+ 动态模型放置
- 分层解析 + Parse Scheduler 锁
- faulthandler / 监控 / ulimit

## 实施顺序(每步验证绿)

1. infrastructure: repository 接口层 + db 拆分(不改业务)
2. retrieval: context → query_compiler/fusion/reranker 拆分
3. intelligence: consolidate + ledger(FactCluster/关系/时序)
4. analysis 窄化 + planning 迁移
5. writing: attribution + writer 变薄 + 句级 provenance
6. qa: 四类 QA + repair_router
7. 清理: 删除清单执行 + 硬编码清零
8. 数据库: Repository 适配器(SQLite→PG 预留)
