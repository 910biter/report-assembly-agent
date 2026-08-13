# 报告整编 Agent — 整体规划与开发计划

> 本文件是项目主线记录,开发过程中必须**经常阅读**,防止偏离主线。
> 每次动手前先对照"主线约束";每完成一个阶段更新"进度日志"。

---

## 0. 项目定位(一句话)

本地运行的**报告整编 Agent**:输入多份异构材料 + 主题 + 要求,内部完成
材料理解、事实提取、来源绑定、综合研判,输出一份自然语言情报报告
(Word .docx),并在在线端提供句级溯源与选择/编辑能力。

**不是**文档处理 pipeline,也**不是**普通 RAG 问答系统。
系统核心数据链:Material → Evidence → Fact → Inference → ReportSentence → Report。
Word 报告只是最终展示结果,不是核心数据对象。

## 1. 主线约束(防偏离清单,最重要)

### 1.1 架构约束
- [ ] Workflow Controller 编排 4 个 Agent:Planner / Evidence / Analysis / Writer
- [ ] 不做自治多 Agent、不做 Agent 自由通信、不做多模型协同
- [ ] 所有 Agent 用**同一个模型服务**,靠角色 Prompt + 工具区分
- [ ] 业务代码**一律不直接调用模型接口**,统一走 ModelGateway(generation/embedding/ocr)
- [ ] 模型推理走远端(100.120.119.108:8000);解析/存储/检索/记忆/知识/Web/导出全本地

### 1.2 来源分级(替代置信度,三档,命名固定)
- [ ] `MATERIAL_FACT` 材料事实 —— 直接来自材料,必存 source_file+page/paragraph+quote
- [ ] `MATERIAL_INFERENCE` 材料推断 —— 基于多个材料事实,必存 based_facts+reasoning_chain
- [ ] `EXTERNAL_INFORMATION` 非材料来源 —— 模型常识/外部知识,显式标注"非任务材料来源"
- [ ] 禁止用"模型知识"这个名称

### 1.3 输出形态
- [ ] 导出 Word:自然行文,**不显示**任何 [事实]/[推论] 标签
- [ ] 在线查看:增强阅读,每句可查 来源类型/原始材料/文件位置/原文片段/推论依据
- [ ] 用户可在线勾选保留、编辑文字;修改意见进入短期记忆

### 1.4 硬约束(继承需求文档)
- [ ] 无来源的事实不产生(宁可输出更少)
- [ ] 多源冲突只标注,不替用户裁定
- [ ] 禁止无依据的推断结论
- [ ] 风格优先级:用户本次要求 > 机构 Style Profile > 模型默认

### 1.5 复杂度控制(一个月 MVP)
- [ ] 单模型、单任务串行编排,无并发 Agent、无消息通信
- [ ] 知识层只做轻量 Entity/Event/Relation 三张表,不做图谱推理、不做本体学习
- [ ] 向量检索用 SQLite + numpy,不引入向量库服务
- [ ] 在线编辑为勾选+改文+排序,不做富文本协同
- [ ] 增量更新(⑨)本期只预留接口,不实现

## 2. 已确认的技术决策

| 项 | 决策 |
|---|---|
| 语言/框架 | Python + FastAPI + Uvicorn |
| 数据库 | SQLite(sqlite3 标准库,轻量;表结构见 3) |
| 模型网关 | ModelGateway Protocol + OllamaGateway,base_url 指向远端 |
| 解析 | PyMuPDF(pdf) / python-docx / python-pptx / openpyxl;OCR 走远端 glm-ocr |
| 检索 | 远端 qwen-embed 计算,本地 SQLite+numpy 余弦相似度去重 |
| 记忆 | short_term(任务上下文 JSON) + long_term(机构偏好/风格/历史索引) |
| 模板学习 | 冷启动:历史报告→Style Analysis→Style Profile{structure_pattern,writing_style,terminology,format_rule}→用户确认→锁定 |
| Web | FastAPI + Jinja2 + 原生 JS,无前端构建链 |
| 导出 | python-docx |
| 配置 | .env 的 IRA_* 键,pydantic-settings 读取 |

