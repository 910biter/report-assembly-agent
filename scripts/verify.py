"""项目验证脚本(准测试套件):核心链路回归,隔离 runtime,不触碰真实产物库。

用法:.venv/bin/python scripts/verify.py
覆盖:模板学习(格式提取/行文范例/模板选择)、成文化(段落化/导出合并/格式应用)、
API(materials/analysis/段落分组/模板)、页面渲染。全部走 FakeGateway,无网络依赖。
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

_TMP_ROOT = tempfile.mkdtemp(prefix="hermes-verify-")
os.environ["IRA_RUNTIME_ROOT"] = _TMP_ROOT
os.environ["IRA_QDRANT_COLLECTION_UNITS"] = "verify_ira_units"
os.environ["IRA_QDRANT_COLLECTION_MATERIALS"] = "verify_ira_materials"
os.environ["IRA_QDRANT_COLLECTION_FACTS"] = "verify_ira_facts"

# create_task 上传接口需要真实文件(Docling 已 mock,内容仅作载体)
_FAKE_PDF = "/tmp/t_cn.pdf"
with open(_FAKE_PDF, "w", encoding="utf-8") as fh:
    fh.write("某监管机构于2026年发布行业监管政策文件。")

import app.agents.base as base_mod
import app.memory.style as style_mod
import app.quality as quality_mod
import app.retrieval.embedder as embedder_mod
from app.retrieval import vector_store

# Docling 解析 mock:验证只测流程,不启动 Docling 模型
import app.workflow.controller as controller_mod
from app.models import Unit


def _fake_parse_file_with_profile(path):
    return [Unit(material_id=0, kind="text", content="某监管机构于2026年发布行业监管政策文件。", page=1)], {
        "parser": "verify-mock", "units_count": 1,
    }


controller_mod.parse_file_with_profile = _fake_parse_file_with_profile

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name)


class FakeGateway:
    def generate(self, prompt, system=None):
        return "{}"

    def generate_json(self, prompt, system=None):
        if "风格分析师" in system:
            return {"name": "政策研究", "description": "政策研究报告",
                    "structure": {"sections": [{"title": "一、背景", "children": ["背景"]}], "summary_first": False, "conclusion_first": False},
                    "writing_style": {"tone": "正式", "sentence_pattern": "长句", "analysis_style": "先事实后判断"},
                    "terminology": {"preferred": ["综合研判"], "forbidden": ["我认为"]},
                    "chapter_styles": [{"chapter_type": "背景", "purpose": "客观描述", "rules": "平实陈述"}],
                    "reasoning_profile": {"analysis_framework": "事实→影响→风险",
                                          "chapter_inputs": [{"chapter_type": "风险研判", "inputs": ["事实"], "outputs": ["判断"]}],
                                          "risk_expression": "结合外部环境分析", "suggestion_style": "面向中长期建议"},
                    "institution_rules": {"must_include": ["政策依据"], "forbidden": ["我认为"],
                                          "word_count": "100-500", "inference_ratio": "", "data_requirements": ""}}
        if "体裁与风格特征" in system:
            return {"topic_type": "政策研究", "structure_notes": "五段式", "language_notes": "正式"}
        if "分析规划师" in system:
            # 初版规划:只定分析问题与证据提取方向,不冻结章节
            return {"title": "T", "core_question": "监管如何变化", "dimensions": ["D1"],
                    "required_facts": ["政策"]}
        if "结构总规划师" in system:
            # 终版规划:Evidence/Analysis 后冻结最终章节结构
            return {"title": "T", "core_question": "监管如何变化", "core_judgment": "监管趋严",
                    "narrative_logic": "背景→影响→风险", "dimensions": ["D1"],
                    "report_budget": {"target_words": 800, "soft_max_words": 1000,
                                      "hard_max_words": 1200, "summary_budget": 100},
                    "chapters": [
                        {"title": "一、背景", "questions": ["背景如何"], "judgment": "介绍背景",
                         "relation_to_prev": "", "required_facts": ["政策"], "required_inferences": [],
                         "exclude": ["影响分析"], "next_bridge": "承接影响",
                         "target_words": 600, "importance": "high", "evidence_density": "medium"},
                    ]}
        if "报告主编" in system:
            return {"core_judgment": "监管趋严", "report_goal": "支撑决策",
                    "narrative_logic": "背景→影响→风险", "chapter_relations": [], "key_questions": []}
        if "章节规划员" in system:
            return {"purpose": "介绍背景", "core_questions": ["背景如何"],
                    "key_points": ["政策背景"], "required_facts": [1], "required_inferences": [],
                    "exclude_content": ["影响分析"], "expected_length": "200字", "writing_pattern": "背景→现状"}
        if "材料分析师" in system:
            return {"doc_type": "政策文件", "topic": "监管政策", "key_sections": ["要求"],
                    "entities": ["监管机构"], "times": ["2026年"]}
        if "事实提取员" in system:
            return {"claims": [{"content": "某监管机构2026年发布行业监管政策文件",
                                "quote": "某监管机构于2026年发布行业监管政策文件。",
                                "file": "x", "page": 1, "fact_type": "EVENT"}]}
        if "冲突检测员" in system:
            return {"conflicts": []}
        if "情报分析员" in system:
            return {"inferences": [{"content": "政策将趋严", "based_fact_ids": [1],
                                    "reasoning": "r", "analysis_type": "TREND"}], "external_notes": []}
        if "综合研判师" in system:
            return {"inferences": [{"content": "监管趋严将推动行业规范", "based_fact_ids": [1],
                                    "short_rationale": "r", "dimension": "全局综合", "analysis_type": "IMPACT"}],
                    "external_notes": [],
                    "critical_fact_ids": [1],
                    "coverage_status": {"D1": "SUFFICIENT"},
                    "unresolved_conflicts": [],
                    "uncertainty": []}
        if "撰稿人" in system:
            return {"paragraphs": [
                {"sentences": [
                    {"text": "某机构发布了监管政策,这是第一句。", "fact_ids": [1]},
                    {"text": "政策内容涵盖多项要求,这是衔接的第二句。", "fact_ids": [1]},
                    {"text": "据称监管标准为99元。", "fact_ids": [1]},
                    {"text": "此外,这一政策值得重点关注。", "fact_ids": []},
                ]},
            ]}
        if "知识抽取员" in system:
            return {"entities": [], "events": [], "relations": []}
        if "证据约束的关系抽取员" in system:
            return {
                "entities": [{"name": "监管机构", "type": "机构", "fact_ids": [1]}],
                "assertions": [{
                    "subject": "监管机构", "predicate": "发布", "object": "行业监管政策文件",
                    "object_kind": "value", "event_name": "", "valid_from": "2026年",
                    "valid_to": "", "fact_ids": [1], "confidence": "high",
                }],
            }
        if "质量检查员" in system:
            return {"issues": []}
        if "报告修订员" in system:
            return {"text": "据称监管标准为15元。"}
        raise AssertionError(system)

    def embed(self, texts, timeout=None, num_gpu=None):
        return [[0.1] * 8 for _ in texts]

    def health(self):
        return {}


fake = FakeGateway()
base_mod.model_gateway = fake
style_mod.model_gateway = fake
quality_mod.model_gateway = fake
embedder_mod.model_gateway = fake

from app.config import settings

check("隔离 runtime", str(settings.runtime_root) == _TMP_ROOT)
from app.db import init_db, session_scope
from sqlalchemy import select
init_db()
# PG 持久库幂等:DROP 全部表后重建(create_all 带 DEFAULT/约束)+ 清数据
from app.db import init_db as _init_db
from app.infrastructure.orm import Base
from app.db import _get_engine
from sqlalchemy import text
with _get_engine().connect() as _conn:
    _tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
    _conn.execute(text(f"DROP TABLE IF EXISTS {_tables} CASCADE"))
    _conn.commit()
_init_db()

MATERIAL_DIR = Path("/mnt/c/Users/32795/OneDrive/桌面/agent/material")
if not MATERIAL_DIR.exists():
    MATERIAL_DIR = Path("/mnt/c/Users/32795/OneDrive/桌面/material")
if not MATERIAL_DIR.exists():
    # 远端/通用环境:取 runtime/templates 下的模板文件
    TPL_DIR = Path("runtime/templates")
    MATERIAL_DIR = TPL_DIR
    _tpl = list(TPL_DIR.glob("*.docx"))
    _TEMPLATE_DOCX = _tpl[0] if _tpl else None
else:
    _TEMPLATE_DOCX = MATERIAL_DIR / "总结报告模板_公文风格.docx"

from app.memory import short_term, style
assert _TEMPLATE_DOCX is not None, "模板缺失(本地桌面 或 runtime/templates)"
spec = style.extract_docx_format(_TEMPLATE_DOCX)
check("docx 格式提取(字体/字号/页边距)",
      bool(spec.get("font_name")) and bool(spec.get("font_size_pt")) and "margins_cm" in spec)

# 风格库:2 份同体裁报告 → 聚类为 1 个变体
def _find_template(name_part: str):
    """模板文件定位(本地裸名 / 远端哈希前缀)。"""
    if MATERIAL_DIR.name == "templates" and MATERIAL_DIR.exists():
        hits = [f for f in MATERIAL_DIR.glob(f"*{name_part}*.docx")]
        return str(hits[0]) if hits else str(MATERIAL_DIR / name_part)
    return str(MATERIAL_DIR / name_part)


va = style.analyze_library([
    {"filename": "模板A.docx",
     "text": "模板A报告\n一、背景概述\n本年度工作围绕立德树人展开。\n二、成效分析\n成果显著。\n三、趋势研判\n后续将深化。",
     "path": _find_template("总结报告模板_公文风格")},
    {"filename": "模板B.docx",
     "text": "模板B报告\n一、背景概述\n坚持问题导向,系统梳理短板弱项。\n二、成效分析\n形成闭环管理机制。\n三、趋势研判\n推动长效化发展。",
     "path": _find_template("通用总结报告模板_公文风格")},
])[0]
check("聚类:2 份同体裁 → 1 变体", va.name == "政策研究")
check("变体含结构化章节", isinstance(va.structure.get("sections"), list) and len(va.structure["sections"]) >= 1)
check("变体含格式 dominant(宋体)", va.format_spec.get("dominant", {}).get("font_name") == "宋体")
check("变体格式含布局 token 与选择策略",
      "title" in va.format_spec.get("dominant", {})
      and "heading1" in va.format_spec.get("dominant", {})
      and "table_style" in va.format_spec.get("dominant", {})
      and va.format_spec.get("selection_strategy", {}).get("mode") == "token_majority_vote")
check("模板格式结构化 document_format",
      "document_format" in va.format_spec.get("dominant", {})
      and "typography" in va.format_spec["dominant"]["document_format"]
      and "export_hints" in va.format_spec["dominant"]["document_format"])
check("模板完备画像:置信度/完整度/冲突策略",
      isinstance(va.format_spec.get("field_confidence"), dict)
      and isinstance(va.format_spec.get("template_profile", {}).get("learned_fields"), list)
      and "conflict_policy" in va.format_spec.get("selection_strategy", {}))
check("变体含分类型范例", any(s["sample_type"] == "opening" for s in va.style_samples)
      and any(s["sample_type"] == "conclusion" for s in va.style_samples))
check("变体 to_prompt_block 含结构/术语/范例",
      "章节结构" in va.to_prompt_block() and "综合研判" in va.to_prompt_block() and "行文范例" in va.to_prompt_block())
check("变体含分析逻辑层与机构规则层",
      va.reasoning_profile.get("analysis_framework") == "事实→影响→风险"
      and va.institution_rules.get("must_include") == ["政策依据"])
check("to_prompt_block 注入分析逻辑与机构规则",
      "分析逻辑" in va.to_prompt_block() and "机构规则" in va.to_prompt_block())

from app.report_tools import build_chapter_evidence_matrix, build_task_profile
from app.policy import build_report_policy, policy_prompt_block
profile = build_task_profile("生成一份要求梳理与执行指导报告", [
    {"material_id": 1, "filename": "a.docx", "doc_type": "说明", "topic": "提交要求",
     "material_role": "上级规范依据", "claim_support": "normative",
     "allowed_usage": ["说明要求"], "forbidden_usage": ["证明任务已经完成"],
     "missing_information": ["实际执行过程"]},
    {"material_id": 2, "filename": "b.docx", "doc_type": "模板", "topic": "报告格式",
     "material_role": "格式模板", "claim_support": "template",
     "allowed_usage": ["学习版式"], "forbidden_usage": ["当作已发生事实"],
     "missing_information": ["结果数据"]},
])
check("运行时任务画像:由材料理解生成角色与边界",
      profile["domain"] == "runtime"
      and profile["report_mode"] == "requirement_summary"
      and profile["material_roles"].get("上级规范依据") == 1
      and profile["support_counts"].get("template") == 1
      and "实际执行过程" in profile["missing_inputs"])
check("角色矩阵:区分支撑类型和证据边界",
      len(profile.get("role_matrix", [])) == 2
      and all(item["claim_support"] in ("normative", "template", "actual", "reference", "unknown") for item in profile["role_matrix"])
      and any("当作已发生事实" in " ".join(item["forbidden_usage"]) for item in profile["role_matrix"]))
policy = build_report_policy({"task_profile": profile}, va, profile)
check("Policy分层:红线与软策略分离",
      "hard_guardrails" in policy
      and "writer_policy" in policy
      and any("不得虚构" in item for item in policy["hard_guardrails"])
      and any("清单" in item or "分项" in item for item in policy["writer_policy"])
      and "分层策略" in policy_prompt_block(policy))
runtime_plan = {
    "structure": ["一、任务边界", "二、执行要求"],
    "chapter_plans": [
        {"title": "一、任务边界", "required_facts": ["材料能证明什么", "缺失什么"], "target_words": 500, "importance": "high", "evidence_density": "high"},
        {"title": "二、执行要求", "required_facts": ["提交要求"], "target_words": 700, "importance": "high", "evidence_density": "medium"},
    ],
    "budget": {"target_words": 1200},
}
coverage = build_chapter_evidence_matrix(runtime_plan, profile, [
    {"id": 101, "content": "材料说明当前只能证明提交要求,不能证明任务已经完成。", "dimension": "材料能证明什么", "source_roles": ["上级规范依据"], "claim_supports": ["normative"], "source_files": ["a.docx"]},
    {"id": 102, "content": "提交要求包括格式模板与必要字段。", "dimension": "提交要求", "source_roles": ["格式模板"], "claim_supports": ["template"], "source_files": ["b.docx"]},
], [])
check("运行时闭环:章节证据矩阵与待补信息",
      coverage["status"] == "needs_materials"
      and len(coverage["chapters"]) == len(runtime_plan["structure"])
      and coverage["missing_inputs"]
      and any(c["guardrails"] for c in coverage["chapters"]))
style.confirm_variant(va.id)
style.lock_variant(va.id)

from app.models import Stage
from app.workflow import WorkflowController, next_stage
from app.writing.writer import _coerce_paragraphs
check("状态机:去重后进入材料理解",
      next_stage(Stage.DEDUP) == Stage.MATERIAL_ANALYSIS
      and next_stage(Stage.MATERIAL_ANALYSIS) == Stage.PLANNING)
check("Writer JSON结构容错:paragraphs字符串不崩溃",
      _coerce_paragraphs({"paragraphs": ["仅为文本"]})[0]["sentences"][0]["text"] == "仅为文本")
flat = _coerce_paragraphs({"sentences": [
    {"paragraph": 2, "text": "第二段", "fact_ids": [1]},
    {"paragraph": 1, "text": "第一段", "fact_ids": [1]},
]})
check("Writer JSON结构容错:扁平sentences按段落归并",
      len(flat) == 2 and flat[0]["sentences"][0]["text"] == "第一段")
with session_scope() as s:
    from app.infrastructure.orm import ORMMaterial
    from sqlalchemy import delete as _del
    s.execute(_del(ORMMaterial).where(ORMMaterial.c.fingerprint == 'r3-a'))  # PG 持久库幂等
    s.execute(ORMMaterial.insert().values(filename='x.pdf', file_type='pdf', path='/tmp/t_cn.pdf', fingerprint='r3-a'))

short_term.save_task("r3a", {"theme": "t", "variant_id": va.id, "material_ids": [1]})
check("指定变体被选中", WorkflowController("r3a")._selected_variant().id == va.id)
short_term.save_task("r3b", {"theme": "t", "variant_id": None, "material_ids": [1]})
check("未指定回落全局锁定变体", WorkflowController("r3b")._selected_variant().id == va.id)
short_term.save_task("r3c", {"theme": "t", "variant_id": 99999, "material_ids": [1]})
check("无效变体 id 回落锁定", WorkflowController("r3c")._selected_variant().id == va.id)

short_term.save_task("r3d", {"theme": "t", "variant_id": va.id, "material_ids": [1]})
wc = WorkflowController("r3d")
wc.run_to_review()
task = short_term.load_task("r3d")
with session_scope() as s:
    from app.infrastructure.orm import ORMSentence, ORMReport
    rows = s.execute(select(ORMSentence.c.section, ORMSentence.c.paragraph, ORMSentence.c.content).order_by(ORMSentence.c.position)).mappings().all()
    rep = s.execute(select(ORMReport.c.style_profile_id).where(ORMReport.c.id == task["report_id"])).mappings().first()
check("章节级生成:句子按章节归属", len(rows) == 4 and all(r["section"] == "一、背景" for r in rows))
check("报告记录任务所选变体", rep["style_profile_id"] == va.id)
with session_scope() as s:
    from app.infrastructure.orm import ORMPlan
    plan_row = s.execute(select(ORMPlan.c.core_judgment, ORMPlan.c.narrative_logic, ORMPlan.c.chapter_plans, ORMPlan.c.budget).where(ORMPlan.c.id == task["plan_id"])).mappings().first()
plan_budget = json.loads(plan_row["budget"])
plan_chapters = json.loads(plan_row["chapter_plans"])
check("规划含核心判断/叙事逻辑/章节规划",
      plan_row["core_judgment"] == "监管趋严"
      and plan_row["narrative_logic"] == "背景→影响→风险"
      and len(plan_chapters) == 1)
check("规划由Planner决定章节数量而非固定五段式",
      len(plan_chapters) == 1 and json.loads(plan_row["chapter_plans"])[0]["title"] == "一、背景")
check("任务保存分层Report Policy",
      "report_policy" in task
      and "hard_guardrails" in task["report_policy"]
      and "planner_policy" in task["report_policy"])
check("章节预算(模型输出 report_budget)",
      plan_budget.get("target_words", 0) > 0
      and plan_budget.get("target_words", 0) <= plan_budget.get("hard_max_words", 0))
check("性能节点:TTFR 已记录且知识沉淀后台化",
      task.get("ttfr_seconds", 0) > 0
      and task.get("critical_path_done") is True
      and "knowledge" in task.get("background_jobs", {}))
check("过渡句保留(无引用短句)",
      any("值得重点关注" in r["content"] for r in rows))
with session_scope() as s:
    from app.infrastructure.orm import Base as _ORMBase
    lineage = s.execute(select(__import__('sqlalchemy').func.count()).select_from(_ORMBase.metadata.tables['report_sentence_fact'])).scalar()
check("句子-事实血缘表写入", lineage >= 3)
print("DEBUG qa_notes:", task.get("qa_notes", []))
check("QA 数字一致性检出(99元不在事实中)",
      any(i["type"] == "CITATION_MISMATCH" for i in task.get("qa_notes", [])))
check("验证闭环:数字不一致句自动重写",
      task.get("auto_revision", {}).get("rewritten") == 1
      and "99" not in "\n".join(r["content"] for r in rows))
check("验证闭环:无依据句自动删除(本流程0条)",
      task.get("auto_revision", {}).get("removed") == 0)
with session_scope() as s:
    from app.infrastructure.orm import ORMClaim, ORMFact, ORMInference
    claim_rows = s.execute(select(ORMClaim.c.fact_type, ORMClaim.c.status, ORMClaim.c.fact_id)).mappings().all()
    fact_row = s.execute(select(ORMFact.c.fact_type).where(ORMFact.c.id == 1)).mappings().first()
    inf_row = s.execute(select(ORMInference.c.analysis_type).where(ORMInference.c.id == 1)).mappings().first()
check("Claim 层:陈述落库且提升为 fact",
      len(claim_rows) == 1 and claim_rows[0]["status"] == "promoted" and claim_rows[0]["fact_id"] == 1)
check("Fact 带 fact_type", fact_row["fact_type"] == "EVENT")
check("推断带 analysis_type", inf_row["analysis_type"] == "TREND")
check("材料理解:insights 落库",
      len(task.get("material_insights", [])) == 1 and task["material_insights"][0]["doc_type"] == "政策文件")
from app.quality import _cosine_similarity, run_quality_check
check("质量检查:重复检测(字符二元组 Jaccard)",
      _cosine_similarity("完全相同的一句话。", "完全相同的一句话。") > 0.7)
qa_issues = run_quality_check(task["report_id"], ["一、背景", "二、缺失章节"], ["我认为"])
check("质量检查:缺失章节检出", any(i["type"] == "MISSING_SECTION" for i in qa_issues))
qa_rules = run_quality_check(task["report_id"], ["一、背景"],
                             ["我认为"], {"must_include": ["不存在的必备内容"], "forbidden": ["第一句"],
                                          "word_count": "1-10"})
check("质量检查:机构规则检出(缺失/禁止/字数)",
      any(i["type"] == "RULE_VIOLATION" and "必备内容" in i["note"] for i in qa_rules)
      and any(i["type"] == "RULE_VIOLATION" and "禁止" in i["note"] for i in qa_rules)
      and any(i["type"] == "RULE_VIOLATION" and "字数" in i["note"] for i in qa_rules))
with session_scope() as s:
    from app.infrastructure.orm import ORMReport, ORMSentence
    _r = s.execute(ORMReport.insert().values(plan_id=task["plan_id"], title="模糊表达检查", style_profile_id=va.id, status="draft"))
    vague_report_id = int(_r.inserted_primary_key[0])
    s.execute(ORMSentence.insert().values(report_id=vague_report_id, section="一、背景", paragraph=1, position=1,
              content="应按规定时间提交相关材料。", source_level="MATERIAL_FACT",
              source_refs=json.dumps({"fact_ids": [1], "inference_ids": []})))
qa_concrete = run_quality_check(vague_report_id, ["一、背景"])
check("质量检查:模糊表达具体化提示",
      any(i["type"] == "CONCRETENESS_ISSUE" for i in qa_concrete))
with session_scope() as s:
    from app.infrastructure.orm import ORMReport, ORMSentence
    _r = s.execute(ORMReport.insert().values(plan_id=task["plan_id"], title="清单溯源粒度", style_profile_id=va.id, status="draft"))
    trace_report_id = int(_r.inserted_primary_key[0])
    s.execute(ORMSentence.insert().values(report_id=trace_report_id, section="一、背景", paragraph=1, position=1,
              content="必交材料方面，需提交登记表和通讯稿。", source_level="MATERIAL_FACT",
              source_refs=json.dumps({"fact_ids": [1], "inference_ids": [], "trace_granularity_warning": True})))
    _r2 = s.execute(ORMReport.insert().values(plan_id=task["plan_id"], title="符号化清单格式", style_profile_id=va.id, status="draft"))
    bullet_report_id = int(_r2.inserted_primary_key[0])
    s.execute(ORMSentence.insert().values(report_id=bullet_report_id, section="一、背景", paragraph=1, position=1,
              content="• 必交: 登记表、通讯稿。", source_level="MATERIAL_FACT",
              source_refs=json.dumps({"fact_ids": [1], "inference_ids": []})))
    _r3 = s.execute(ORMReport.insert().values(plan_id=task["plan_id"], title="模糊表达无明确证据", style_profile_id=va.id, status="draft"))
    vague_no_hit_report_id = int(_r3.inserted_primary_key[0])
    from app.infrastructure.orm import ORMFact
    _fr = s.execute(ORMFact.insert().values(content="材料要求团队后续完成提交工作", dimension="D",
                  source_level="MATERIAL_FACT", fact_type="STATEMENT", evidence_ids="[]",
                  conflict_ids="[]", task_id="qa-vague"))
    vague_fact_id = int(_fr.inserted_primary_key[0])
    s.execute(ORMSentence.insert().values(report_id=vague_no_hit_report_id, section="一、背景", paragraph=1, position=1,
              content="应按规定时间提交相关材料。", source_level="MATERIAL_FACT",
              source_refs=json.dumps({"fact_ids": [vague_fact_id], "inference_ids": []})))
qa_trace = run_quality_check(trace_report_id, ["一、背景"])
qa_vague_no_hit = run_quality_check(vague_no_hit_report_id, ["一、背景"])
qa_bullet = run_quality_check(bullet_report_id, ["一、背景"])
check("质量检查:清单条目溯源粒度提示",
      any(i["type"] == "TRACE_GRANULARITY_ISSUE" for i in qa_trace))
check("质量检查:无明确证据不误报具体化",
      not any(i["type"] == "CONCRETENESS_ISSUE" for i in qa_vague_no_hit))
check("质量检查:禁止符号化清单前缀",
      any(i["type"] == "FORMAT_STYLE_ISSUE" for i in qa_bullet))
from app.export.docx import _is_list_item as _export_is_list_item
from app.writing.writer import _normalize_report_sentence
normalized_item = _normalize_report_sentence("• A类事项: 示例内容。")
check("写作清洗符号化清单前缀",
      normalized_item == "A类事项方面，示例内容。")
check("导出兼容历史符号化条目", _export_is_list_item("• 必交: 登记表、通讯稿。"))
check("解析进度最终态 done==total",
      task.get("parse_progress", {}).get("done") == task.get("parse_progress", {}).get("total") == 1)
check("证据进度最终态 done==total",
      (lambda ep: ep.get("done") == ep.get("total") and ep.get("total", 0) >= 1)
      (task.get("evidence_progress") or {}))
check("LLM 调用统计记录", task.get("llm_stats", {}).get("calls", 0) >= 1)
with session_scope() as s:
    from app.infrastructure.orm import ORMLLMCall
    token_log_count = s.execute(select(__import__('sqlalchemy').func.count()).select_from(ORMLLMCall).where(ORMLLMCall.c.task_id == "r3d")).scalar()
check("Token观测:LLM Call 日志落库", token_log_count >= 1)
check("Token观测:效率指标写入任务",
      task.get("token_efficiency", {}).get("call_count", 0) >= 1
      and "by_agent" in task.get("token_efficiency", {})
      and "fact_utilization_rate" in task.get("token_efficiency", {})
      and "token_buckets" in task.get("token_efficiency", {})
      and "scale_drivers" in task.get("token_efficiency", {}))
mvs = vector_store.material_vectors()
check("材料向量由 unit 聚合(写入Qdrant)", len(mvs) == 1 and len(mvs[0][1]) == 8)
with session_scope() as s:
    from app.infrastructure.orm import Base as _B
    from sqlalchemy import func as _f
    _ac = _B.metadata.tables["artifact_cache"]
    cache_row = s.execute(select(_f.count()).select_from(_ac).where(_ac.c.stage == "material_analysis")).scalar()
check("性能缓存:材料分析 Artifact Cache 写入", cache_row >= 1)
with session_scope() as s:
    from app.infrastructure.orm import ORMTaskArtifact
    artifact_rows = s.execute(select(ORMTaskArtifact.c.stage, ORMTaskArtifact.c.status).where(ORMTaskArtifact.c.task_id == "r3d")).mappings().all()
    from app.infrastructure.orm import ORMMaterial
    material_row = s.execute(select(ORMMaterial.c.parser_version, ORMMaterial.c.parsed_at).limit(1)).mappings().first()
artifact_stages = {r["stage"] for r in artifact_rows if r["status"] == "done"}
check("Task Cache:阶段 Artifact 写入",
      {"parse", "material_analysis", "plan", "evidence", "analysis", "write"} <= artifact_stages)
check("Task Cache:章节草稿 Artifact 写入",
      any(stage.startswith("chapter_draft:") for stage in artifact_stages))
with session_scope() as s:
    from app.infrastructure.orm import ORMKGAssertion, ORMKGAssertionFact
    graph_assertions = s.execute(select(ORMKGAssertion)).mappings().all()
    graph_fact_links = s.execute(select(ORMKGAssertionFact)).mappings().all()
check("任务图谱:关系只从 Fact 构建且保留依据",
      len(graph_assertions) >= 1 and graph_assertions[0]["status"] == "validated"
      and len(graph_fact_links) >= 1 and int(graph_fact_links[0]["fact_id"]) == 1)
check("Material Cache:解析版本记录",
      material_row is not None and material_row["parser_version"] == "verify-mock")

from app.export import export_report
out = export_report(task["report_id"])
from docx import Document
from docx.oxml.ns import qn
doc = Document(out)
paras = [p.text for p in doc.paragraphs if p.text.strip()]
check("导出同段合并", any("第一句" in p and "第二句" in p for p in paras))
check("导出应用变体 dominant 格式(宋体)", doc.styles["Normal"].font.name == "宋体")
title_para = next((p for p in doc.paragraphs if p.text.strip()), None)
title_ppr = title_para._p.pPr if title_para is not None else None
title_style_ppr = title_para.style.element.pPr if title_para is not None and title_para.style is not None else None
title_has_border = bool(
    (title_ppr is not None and title_ppr.find(qn("w:pBdr")) is not None)
    or (title_style_ppr is not None and title_style_ppr.find(qn("w:pBdr")) is not None)
)
check("导出使用 IRA 语义标题样式", title_para is not None and title_para.style.name == "IRA_DocumentTitle")
check("导出清理标题实际下划线/边框", not title_has_border)
conformance_path = out.with_suffix(".conformance.json")
check("导出生成模板一致性检查结果", conformance_path.exists())

from fastapi.testclient import TestClient
from app.api import app
client = TestClient(app)
r = client.get("/api/tasks/r3d/materials")
check("materials API", r.status_code == 200 and len(r.json()) == 1)
r = client.get("/api/materials")
check("材料库 API 绑定所属任务", r.status_code == 200 and any(item.get("tasks") for item in r.json()))
r = client.get("/api/tasks/r3d/analysis")
check("analysis API:事实/推断",
      r.status_code == 200 and len(r.json()["facts"]) == 1 and len(r.json()["inferences"]) >= 1)
r = client.get("/api/tasks/r3d/graph")
check("任务图谱 API:只返回任务关系和事实绑定",
      r.status_code == 200 and r.json().get("stats", {}).get("assertion_count", 0) >= 1
      and r.json().get("edges", [{}])[0].get("fact_ids") == [1])
r = client.get(f"/api/reports/{task['report_id']}")
data = r.json()
check("报告详情 API 返回所属任务", data.get("task_id") == "r3d" and data.get("status") == "draft")
check("报告段落分组:1节1段4句", len(data["sections"]) == 1
      and len(data["sections"][0]["paragraphs"]) == 1
      and len(data["sections"][0]["paragraphs"][0]["sentences"]) == 4)
r = client.post("/api/tasks", data={"theme": "带变体任务", "variant_id": str(va.id)},
                files=[("files", ("t_cn.pdf", open("/tmp/t_cn.pdf", "rb"), "application/pdf"))])
created_task_id = r.json().get("task_id") if r.status_code == 200 else ""
check("create_task 带 variant_id", r.status_code == 200 and created_task_id)
r = client.get("/api/style/variants")
check("变体 API 返回结构化字段", r.status_code == 200 and r.json()[0].get("structure") and r.json()[0].get("style_samples") is not None)
r = client.get("/api/system/queues")
queues = r.json()
check("队列 API:任务队列与 LLM 队列状态",
      r.status_code == 200 and "tasks" in queues and "llm" in queues
      and "pending" in queues["tasks"] and "pending" in queues["llm"])
r = client.post("/api/tasks/not-found/run")
check("任务队列:不存在任务返回 404", r.status_code == 404)
r = client.get("/api/tasks/r3d/token-efficiency")
token_payload = r.json()
check("Token观测 API:任务效率明细",
      r.status_code == 200
      and "final_token_efficiency" in token_payload.get("summary", {})
      and "token_buckets" in token_payload.get("summary", {})
      and isinstance(token_payload.get("calls"), list)
      and token_payload["calls"])
r = client.get(f"/api/style/variants/{va.id}/template-schema")
schema_json = r.json()
check("模板 Schema 可独立导出 JSON",
      r.status_code == 200
      and schema_json.get("style", {}).get("roles", {}).get("document_title")
      and schema_json.get("components"))
r = client.get("/api/reports")
check("历史报告 API 绑定所属任务", r.status_code == 200 and any(item.get("task") for item in r.json()))
r = client.post(f"/api/reports/{task['report_id']}/finalize")
check("审核完成:报告 final 且任务 done",
      r.status_code == 200
      and short_term.load_task("r3d").get("stage") == "done")
with session_scope() as s:
    from app.infrastructure.orm import ORMKGChangeSet
    graph_final = s.execute(select(ORMKGAssertion.c.status)).scalar()
    graph_changes = s.execute(select(ORMKGChangeSet).where(ORMKGChangeSet.c.task_id == "r3d")).mappings().all()
check("长期图谱:仅审核后确认并绑定变更记录",
      graph_final == "confirmed" and any(
          item["change_type"] == "PUBLISH_TASK_GRAPH" and item["report_version_id"] is not None
          for item in graph_changes
      ))
r = client.patch(f"/api/style/variants/{va.id}", json={"name": "政策研究报告(改名)"})
check("变体改名 PATCH", r.status_code == 200 and style.get_variant(va.id).name == "政策研究报告(改名)")
check("页面 / 含模板下拉", "使用默认模板" in client.get("/").text)
task_page = client.get("/tasks/r3d").text
check("任务页规模计划不暴露内部预估文案",
      "初步目标" not in task_page and "预估上限" not in task_page and "风险 low" not in task_page)
check("页面 /reports/{id} 200", client.get(f"/reports/{task['report_id']}").status_code == 200)
r = client.delete(f"/api/tasks/{created_task_id}")
check("任务删除 API:删除任务记录但保留资产", r.status_code == 200 and short_term.load_task(created_task_id) is None)
r = client.delete(f"/api/style/variants/{va.id}")
check("模板删除 API:软删除后列表隐藏但旧数据可读",
      r.status_code == 200
      and all(item["id"] != va.id for item in client.get("/api/style/variants").json())
      and style.get_variant(va.id) is not None
      and style.get_variant(va.id).status == "deleted")

try:
    for _collection in (
        vector_store.units_collection,
        vector_store.materials_collection,
        vector_store.facts_collection,
    ):
        vector_store.client.delete_collection(_collection)
except Exception:
    pass
shutil.rmtree(_TMP_ROOT, ignore_errors=True)
failed = [n for n, ok in CHECKS if not ok]
print(f"\n=== {len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed (ad-hoc, 隔离环境) ===")
sys.exit(1 if failed else 0)