## 3. 数据模型(核心链)

Material → Unit → Evidence → Fact → Inference → ReportSentence → Report

- Material: 素材元数据 + 文件指纹
- Unit: 解析后的内容单元(text/table/caption/image,带页码/段落)
- Evidence: 来源绑定(source_file/page/paragraph/quote)
- Fact: MATERIAL_FACT,挂 evidence_ids + conflict_ids
- Conflict: 多源矛盾标注,不裁定
- Inference: MATERIAL_INFERENCE(挂 based_fact_ids+reasoning_chain)或 EXTERNAL_INFORMATION
- StyleProfile: structure_pattern/writing_style/terminology/format_rule + status(draft/confirmed/locked)
- ReportPlan: 结构 + 分析维度 + 用户要求
- Report / ReportSentence: 句级内容 + source_level + 依据链 + selected + 用户编辑
- ShortMemory: 任务上下文(阶段产物/用户修改)
- LongMemory: 机构偏好/风格/历史索引
- Entity/Event/Relation: 轻量知识关联层

## 4. 开发计划(分阶段)

### Phase 0 — 项目骨架
目标:目录、依赖、配置、数据库、模型网关就绪
交付:requirements.txt / .env / run.py / app/config.py / app/gateway.py / app/db.py / app/models/*
完成标准:db 建表可跑,gateway 指向远端可用

### Phase 1 — 解析层 parser
目标:PDF/Word/PPT/Excel/图片 → 结构化 Unit(带页码/段落/图注;图片 OCR+描述走远端)
交付:app/parser/{pdf,office,images}.py
完成标准:本地样例文件解析出带页码的结构化单元

### Phase 2 — 检索层 retrieval
目标:embedding 计算(远端) + 本地向量存储 + 余弦相似度去重
交付:app/retrieval/{embedder,store}.py
完成标准:转载素材可识别主条目/附属

### Phase 3 — 记忆层 + 冷启动模板学习
目标:短期记忆(任务上下文)、长期记忆(机构偏好/风格/历史索引)、Style Analysis 冷启动
交付:app/memory/{short_term,long_term,style}.py
完成标准:≥10 份历史报告 → Style Profile 草案 → 可确认/锁定

### Phase 4 — Agent 层 + Workflow Controller
目标:Planner/Evidence/Analysis/Writer 四 Agent + 任务状态机编排
交付:app/agents/*.py + app/workflow/{controller,tasks}.py
完成标准:一次完整任务(素材→计划→事实→推断→报告对象)可跑通

### Phase 5 — 轻量知识关联层
目标:实体/事件/关系抽取与关联,支撑历史关联与增量分析
交付:app/knowledge/graph.py
完成标准:报告沉淀时实体/事件可关联到历史

### Phase 6 — API + Web 在线查看/编辑
目标:REST 接口 + 在线增强阅读页(句级溯源、勾选、编辑)
交付:app/api/{routes,web}.py + web/*
完成标准:浏览器可查看报告、展开溯源、勾选编辑

### Phase 7 — Word 导出
目标:按勾选内容导出自然行文 .docx
交付:app/export/docx.py
完成标准:导出的 Word 无标签、结构符合 Style Profile

### Phase 8 — 端到端验证与打磨
目标:完整流程回归、边界处理、错误信息完善
完成标准:一份真实材料包跑通 解析→去重→计划→事实→推断→在线编辑→Word

## 5. 验收标准(来自需求文档 v0.2,本期范围)

- [ ] 支持 PDF/Word/PPT 为主、Excel 附属、内嵌图片解析(OCR+描述)
- [ ] 素材去重:同事件转载、同报告多版本可识别(主条目/附属)
- [ ] 每条事实可溯源到 文件名+页码/段落+原文片段,否则不得进入报告
- [ ] 跨文档拼接事实标注全部来源;语义改写仍算事实
- [ ] 多源冲突显式标注,不裁定
- [ ] 每条推断挂依据(事实清单)+ 来源分级,否则不得进入报告
- [ ] 来源分级三档明确,EXTERNAL_INFORMATION 显式标注非材料来源
- [ ] Word 自然行文,在线句级溯源完整
- [ ] 冷启动:≥10 份历史报告 → Style Profile → 用户确认 → 锁定 → 后续任务套用
- [ ] 风格优先级:用户要求 > Style Profile > 默认

## 6. 开发纪律

- 先分析根因再动手,想清楚再做,不急于改代码
- 死代码零容忍;禁止 bare except
- KISS/DRY;降低工具调用频率,每次调用后看结果再决定下一步
- 明显错误(重复/占位符/断链)交付前自行发现并修复
- 涉及外部副作用(写文件/网络)必须真实验证,不编造结果

## 7. 进度日志

### Phase 0-7 全部完成(2026-08-06)
- Phase 0 骨架:config(gateway 指向远端)/gateway(Ollama 协议+describe_image)/db(18 表)/models(6 文件)✓ 已验证建表
- Phase 1 解析:pdf(文本块+页码+内嵌图 OCR)/office(docx/pptx/xlsx)/images(OCR+描述降级)✓ 中文 PDF(文泉驿字体)解析验证通过
- Phase 2 检索:embedder(远端)+store(SQLite BLOB+numpy 余弦)✓ 去重/检索验证通过
- Phase 3 记忆:short_term/long_term/style(冷启动模板)✓ 模板 draft→confirmed→locked 验证通过
- Phase 4 Agent+编排:planner/evidence/analysis/writer + controller ✓ FakeGateway 离线端到端通过(来源绑定/跨维度去重/推断挂依据/句级分级)
- Phase 5 知识层:实体/事件/关系抽取与查询 ✓
- Phase 6 API+Web:routes(任务/报告/编辑/导出/模板)+页面(新建/进度/审阅/模板)✓ TestClient 全过
- Phase 7 导出:docx(勾选过滤+用户编辑+无标签,宋体)✓
- 质量:pyflakes 清零,裸 except 为零,真实服务冒烟 5 项全过

### 遗留事项(待接口文件/网络就绪)
- [x] 远端端口更正为 100.120.119.108:11434(Ollama 0.32.5,qwen-agent/qwen-embed/glm-ocr 在位)——已连通
- [x] 真实模型端到端:2 份中文材料跑通全流程,8 事实(带来源)+3 推断(挂依据)+1 外部信息,报告 5 章节 7 句分级正确,Word 导出正常,沉淀实体5/事件3/关系1
- [ ] 增量更新(⑨):finalize 已留沉淀点,增量重跑②④⑤⑥ 未实现(本期不做)
- [ ] 观察项:Writer 对同时引用 fact+inference 的句子按"有 fact 即 MATERIAL_FACT"分级,混合句可能更宜按内容判级(当前可接受)

### 真实材料暴露的问题与修复(2026-08-06,用户提供 2 模板+10 材料)
- [x] 远端 glm-ocr OCR 服务挂起(手动测试 90s 无响应)→ OCR/describe 超时 300s→60s,失败快速降级;**环境问题待远端恢复**(截图/内嵌图信息暂时丢失)
- [x] 附件6 操作手册内嵌 15 张图,逐张 OCR 空等 → 文档内嵌图片上限 3 张(parser/_MAX_EMBEDDED_IMAGES)
- [x] qwen-embed 在远端 CPU 上跑(0 VRAM),单次 30-60s,10 份材料 material 级 embedding 串行 ~10 分钟且偶发挂起 → embedding 失败容错跳过(不中断任务);批量优化留待
- [x] 附件5 .doc 老格式无转换工具 → 单材料解析失败容错(记录 parse_errors 继续)
- [ ] 主题"大学生暑期社会实践项目总结报告与规划"真实端到端运行结果(进行中)

### UI/风格/格式改造(2026-08-06,用户验收反馈 + 并行 UI 重构)
- [x] 任务级模板选择:create_task 支持 template_id,任务可选任意历史模板(草案/已确认/已锁定)或不选;controller._selected_profile 优先任务模板,fallback 全局锁定;writer 记录所选模板 id
- [x] 格式学习:StyleProfile 增 format_spec(JSON);docx 模板真实提取字体/字号/行距/页边距/标题样式(style.extract_docx_format);Word 导出应用(_apply_format);db 增列迁移(_migrate)
- [x] 行文模仿:StyleProfile 增 style_samples(从历史报告提取开篇段+中段,截断去重,≤6条);to_prompt_block 注入"行文范例(请模仿其句式与用词)",Writer few-shot 模仿;API 返回 style_samples
- [x] 准测试命令:scripts/verify.py(隔离 runtime + FakeGateway,24 项核心回归:模板/格式/范例/段落化/导出/API/页面),每次改动后运行 .venv/bin/python scripts/verify.py
- [x] Writer 输出兼容修复(真实模型暴露):模型实际输出顶层 {"paragraphs":[...]} 而非 {"sections":[...]},且段首句含"一、章节名" → _split_sections 每个段落独立成节 + 段首编号拆分为章节标题;无标题段落按 plan.structure 顺序回退填充;verify.py 增 2 项防回归
- [x] 真实材料验收(real2,模板#1):26 事实/3 推断/1 外部信息;重放修复后 16 句 5 章,导出宋体 12pt + 页边距 3.0/2.8/3.0/2.6cm 与模板完全一致;行文模仿模板句式("立德树人""统筹政策传导""内涵化规范化常态化长效化");report_3.docx 可在线审阅

### 风格库重构(2026-08-06,用户设计建议:单模板 → 机构级多模板 Style Library)
- [x] 数据层:新增 style_library / style_variants / style_samples 表(变体含 structure/writing_style/terminology/format_spec/writing_patterns/style_samples/source_reports/confidence/status 结构化 JSON 字段);旧 style_profiles 保留兼容
- [x] 冷启动重构:analyze_library() 逐份提取标题结构(docx 样式优先+正则)+ 多位置采样(开篇/正文/结尾)→ LLM 判定体裁 → 按体裁聚类 → 每组 LLM 提炼变体(结构化章节树/语言/术语 preferred+forbidden)→ 规则提取格式 dominant+alternatives → 落库 draft → 用户确认/改名/锁定
- [x] Writer 章节级生成:对 plan.structure 逐章调用模型(章节目标+事实/推断+变体约束),单章失败不中断;删除无调用方的 _split_sections(死代码零容忍)
- [x] 变体选择链路:controller._selected_variant 优先级 = 任务 variant_id > 兼容旧 template_id > 全局锁定变体 > 旧锁定模板;create_task 参数改 variant_id
- [x] API/UI:冷启动返回 variants 聚类结果;/api/style/variants 列表 + PATCH 改名 + confirm/lock;首页下拉改选变体;模板管理页展示变体卡片(结构/规范/术语/格式 dominant+备选/范例/来源报告);导出按 report 变体 dominant 格式
- [x] verify.py 更新为变体链路(24 项:聚类/结构化/dominant/分类型范例/变体选择/章节级生成/PATCH 改名等)

### 证据驱动架构升级(2026-08-06,用户架构审视:流水线 → 完整 Agent)
- [x] 数据链路:Material→Unit→Evidence→Claim→Fact 五层;新增 claims 表(fact_id/material_id/content/quote/source/fact_type/status);Evidence Agent 提取陈述→quote 校验→提升为 Fact(status=promoted)或保留待核(pending);fact_type 六类 EVENT/PERSON/LOCATION/TIME/NUMBER/STATEMENT
- [x] 冲突下沉 Claim 层:detect_conflicts 输入全部陈述(含 pending),输出 claim_ids + entries,只标注不裁定
- [x] Analysis 增强:analysis_type(TREND/IMPACT/RISK/CAUSE/PREDICTION)+ 输入来源冲突与历史实体知识(memory_block)
- [x] Planner 完整规划:objective/audience/report_type/required_facts(决定 Evidence 提取方向)
- [x] ChapterStyle:变体提炼输出章节类型化写法(目的/规则/示例),经 to_prompt_block 注入 Writer
- [x] Revision:edit_history 记录 editor/time;用户修改沉淀 user_memory 表(原句→改句,长期记忆 User 域)
- [x] 长期记忆拆分:long_memory.scope(institution/user)+ user_memory 表 + knowledge 表(Entity/Event/Relation)
- [x] 轻量润色:writer 生成后扫描变体 forbidden 术语,命中记录 polish_notes 供人工修正(不自动改)
- [x] verify.py 27 项(新增 Claim 提升/fact_type/analysis_type 断言)
- [x] 关键 Bug 修复:BaseAgent.generate_json 不接受 system 参数 → detect_conflicts 传 system 一直 TypeError 被吞,**冲突检测自 Phase 1 从未生效**(verify 无断言掩盖);已修(system 默认回退 self.role)+ verify/claims 脚本加冲突断言防回归

### 业务能力增强(2026-08-06,业务视角审视:材料理解/时间线/质量检查)
- [x] 材料理解层:Stage.MATERIAL_ANALYSIS + analyze_materials()(材料分析师 prompt:类型/主题/关键章节/实体/时间)→ material_insights 表(含 value_rank 价值排序,按篇幅排序)→ 引导 Evidence 提取(insight_block 注入 prompt)
- [x] 事件时间线:knowledge.load_events()(events 表含 time)+ controller._timeline_block() 按时间排序 → 注入 Writer 供时间线章节引用
- [x] 报告质量检查:app/quality.py(规则:句间二元组 Jaccard 重复检测/模板章节缺失/禁用术语违规;LLM:逻辑跳跃与引用一致性)→ qa_notes 存 task 供人工审核;取代原 polish_terms(死代码删除)
- [x] 进度条 10 步(新增④ 材料理解)
- [x] 时序修复:知识抽取提前到 _sink_knowledge(分析后、写作前),事件时间线在写作阶段即可引用;finalize 简化为置 final
- [x] 修复被吞异常:load_events/load_entity_names 为模块级函数,controller 按实例方法调用一直 AttributeError 被吞(历史实体参考从未生效)→ 改模块函数调用 + knowledge/__init__ 导出

### 小上下文架构:Context Manager(2026-08-06,用户设计:大模型依赖 → 上下文工程)
- [x] app/context.py:ContextManager 按阶段生成最小必要上下文;预算控制(BUDGET_TOKENS=6000,优先级:事实证据>任务要求>章节目标>风格规则>范例>辅助)
- [x] Hybrid 检索:embedding 相似度 + 实体/时间关键词补充(不唯向量);retrieve_units 返回带来源标注片段
- [x] 阶段上下文:Planner=主题+要求+材料摘要+风格(无正文);Evidence=维度检索 top-8 片段(替换 12000 字全量);Analysis=事实+冲突+时间线+历史知识;Writer=章节检索事实 top-10 + 按依据过滤推断
- [x] 阶段顺序调整:材料理解提前到规划前(Planner 可用摘要);进度条 ③ 材料理解 ④ 规划结构
- [x] 材料摘要增强:key_points(核心观点)字段
- [x] 修复:_inferences 补 based_fact_ids(章节推断过滤的引用链);_material_overview 死代码删除;verify/claims/business/stylelib 脚本统一 patch quality_mod(LLM QA 隔离)
- [x] 验证:context 9/9(最小上下文/检索/预算/章节子集)+ verify 30/30 + claims 14/14 + business 12/12 + stylelib 19/19

### 模板学习五层重构(2026-08-07,用户设计:模板=机构报告生产规范,非 Word 格式)
- [x] 五层模型:Format(物理格式)/Structure(报告结构)/Reasoning(分析逻辑)/Language(语言风格)/Institution Rules(机构规则)
- [x] Reasoning Profile(新):style_variants 加 reasoning_profile_json——analysis_framework(事实→影响→风险)/chapter_inputs(每章输入输出)/risk_expression/suggestion_style;LLM 提炼 + to_prompt_block 注入 Writer
- [x] Institution Rules(新):institution_rules_json——must_include/forbidden/word_count/inference_ratio/data_requirements;QA 新增 RULE_VIOLATION 检查(缺失/禁止/字数区间)
- [x] Format 补全:extract_docx_format 增 first_line_indent/space_before_after/page_cm/header_text/footer_text/table_font;docx 导出应用(缩进/段前段后/页面尺寸/页眉页脚)
- [x] QA 修复:去重 key 增加 note(缺失与字数问题的 (type,section,quote) 相同被吞)
- [x] verify 33 项(新增 reasoning/rules 落库与注入、RULE_VIOLATION 三类检出断言)

### 成文质量重构:Blueprint + SectionPlan + Report Memory(2026-08-07,用户设计:信息→组织的推理层)
- [x] Report Blueprint(报告蓝图):_build_blueprint() 规划后生成——core_judgment 核心判断/report_goal/narrative_logic 叙事逻辑(是什么→如何发展→什么影响→未来风险)/chapter_relations 章节递进关系/key_questions;存 task,注入 Writer 每章
- [x] SectionPlan(章节规划):Writer 每章先规划(目的/核心问题/要点/所需事实推断/exclude_content 禁止重复内容/字数/写作结构),再按规划生成;exclude_content 明确"本章不写什么"治章节重复
- [x] Report Memory(跨章一致性):核心观点/统一术语/已用事实/前章摘要(≤3 章),每章生成后更新,下一章 prompt 注入"前文已写内容不要重复"
- [x] QA 数字一致性:句子数字必须在其引用事实中出现(CITATION_MISMATCH,防模型改述出错)
- [x] verify 35 项(蓝图记录/SectionPlan 流程/数字一致性检出断言);全套脚本 116 项全绿

### 性能与规划重构(2026-08-07,用户设计:embedding 持久化 + 规划合并 + 数据血缘 + Hybrid 检索)
- [x] P0 Fact embedding 持久化:fact_vectors 表;evidence 每维度批量计算并缓存向量;context._retrieve_facts 只算 query 向量,事实向量从库读(消除每章重复 embedding:500 facts×10 章→10 次 query embedding);无缓存回退关键词
- [x] P1 规划合并:Planner 一次输出 ReportPlan(核心问题/核心判断/叙事逻辑)+ ChapterPlan[](每章:标题/回答的问题/核心判断/与上章关系/所需事实推断/禁止内容/下章承接);删除 Blueprint 独立调用与每章 SectionPlan 调用(每章省 1 次 LLM,消除逻辑漂移);Writer 只执行 ChapterPlan;检索 query = 标题+核心问题+核心判断+所需事实(逻辑与检索打通);旧格式 structure 回退兼容
- [x] Writer 过渡句放宽:FACT/ANALYSIS 必须有依据,TRANSITION(承上启下/导语)每章 ≤2 句可无引用;auto_revision 跳过 TRANSITION 不删
- [x] 数据血缘表:report_sentence_fact/report_sentence_inference 写入(修复 cur.lastrowid 错位 bug);inference_fact/event 关联表已建(写入待 P2);7 个外键索引
- [x] verify 40 项(规划含核心判断/章节规划、过渡句保留、血缘表、向量缓存检索)
- [x] P2 血缘写入:save_inference 写 inference_fact(推断→依据事实);report_sentence_fact/inference 已写入;fact_evidence 由 evidence.fact_id 外键覆盖(无需新表);event 关联表未建(无消费方,KISS)
- [x] P3 Hybrid 检索:units_fts FTS5 trigram 虚拟表 + 插入/删除触发器 + init_db 幂等重建;search_units 改 FTS 关键词候选过滤 + 向量余弦排序,候选不足全量补足,无 query_text 回退全量;FTS query 清洗转义
- [x] 任务隔离:facts 表加 task_id(建表+迁移),extract_evidence 带 task_id 写入;读取已按 task fact_ids 过滤(双保险);idx_facts_task 索引
- [x] 解析批量事务:每份材料一个事务批量写 Units + Embeddings(替代逐条事务,减少 fsync)
- [x] verify 40/40 + 11 套 /tmp 脚本全绿 + FTS5 Hybrid 单测通过

### 性能优化补全(2026-08-07,任务书:埋点/缓存/去重/预筛/图片过滤)
- [x] 完整埋点:run_to_review 阶段计时(stage_timings:parse→write 各阶段)+ LLM 调用统计(base.py 计数器:次数+输入规模,含复用自动体现)
- [x] Material Cache:上传 SHA256 去重(create_task,同文件复用 material_id,解析/向量/理解全缓存)+ file_hash 列与索引;fingerprint 兼容(写入=file_hash)
- [x] Material embedding 聚合:材料向量 = unit 向量均值(不再独立调用 embedding 模型)
- [x] Conflict 规则预筛:共享数字/3-gram 候选才调 LLM(_conflict_candidates);无候选零调用
- [x] 知识抽取去重:_sink_knowledge 实体直接复用材料理解 entities(零 LLM),LLM 只补事件/关系
- [x] 图片过滤:小图/logo(<40x40)跳过 OCR 与描述(_is_trivial_image,PIL 解码)
- [x] 调用对比:LLM 34→28 次(首任务)/16 次(二次任务);embedding ~2500→~250 次;material embedding 12→0
- [x] verify 42 项(LLM 统计/聚合向量/哈希去重/预筛断言)+ 15 套脚本全绿

### 报告规模控制机制(2026-08-07,任务书:预算驱动长度,避免凑字数)
- [x] Report Budget:Planner 一次输出 report_budget(target_words/soft_max/hard_max/summary_budget,report_plans.budget 列);档位:简要2000-4000/标准5000-10000/深度10000-20000,用户明确字数优先
- [x] 章节预算:ChapterPlan 增 target_words/importance(high/medium/low)/evidence_density;按重要性与信息密度分配,不平均
- [x] Writer 软预算:prompt 注入本章预算 + 完成条件(核心问题已回答/所需事实覆盖/判断形成/无新价值信息/达目标70%可结束),禁止凑字数扩写
- [x] QA 信息密度检查:DENSITY_ISSUE(无事实/推断依据句占比 <60%)
- [x] 局部压缩:超 hard_max 时删多余过渡句/同节高重复句(不硬截断、不删有依据句);绝不重生成整章
- [x] 规模统计:task.report_stats(target/actual/章节预算与实况/facts_per_1000/compress_count)
- [x] verify 45 项(budget 落库/章节预算/规模统计断言)+ 16 套脚本全绿
- [x] 成文化:Writer 输出 paragraphs 结构(段落内 2-4 句衔接),report_sentences 增 paragraph 列,导出按段合并;兼容旧 sentences 格式
- [x] 任务工作台:/tasks/{id} 步骤进度条(9 步)+ 材料解析状态(/api/tasks/{id}/materials)+ 分析结果(事实/推断/冲突,/api/tasks/{id}/analysis)
- [x] 报告编辑三段式:左目录 + 中正文(段落化、句级 trace-chip 标签、可编辑/勾选)+ 右证据面板(来源类型/文件/页码/原文/依据/修改历史,AI 原文 vs 用户修改)
- [x] 材料库/历史报告/系统设置页(用户并行实现)+ base.html 左侧导航布局;API 已齐(/api/materials、/api/materials/{id}、/api/reports)
- [x] web 目录定位改用 __file__(不依赖 runtime_root 相对位置)
- [ ] 观察项:Writer 混合引用句(同时挂 fact+inference)按"有 fact 即 MATERIAL_FACT"分级,可接受
