"""Workflow Controller:单任务串行编排。

阶段:解析 → 去重 → 规划 → 材料理解 → 事实 → 冲突 → 分析 → 写作 → 在线评审 → 完成。
Agent 由控制器顺序驱动,阶段产物写入短期记忆(task payload)。
"""
import json
import re
import threading
import time

import numpy as np

from app.agents.analysis import AnalysisAgent
from app.agents.evidence import EvidenceAgent, load_evidence_quotes
from app.agents.planner import PlannerAgent
from app.agents.writer import WriterAgent, _chapter_position
from app.business import (
    append_business_issues,
    build_business_qa,
    build_chapter_evidence_matrix,
    build_task_profile,
    business_block,
    looks_like_template_meta,
)
from app.cache import get_cached, set_cached, stable_hash
from app.config import settings
from app.db import connect
from app.knowledge import KnowledgeAgent
from app.llm_queue import PRIORITY_BACKGROUND, llm_priority
from app.memory import short_term
from app.models import Stage, Unit
from app.parser import PARSER_VERSION, parse_file_with_profile
from app.policy import build_report_policy, policy_prompt_block
from app.retrieval import vector_store
from app.scale import (
    apply_scale_plan_to_report_plan,
    build_preliminary_scale_plan,
    build_report_scale_plan,
)
from app.task_artifacts import save_task_artifact
from app.token_monitor import build_token_efficiency, token_context

_MATERIAL_ANALYSIS_PROMPT = """你是材料分析师。理解一份情报材料,输出 JSON:
{
  "doc_type": "材料类型(政策文件/新闻报道/研究报告/通知公告/统计资料等)",
  "topic": "核心主题(一句话)",
  "key_points": ["核心观点或关键事实(2-4 条)"],
  "key_sections": ["重要章节或段落主题"],
  "entities": ["关键实体(机构/人物/地区/项目)"],
  "times": ["材料中出现的具体时间信息,如:2026年3月"],
  "material_role": "材料自身在信息集合中的角色(例如规范依据/事实记录/模板样例/参考资料等,根据材料内容命名)",
  "claim_support": "actual/normative/template/reference/unknown",
  "allowed_usage": ["仅依据材料自身内容能够稳定支撑的信息用途"],
  "forbidden_usage": ["仅依据材料自身内容不能支撑、容易误用的信息用途"],
  "missing_information": ["仅从材料自身边界可见的缺失信息"]
}"""

_MATERIAL_ANALYSIS_PROMPT_VERSION = "material-analysis-v4"
_MATERIAL_ROUTING_VERSION = "runtime-profile-v1"

_REWRITE_SYSTEM = """你是报告修订员。修正报告句子中与事实不符的数字,使其与给定事实一致。
严格输出 JSON:{"text": "修正后的句子"}
要求:只修正数字,保持原句意思与措辞,不新增任何事实或数字。"""

planner = PlannerAgent()
evidence_agent = EvidenceAgent()
analysis_agent = AnalysisAgent()
writer_agent = WriterAgent()
knowledge_agent = KnowledgeAgent()


class WorkflowController:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.task = short_term.load_task(task_id)
        self.cm = None
        if self.task is None:
            raise ValueError(f"TASK_NOT_FOUND: {task_id}")
        self.task.setdefault("task_id", task_id)
        self.task.setdefault("id", task_id)

    # ---------- 对外入口 ----------

    def run_to_review(self) -> None:
        """跑完 解析→去重→规划→事实→冲突→分析→写作,停在评审阶段等待用户。

        各阶段耗时记录到 task.stage_timings(自阶段起点累计秒数),便于定位热点。
        """
        import time as _time

        from app.agents.base import llm_stats, reset_llm_stats

        reset_llm_stats()  # 任务级 LLM 调用统计起点(调用次数 + 输入规模)
        _t0 = _time.time()
        _last = _t0
        _marks: dict[str, float] = {}
        _durations: dict[str, float] = {}

        def _mark(name: str) -> None:
            nonlocal _last
            now = _time.time()
            _marks[name] = round(_time.time() - _t0, 1)
            _durations[name] = round(now - _last, 1)
            _last = now
            self._update(stage_timings=_marks, stage_durations=_durations)

        with self._token_context("parse"):
            self.parse_materials()
        self._record_artifact("parse", {
            "material_ids": self.task.get("material_ids", []),
            "parser_version": PARSER_VERSION,
            "parse_errors": self.task.get("parse_errors", []),
            "embed_errors": self.task.get("embed_errors", []),
            "parse_stats": self.task.get("parse_stats", {}),
            "parse_results": self.task.get("parse_results", []),
        })
        _mark("parse")
        with self._token_context("dedup"):
            self.dedup()
        self._record_artifact("dedup", {"dedup_pairs": self.task.get("dedup_pairs", [])})
        _mark("dedup")
        with self._token_context("material_analysis"):
            self.analyze_materials()
        self._record_artifact("material_analysis", {
            "material_insights": self.task.get("material_insights", []),
            "reused": self.task.get("material_analysis_reused", 0),
            "cache_hits": self.task.get("material_analysis_cache_hits", 0),
        })
        _mark("material_analysis")
        if self.task.get("plan_id"):
            self._update(stage=str(Stage.PLANNING), resume={"stage": "plan", "status": "reused"})
        else:
            with self._token_context("planning"):
                self.plan()  # 一次规划:ReportPlan + ChapterPlan[](核心判断/叙事逻辑/每章规划)
        self._record_artifact("plan", {
            "plan_id": self.task.get("plan_id"),
            "plan_title": self.task.get("plan_title", ""),
            "preliminary_scale_plan": self.task.get("preliminary_scale_plan", {}),
            "analysis_plan_snapshot": self._safe_plan_snapshot(),
        })
        _mark("plan")
        if self.task.get("fact_ids"):
            # 断点续跑(维度级):evidence_progress.done 记录已完成维度数
            ep = self.task.get("evidence_progress") or {}
            done = int(ep.get("done") or 0)
            total = int(ep.get("total") or 0)
            if done >= total and total > 0:
                self._update(
                    stage=str(Stage.EVIDENCE),
                    evidence_progress={"done": done, "total": total, "status": "resumed"},
                )
                self._ensure_context_manager()
                facts = self._facts()
            else:
                self._update(stage=str(Stage.EVIDENCE), resume={"stage": "evidence", "status": "partial"})
                facts = self.extract_evidence(start_dimension=done)
        else:
            with self._token_context("evidence"):
                facts = self.extract_evidence()
        self._record_artifact("evidence", {
            "fact_ids": self.task.get("fact_ids", []),
            "fact_count": len(facts),
            "report_scale_plan": self.task.get("report_scale_plan", {}),
        })
        _mark("evidence")
        if self.task.get("conflict_ids"):
            self._update(stage=str(Stage.CONFLICT), resume={"stage": "conflict", "status": "reused"})
        else:
            with self._token_context("conflict"):
                self.detect_conflicts()
        self._record_artifact("conflict", {"conflict_ids": self.task.get("conflict_ids", [])})
        _mark("conflict")
        if self.task.get("analysis_done"):
            self._update(stage=str(Stage.ANALYSIS), resume={"stage": "analysis", "status": "reused"})
            self._update_business_coverage(self._facts(), self._inferences())
        else:
            if self.task.get("inference_ids"):
                # 中断的部分分析:删除本任务旧推断后重跑(幂等,避免部分/重复推断)
                self._delete_task_inferences()
                self._update(inference_ids=[], external_ids=[], analysis_global_meta={})
            with self._token_context("analysis"):
                self.analyze(facts)
        self._record_artifact("analysis", {
            "inference_ids": self.task.get("inference_ids", []),
            "external_ids": self.task.get("external_ids", []),
            "report_scale_plan": self.task.get("report_scale_plan", {}),
        })
        _mark("analysis")
        if self.task.get("final_plan_frozen"):
            self._update(stage=str(Stage.PLANNING), resume={"stage": "final_plan", "status": "reused"})
        else:
            with self._token_context("final_planning"):
                self.finalize_report_structure()
        self._record_artifact("final_plan", {
            "plan_id": self.task.get("plan_id"),
            "plan_title": self.task.get("plan_title", ""),
            "report_scale_plan": self.task.get("report_scale_plan", {}),
            "final_plan_frozen": self.task.get("final_plan_frozen", False),
            "final_plan_snapshot": self._safe_plan_snapshot(),
        })
        _mark("final_plan")
        if self.task.get("report_id") and self._report_has_all_chapters():
            self._update(stage=str(Stage.WRITING), write_progress={"status": "resumed"})
        else:
            with self._token_context("writing"):
                self.write()
        self._record_artifact("write", {
            "report_id": self.task.get("report_id"),
            "report_stats": self.task.get("report_stats", {}),
            "qa_notes": self.task.get("qa_notes", []),
            "business_qa": self.task.get("business_qa", {}),
        })
        _mark("write")
        ttfr = round(_time.time() - _t0, 1)
        token_efficiency = build_token_efficiency(self.task_id, self.task.get("report_id"))
        self._update(
            stage=str(Stage.REVIEW),
            stage_timings=_marks,
            stage_durations=_durations,
            llm_stats=llm_stats(),
            token_efficiency=token_efficiency,
            ttfr_seconds=ttfr,
            critical_path_done=True,
        )
        self._start_background_post_review(facts)

    def _update(self, **fields) -> None:
        """更新短期记忆并同步内存快照,保证后续阶段读到最新产物。"""
        version_fields = {
            "stage", "parse_progress", "material_analysis_progress", "evidence_progress",
            "write_progress", "material_ids", "material_insights", "fact_ids",
            "inference_ids", "external_ids", "conflict_ids", "report_id",
            "qa_notes", "business_coverage", "business_qa", "report_stats",
            "stage_timings", "stage_durations", "llm_stats", "token_efficiency", "error",
            "ttfr_seconds", "critical_path_done", "background_jobs", "knowledge_stats",
            "artifact_status", "queue_status", "preliminary_scale_plan", "report_scale_plan",
            "final_plan_frozen",
        }
        if set(fields) & version_fields:
            current_versions = dict(self.task.get("versions") or {})
            current_versions["task"] = int(current_versions.get("task", 0)) + 1
            if set(fields) & {"material_ids", "material_insights", "parse_progress"}:
                current_versions["materials"] = int(current_versions.get("materials", 0)) + 1
            if set(fields) & {"fact_ids", "inference_ids", "external_ids", "conflict_ids", "qa_notes", "business_coverage", "business_qa"}:
                current_versions["analysis"] = int(current_versions.get("analysis", 0)) + 1
            if set(fields) & {"plan_id", "plan_title", "final_plan_frozen"}:
                current_versions["plan"] = int(current_versions.get("plan", 0)) + 1
            if set(fields) & {"report_id", "report_stats", "write_progress"}:
                current_versions["report"] = int(current_versions.get("report", 0)) + 1
            fields["versions"] = current_versions
        short_term.update_task(self.task_id, **fields)
        self.task.update(fields)

    # ---------- 阶段执行 ----------

    def parse_materials(self) -> None:
        self._update(stage=str(Stage.PARSING))
        # 解析阶段不依赖 LLM:声明重资源阶段,按 GPU 策略(gpu_memory_tight)由
        # 统一调度器决定是否临时卸载推理模型(策略化,不写死 unload)。
        from app.llm_scheduler import heavy_stage
        with heavy_stage():
            self._parse_loop()

    def _parse_loop(self) -> None:
        parse_errors: list[dict] = []
        embed_errors: list[dict] = []
        parse_results: list[dict] = []
        material_ids = [int(i) for i in self.task.get("material_ids", [])]
        total = len(material_ids)
        reused = 0
        parse_started = time.time()
        self._update(parse_progress={"done": 0, "total": total})
        for index, material_id in enumerate(material_ids, start=1):
            item_started = time.time()
            material = self._fetchone("SELECT * FROM materials WHERE id=?", (material_id,))
            if material is None:
                continue
            # 复用:同材料已解析过(units 存在)→ 跳过解析与向量化(材料库二次任务直接引用)
            with connect() as conn:
                material_parser_version = conn.execute(
                    "SELECT parser_version FROM materials WHERE id=?", (material_id,)
                ).fetchone()["parser_version"]
                already = conn.execute(
                    "SELECT COUNT(*) c FROM units WHERE material_id=?", (material_id,)
                ).fetchone()["c"]
            if already > 0 and material_parser_version == PARSER_VERSION:
                reused += 1
                parse_results.append({
                    "material_id": int(material_id),
                    "filename": material["filename"],
                    "status": "reused",
                    "duration_seconds": 0.0,
                    "unit_count": int(already),
                    "parser": material_parser_version,
                })
                self._update(parse_progress={"done": index, "total": total})
                continue
            try:
                units, parse_profile = parse_file_with_profile(material["path"])
            except Exception as exc:
                # 单个材料解析失败(如不支持的格式)不中断任务,记录后跳过
                duration = round(time.time() - item_started, 2)
                parse_errors.append({
                    "material_id": material_id,
                    "filename": material["filename"],
                    "error": str(exc),
                    "duration_seconds": duration,
                })
                parse_results.append({
                    "material_id": int(material_id),
                    "filename": material["filename"],
                    "status": "failed",
                    "duration_seconds": duration,
                    "unit_count": 0,
                    "parser": PARSER_VERSION,
                    "error": str(exc),
                })
                self._save_parse_profile(material_id, material, {}, "failed", str(exc), duration, 0)
                self._update(parse_progress={"done": index, "total": total})
                continue
            unit_ids: list[int] = []
            # 每份材料一个事务:批量写 Units(快,无网络调用;向量在事务外异步补)
            with connect() as conn:
                conn.execute("DELETE FROM units WHERE material_id=?", (material_id,))
                for unit in units:
                    unit.material_id = int(material_id)
                    cur = conn.execute(
                        "INSERT INTO units(material_id, kind, content, page, paragraph, image_desc, metadata_json) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?)",
                        (
                            unit.material_id, unit.kind, unit.content, unit.page,
                            unit.paragraph, unit.image_desc, unit.metadata_json,
                        ),
                    )
                    unit_ids.append(cur.lastrowid)
                conn.execute(
                    "UPDATE materials SET parser_version=?, parsed_at=datetime('now') WHERE id=?",
                    (parse_profile.get("parser") or PARSER_VERSION, int(material_id)),
                )
            # 解析完成先更新进度(embedding 是慢速可降级环节,不冻结进度,不持锁)
            self._update(parse_progress={"done": index, "total": total})
            # 事务外:embedding 分批 + 失败降级(单批失败即跳过整份向量化,不阻塞)
            embed_error = self._embed_units(int(material_id), unit_ids, units)
            if embed_error:
                embed_errors.append({
                    "material_id": int(material_id),
                    "filename": material["filename"],
                    "error": f"embed: {embed_error}",
                })
            duration = round(time.time() - item_started, 2)
            parse_status = "partial" if embed_error else "success"
            parse_results.append({
                "material_id": int(material_id),
                "filename": material["filename"],
                "status": parse_status,
                "duration_seconds": duration,
                "unit_count": len(units),
                "parser": parse_profile.get("parser") or PARSER_VERSION,
                "page_count": int(parse_profile.get("page_count") or 0),
                "text_count": int(parse_profile.get("text_count") or 0),
                "table_count": int(parse_profile.get("table_count") or 0),
                "image_count": int(parse_profile.get("image_count") or 0),
                "ocr_enabled": bool(parse_profile.get("ocr_enabled")),
                "ocr_route": parse_profile.get("ocr_route", ""),
                "embed_status": "failed" if embed_error else "success",
                "embed_error": embed_error,
            })
            self._save_parse_profile(
                material_id, material, parse_profile, parse_status,
                f"embed: {embed_error}" if embed_error else "", duration, len(units),
            )
            self._update(parse_progress={"done": index, "total": total})
        stats = _parse_stats(parse_results, parse_errors, embed_errors, round(time.time() - parse_started, 2))
        self._update(
            parsed=True,
            parse_errors=parse_errors,
            embed_errors=embed_errors,
            parse_results=parse_results,
            parse_stats=stats,
            parse_reused=reused,
            parser_version=PARSER_VERSION,
        )

    def _embed_units(self, material_id: int, unit_ids: list[int], units) -> str:
        """事务外 embedding:自适应分批(token 预算 + 吞吐反馈)+ 失败降级。

        返回错误信息(空串=成功)。向量缺失的材料由检索层词法回退,不阻塞任务。
        """
        pairs = [(uid, u) for uid, u in zip(unit_ids, units) if (u.content or "").strip()]
        if not pairs:
            return ""
        from app.retrieval.embedder import embed_texts_adaptive
        vectors, errors = embed_texts_adaptive([u.content for _, u in pairs])
        unit_vecs: list[np.ndarray] = []
        for (uid, _unit), vector in zip(pairs, vectors):
            if vector is None:
                continue
            try:
                vector_store.save_unit_vector(uid, vector)
                unit_vecs.append(np.asarray(vector, dtype=np.float32))
            except Exception:
                pass
        if errors:
            return "; ".join(errors[:3])
        if unit_vecs:
            try:
                material_vector = np.mean(unit_vecs, axis=0)
                vector_store.save_material_vector(material_id, material_vector.tolist())
            except Exception:
                pass
        return ""

    @staticmethod
    def _save_parse_profile(material_id: int, material, profile: dict, status: str,
                            error: str, duration: float, unit_count: int) -> None:
        """Persist material-level parser telemetry for UI and diagnostics."""
        try:
            structure = dict(profile or {})
            structure["duration_seconds"] = duration
            structure["unit_count"] = int(unit_count)
            with connect() as conn:
                row = conn.execute(
                    "SELECT id FROM file_nodes WHERE material_id=? ORDER BY id DESC LIMIT 1",
                    (int(material_id),),
                ).fetchone()
                node_id = int(row["id"]) if row else 0
                conn.execute(
                    "INSERT INTO file_parse_profiles(node_id, material_id, parser, status, page_count, "
                    "text_count, table_count, image_count, markdown_chars, structure_json, error, parsed_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                    (
                        node_id,
                        int(material_id),
                        str(profile.get("parser") or PARSER_VERSION),
                        status,
                        int(profile.get("page_count") or 0),
                        int(profile.get("text_count") or 0),
                        int(profile.get("table_count") or 0),
                        int(profile.get("image_count") or 0),
                        int(profile.get("markdown_chars") or 0),
                        json.dumps(structure, ensure_ascii=False),
                        error,
                    ),
                )
        except Exception:
            pass

    def dedup(self) -> None:
        self._update(stage=str(Stage.DEDUP))
        pairs = vector_store.dedup_materials()
        for dup_id, main_id in pairs:
            with connect() as conn:
                conn.execute(
                    "UPDATE materials SET is_duplicate=1, duplicate_of=? WHERE id=?",
                    (main_id, dup_id),
                )
        self._update(dedup_pairs=pairs)

    def plan(self) -> None:
        self._update(stage=str(Stage.PLANNING))
        from app.context import ContextManager

        profile = self.task.get("task_profile") or {}
        preliminary_scale = build_preliminary_scale_plan(
            self.task.get("theme", ""),
            self.task.get("user_requirements", ""),
            profile,
        )
        self._update(preliminary_scale_plan=preliminary_scale)
        cm = ContextManager(self.task)
        variant = self._selected_variant()
        policy = build_report_policy(self.task, variant, profile)
        self._update(report_policy=policy)
        context_block = cm.for_planner(
            self.task.get("theme", ""),
            self.task.get("user_requirements", ""),
            self.task.get("material_insights", []),
            variant.to_prompt_block() if variant else self._style_block(),
            business_block(profile),
            policy_prompt_block(policy),
        )
        try:
            plan = planner.plan(context_block, user_requirements=self.task.get("user_requirements", ""))
        except Exception:
            raise
        self._update(plan_id=plan.id, plan_title=plan.title)

    def finalize_report_structure(self) -> None:
        """Freeze final chapters after Evidence + Analysis, not before."""
        self._update(stage=str(Stage.PLANNING))
        from app.context import ContextManager

        plan = self._plan()
        facts = self._facts()
        inferences = self._inferences()
        variant = self._selected_variant()
        profile = self.task.get("task_profile") or {}
        policy = self.task.get("report_policy") or build_report_policy(self.task, variant, profile)
        cm = ContextManager(self.task)
        context_block = cm.for_final_planner(
            self.task.get("theme", ""),
            self.task.get("user_requirements", ""),
            plan,
            facts,
            inferences,
            self.task.get("material_insights", []),
            variant.to_prompt_block() if variant else self._style_block(),
            policy_prompt_block(policy),
        )
        final_plan = planner.finalize_report_plan(int(plan["id"]), context_block)
        self._update(plan_title=final_plan.title, final_plan_frozen=True)
        self._update_scale_plan(self._facts(), self._inferences())
        self._update_business_coverage(self._facts(), self._inferences())

    def analyze_materials(self) -> None:
        """材料理解层:识别每份材料的类型/主题/重要章节/关键实体/时间/价值排序。

        结果存 material_insights,并引导 Evidence 提取(高价值材料优先、按主题聚焦)。
        """
        self._update(stage=str(Stage.MATERIAL_ANALYSIS))
        from app.agents.base import BaseAgent
        from app.db import connect as db_connect

        units_by_material, filenames = self._load_units()
        if not units_by_material:
            return
        analyzer = BaseAgent()
        analyzer.name = "material_analyzer"
        analyzer.role = _MATERIAL_ANALYSIS_PROMPT
        insights: list[dict] = []
        material_ids = [int(i) for i in self.task.get("material_ids", [])]
        total = len(material_ids)
        # 复用:同材料已分析过(material_insights 有记录)→ 跳过 LLM,直接引用
        existing: dict[int, dict] = {}
        try:
            with db_connect() as conn:
                for r in conn.execute(
                    "SELECT material_id, doc_type, topic, key_sections, key_points, entities, times, "
                    "material_role, claim_support, allowed_usage, forbidden_usage, missing_information "
                    "FROM material_insights WHERE task_id=?",
                    (self.task_id,),
                ).fetchall():
                    existing[r["material_id"]] = {
                        "material_id": r["material_id"],
                        "filename": filenames.get(r["material_id"], ""),
                        "doc_type": r["doc_type"], "topic": r["topic"],
                        "key_points": json.loads(r["key_points"] or "[]"),
                        "key_sections": json.loads(r["key_sections"] or "[]"),
                        "entities": json.loads(r["entities"] or "[]"),
                        "times": json.loads(r["times"] or "[]"),
                        "material_role": r["material_role"],
                        "claim_support": r["claim_support"],
                        "allowed_usage": json.loads(r["allowed_usage"] or "[]"),
                        "forbidden_usage": json.loads(r["forbidden_usage"] or "[]"),
                        "missing_information": json.loads(r["missing_information"] or "[]"),
                    }
        except Exception:
            existing = {}
        reused = 0
        cache_hits = 0
        for index, material_id in enumerate(material_ids, start=1):
            self._update(material_analysis_progress={"done": index - 1, "total": total})
            if material_id in existing:
                insights.append(existing[material_id])
                reused += 1
                self._update(material_analysis_progress={"done": index, "total": total})
                continue
            units = units_by_material.get(material_id, [])
            text, text_meta = _material_understanding_text(
                units, budget_chars=4000, theme=self.task.get("theme") or "")
            if not text:
                continue
            effective_inputs = {
                "material_id": material_id,
                "filename": filenames.get(material_id, ""),
                "text_hash": stable_hash("\n".join(u.content for u in units if u.content.strip())),
                "prompt_scope": "material_only",
            }
            cached = get_cached(
                "material_analysis", effective_inputs,
                model_version=settings.generation_model,
                prompt_version=_MATERIAL_ANALYSIS_PROMPT_VERSION,
                config_version=_MATERIAL_ROUTING_VERSION,
            )
            if isinstance(cached, dict):
                insight = dict(cached)
                insight["material_id"] = material_id
                insight["filename"] = filenames.get(material_id, "")
                insight["analysis_route"] = "cache"
                insight["context_meta"] = text_meta
                insights.append(insight)
                cache_hits += 1
                self._update(material_analysis_progress={"done": index, "total": total})
                continue
            historical = _load_historical_material_insight(material_id, filenames.get(material_id, ""))
            if historical:
                historical["analysis_route"] = "material_insight_reuse"
                historical["context_meta"] = text_meta
                set_cached(
                    "material_analysis", effective_inputs, historical,
                    model_version=settings.generation_model,
                    prompt_version=_MATERIAL_ANALYSIS_PROMPT_VERSION,
                    config_version=_MATERIAL_ROUTING_VERSION,
                )
                insights.append(historical)
                cache_hits += 1
                self._update(material_analysis_progress={"done": index, "total": total})
                continue
            try:
                payload = analyzer.generate_json(f"材料文本:\n{text}")
            except Exception:
                continue
            insight = {
                "material_id": material_id,
                "filename": filenames.get(material_id, ""),
                "doc_type": str(payload.get("doc_type", "")),
                "topic": str(payload.get("topic", "")),
                "key_points": [str(s) for s in payload.get("key_points", [])],
                "key_sections": [str(s) for s in payload.get("key_sections", [])],
                "entities": [str(s) for s in payload.get("entities", [])],
                "times": [str(s) for s in payload.get("times", [])],
                "material_role": str(payload.get("material_role", "")),
                "claim_support": str(payload.get("claim_support", "unknown")),
                "allowed_usage": [str(s) for s in payload.get("allowed_usage", [])],
                "forbidden_usage": [str(s) for s in payload.get("forbidden_usage", [])],
                "missing_information": [str(s) for s in payload.get("missing_information", [])],
                "context_meta": text_meta,
            }
            set_cached(
                "material_analysis", effective_inputs, insight,
                model_version=settings.generation_model,
                prompt_version=_MATERIAL_ANALYSIS_PROMPT_VERSION,
                config_version=_MATERIAL_ROUTING_VERSION,
            )
            insights.append(insight)
            self._update(material_analysis_progress={"done": index, "total": total})
        # 价值排序:材料篇幅(内容量)粗略排序,供提取引导
        insights.sort(key=lambda i: sum(
            len(u.content) for u in units_by_material.get(i["material_id"], [])
        ), reverse=True)
        for rank, insight in enumerate(insights, start=1):
            insight["value_rank"] = rank
            insight["filename"] = insight.get("filename") or filenames.get(int(insight["material_id"]), "")
            if insight["material_id"] in existing:
                continue  # 复用材料:已落库,不重复插入
            with db_connect() as conn:
                conn.execute(
                    "INSERT INTO material_insights(material_id, doc_type, topic, key_sections, "
                    "key_points, entities, times, value_rank, material_role, claim_support, "
                    "allowed_usage, forbidden_usage, missing_information, task_id, analysis_version) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (insight["material_id"], insight["doc_type"], insight["topic"],
                     json.dumps(insight["key_sections"], ensure_ascii=False),
                     json.dumps(insight.get("key_points", []), ensure_ascii=False),
                     json.dumps(insight["entities"], ensure_ascii=False),
                     json.dumps(insight["times"], ensure_ascii=False),
                     insight["value_rank"],
                     insight.get("material_role", ""),
                     insight.get("claim_support", "unknown"),
                     json.dumps(insight.get("allowed_usage", []), ensure_ascii=False),
                     json.dumps(insight.get("forbidden_usage", []), ensure_ascii=False),
                     json.dumps(insight.get("missing_information", []), ensure_ascii=False),
                     self.task_id, _MATERIAL_ANALYSIS_PROMPT_VERSION),
                )
        profile = build_task_profile(self.task.get("theme", ""), insights)
        self._update(
            material_insights=profile.get("enriched_insights") or insights,
            material_analysis_reused=reused,
            material_analysis_cache_hits=cache_hits,
            task_profile=profile,
        )

    def extract_evidence(self, start_dimension: int = 0) -> list[dict]:
        self._update(stage=str(Stage.EVIDENCE))
        from app.context import ContextManager

        plan = self._plan()
        units_by_material, filenames = self._load_units()
        insights = self.task.get("material_insights", [])
        self.cm = ContextManager(self.task, units_by_material, filenames)
        facts = evidence_agent.extract_facts(
            plan["dimensions"], units_by_material, filenames,
            cm=self.cm, insights=insights,
            required_facts=plan.get("required_facts", []),
            task_id=self.task_id,
            start_dimension=start_dimension,
            progress_callback=lambda done, total: self._update(evidence_progress={"done": done, "total": total}),
        )
        # 断点续跑:fact_ids 保留已完成维度 + 追加新增(不覆盖丢失)
        existing_ids = [int(x) for x in (self.task.get("fact_ids") or [])]
        new_ids = [int(f.id) for f in facts if f.id is not None]
        self._update(fact_ids=list(dict.fromkeys(existing_ids + new_ids)))
        fact_payload = [{"id": f.id, "content": f.content, "sources": load_evidence_quotes(f.id)} for f in facts]
        self._update_business_coverage(self._facts(), [])
        self._update_scale_plan(self._facts(), [])
        return fact_payload

    def detect_conflicts(self) -> None:
        self._update(stage=str(Stage.CONFLICT))
        from app.agents.evidence import load_claims

        claims = load_claims(self.task_id)
        conflicts = evidence_agent.detect_conflicts(claims, task_id=self.task_id)
        self._update(conflict_ids=[c.id for c in conflicts])

    def analyze(self, facts: list[dict]) -> None:
        self._update(stage=str(Stage.ANALYSIS))
        if not facts:
            return
        conflicts = self._conflicts()
        memory_block = self._knowledge_block()
        timeline_block = self._timeline_block()
        # Map:按维度分组 → 每组局部分析(不再单次全量分析,可随事实规模扩展)
        groups: dict[str, list[dict]] = {}
        for fact in facts:
            groups.setdefault(str(fact.get("dimension") or "未分类"), []).append(fact)
        local_inferences: list = []
        for dimension, group_facts in groups.items():
            context_block = self.cm.for_analysis(group_facts, conflicts, timeline_block, memory_block) if self.cm else ""
            local_inferences.extend(analysis_agent.analyze_local(dimension, group_facts, context_block))
        # Reduce:跨维度综合(局部推断 → 全局推断 + 覆盖状态元数据)
        global_inferences, external, global_meta = analysis_agent.analyze_global(
            local_inferences, facts,
            "、".join(str(c.get("description") or c.get("note") or "") for c in (conflicts or [])[:5]),
        )
        inferences = local_inferences + global_inferences
        self._update(
            inference_ids=[i.id for i in inferences],
            external_ids=[i.id for i in external],
            analysis_global_meta=global_meta,
        )
        self._update_business_coverage(self._facts(), self._inferences())
        self._update_scale_plan(self._facts(), self._inferences())
        self._update_fact_dispositions()
        self._update_coverage_audit()
        added = self._gap_filling_pass()
        if added > 0:
            self._update_fact_dispositions()
            self._update_coverage_audit()
        self._update(analysis_done=True)

    def _delete_task_inferences(self) -> None:
        """删除本任务全部推断(分析中断后重跑前的幂等清理)。"""
        try:
            with connect() as conn:
                inf_ids = [r["id"] for r in conn.execute(
                    "SELECT id FROM inferences WHERE origin_call_id IN "
                    "(SELECT id FROM llm_call_logs WHERE task_id=?)",
                    (self.task_id,),
                ).fetchall()]
                if inf_ids:
                    placeholders = ",".join("?" * len(inf_ids))
                    conn.execute(
                        f"DELETE FROM inference_fact WHERE inference_id IN ({placeholders})", inf_ids)
                    conn.execute(
                        f"DELETE FROM inferences WHERE id IN ({placeholders})", inf_ids)
        except Exception:
            pass

    def _gap_filling_pass(self) -> int:
        """缺口驱动补抽(规格 6):维度无 Fact 或 required_facts 未覆盖时,
        用缺口维度定向补抽一轮(材料全覆盖检索)。停止条件:无新增 Fact 即停。"""
        try:
            self.task = short_term.load_task(self.task_id) or self.task
            audit = self.task.get("coverage_audit") or {}
            dim_gaps = [d for d, info in (audit.get("dimension") or {}).items()
                        if int(info.get("fact_count") or 0) == 0]
            req_gaps = [t.get("term") for t in (audit.get("requirement") or [])
                        if int(t.get("covered_facts") or 0) == 0 and t.get("term")]
            gaps = dim_gaps + req_gaps
            if not gaps:
                return 0
            units_by_material, filenames = self._load_units()
            from app.agents.evidence import EvidenceAgent

            agent = EvidenceAgent()
            new_facts = agent.extract_facts(
                dimensions=gaps, cm=self.cm,
                units_by_material=units_by_material, filenames=filenames,
                task_id=self.task_id, progress_callback=None,
            )
            if not new_facts:
                return 0
            # 增量局部分析:新增 Fact 按维度分析,追加推断(不重跑全局综合,避免重复)
            new_ids = {int(f.id) for f in new_facts}
            new_inference_ids = list(self.task.get("inference_ids") or [])
            groups: dict[str, list[dict]] = {}
            for fact in self._facts():
                if int(fact["id"]) in new_ids:
                    groups.setdefault(str(fact.get("dimension") or "未分类"), []).append(fact)
            for dimension, group_facts in groups.items():
                context_block = self.cm.for_analysis(group_facts, [], "", "") if self.cm else ""
                infs = analysis_agent.analyze_local(dimension, group_facts, context_block)
                new_inference_ids.extend(i.id for i in infs)
            self._update(inference_ids=new_inference_ids)
            return len(new_facts)
        except Exception:
            return 0

    def _update_fact_dispositions(self) -> None:
        """Fact 去向标记(规格:每条有效 Fact 必须有明确去向)。"""
        try:
            from app.retrieval.rag import _ngram_similarity

            facts = self._facts()
            inferences = self._inferences()
            used_ids = {int(fid) for inf in inferences for fid in (inf.get("based_fact_ids") or [])}
            theme = self.task.get("theme") or ""
            with connect() as conn:
                for fact in facts:
                    fid = int(fact["id"])
                    if fid in used_ids:
                        disp = "USED_IN_ANALYSIS"
                    elif (fact.get("conflict_ids") or "") not in ("", "[]"):
                        disp = "CONFLICT_UNRESOLVED"
                    elif theme and _ngram_similarity(theme, fact.get("content") or "") < 0.05:
                        disp = "LOW_RELEVANCE"
                    else:
                        disp = "BACKGROUND"
                    conn.execute("UPDATE facts SET disposition=? WHERE id=?", (disp, fid))
        except Exception:
            pass

    def _update_coverage_audit(self) -> None:
        """三类覆盖可观测性:材料覆盖 / 维度覆盖 / 需求覆盖(规格 5/9)。"""
        try:
            # 材料扫描状态读独立表(evidence 阶段写入,避免 task payload 膨胀)
            with connect() as conn:
                scan_rows = conn.execute(
                    "SELECT material_id, scanned, units_selected, fact_count "
                    "FROM material_scan WHERE task_id=?",
                    (self.task_id,),
                ).fetchall()
            merged: dict[int, dict] = {}
            for row in scan_rows:
                m = merged.setdefault(int(row["material_id"]),
                                      {"scanned": False, "units_selected": 0, "fact_count": 0})
                m["scanned"] = m["scanned"] or bool(row["scanned"])
                m["units_selected"] = max(m["units_selected"], int(row["units_selected"] or 0))
                m["fact_count"] += int(row["fact_count"] or 0)
            facts = self._facts()
            dim_facts: dict[str, dict] = {}
            for fact in facts:
                dim = str(fact.get("dimension") or "未分类")
                d = dim_facts.setdefault(dim, {"fact_count": 0, "source_material_count": 0})
                d["fact_count"] += 1
            # 独立来源材料数(evidence 按 fact 关联)
            with connect() as conn:
                fact_ids = [int(f["id"]) for f in facts if f.get("id") is not None]
                if fact_ids:
                    ev_rows = conn.execute(
                        "SELECT fact_id, COUNT(DISTINCT material_id) c FROM evidence "
                        "WHERE fact_id IN (%s) GROUP BY fact_id" % ",".join("?" * len(fact_ids)),
                        fact_ids,
                    ).fetchall()
                    sources_by_fact = {r["fact_id"]: int(r["c"]) for r in ev_rows}
                else:
                    sources_by_fact = {}
            for dim, d in dim_facts.items():
                ids = [int(f["id"]) for f in facts if str(f.get("dimension") or "未分类") == dim]
                d["source_material_count"] = sum(sources_by_fact.get(fid, 0) for fid in ids)
            required = []
            plan = self._plan()
            if plan:
                required = plan.get("required_facts") or []
            req_status: list[dict] = []
            for term in required or []:
                hit = sum(1 for f in facts if term and term in (f.get("content") or ""))
                req_status.append({"term": str(term)[:50], "covered_facts": hit})
            audit = {
                "material": {
                    "total": len(self.task.get("material_ids") or []),
                    "scanned": sum(1 for m in merged.values() if m["scanned"]),
                    "with_facts": sum(1 for m in merged.values() if m["fact_count"] > 0),
                    "fact_count": sum(m["fact_count"] for m in merged.values()),
                },
                "dimension": {dim: {"fact_count": d["fact_count"], "source_material_count": d["source_material_count"]}
                              for dim, d in dim_facts.items()},
                "requirement": req_status,
                "analysis": self.task.get("analysis_global_meta") or {},
            }
            self._update(coverage_audit=audit)
        except Exception:
            pass

    def _update_scale_plan(self, facts: list[dict], inferences: list[dict]) -> None:
        try:
            plan = self._plan()
            scale_plan = build_report_scale_plan(
                plan,
                facts,
                inferences,
                self.task.get("task_profile") or {},
                self.task.get("preliminary_scale_plan") or {},
            )
            apply_scale_plan_to_report_plan(int(plan["id"]), scale_plan)
            self._update(report_scale_plan=scale_plan)
        except Exception:
            return

    def _conflicts(self) -> list[dict]:
        result = []
        for conflict_id in self.task.get("conflict_ids", []):
            row = self._fetchone("SELECT * FROM conflicts WHERE id=?", (int(conflict_id),))
            if row is not None:
                result.append({"id": row["id"], "fact_key": row["fact_key"], "entries": row["entries"]})
        return result

    def _knowledge_block(self) -> str:
        """历史知识参考:已沉淀实体/事件(供推断参考,轻量)。"""
        from app.knowledge import load_entity_names

        try:
            names = load_entity_names(self.task_id)
            if names:
                return "历史实体: " + "、".join(names[:20])
        except Exception:
            pass
        return ""

    def write(self) -> None:
        self._update(stage=str(Stage.WRITING), write_progress={
            "done": 0,
            "total": 0,
            "chapter": "",
            "status": "starting",
            "elapsed_seconds": 0,
        })
        plan = self._plan()
        facts = self._facts()
        inferences = self._inferences()
        variant = self._selected_variant()
        style_block = variant.to_prompt_block() if variant else ""
        timeline_block = self._timeline_block()
        if timeline_block:
            style_block = f"{style_block}\n\n事件时间线(供时间线章节引用):\n{timeline_block}"
        chapters = plan.get("chapter_plans") or [{"title": t} for t in (plan.get("structure") or [])]
        self._update(write_progress={
            "done": 0,
            "total": len(chapters),
            "chapter": str(chapters[0].get("title", "")) if chapters else "",
            "status": "starting",
            "elapsed_seconds": 0,
        })
        report = writer_agent.write(
            plan, facts, inferences,
            style_block,
            variant.id if variant else None,
            cm=self.cm,
            institution_rules=self._effective_institution_rules(variant),
            task_profile=self.task.get("task_profile") or {},
            business_coverage=self.task.get("business_coverage") or {},
            report_policy=self.task.get("report_policy") or {},
            progress_callback=lambda done, total, chapter, status, elapsed: self._update(write_progress={
                "done": done,
                "total": total,
                "chapter": chapter,
                "status": status,
                "elapsed_seconds": elapsed,
            }),
            existing_report_id=self.task.get("report_id"),
            report_callback=lambda report: self._update(report_id=report.id),
            chapter_callback=self._record_chapter_artifact,
            task_id=self.task_id,
        )
        self._update(report_id=report.id)
        # 验证闭环:自动修订(无依据删除/数字重写/缺失章节补写),剩余问题供人工
        self._auto_revision(report.id, plan, facts, inferences, variant)
        self._normalize_report_order(report.id, plan)
        # 规模控制:超 hard_max 局部压缩(删重复/过渡句,不硬截断)+ 规模统计
        self._apply_budget_control(report.id, plan, facts, inferences)
        self._run_quality_check(variant, plan, report.id)
        if self._auto_quality_fix(report.id, plan):
            self._normalize_report_order(report.id, plan)
            self._apply_budget_control(report.id, plan, facts, inferences)
            self._run_quality_check(variant, plan, report.id)
        self._update_business_qa(report.id, plan, facts, inferences)

    def _record_chapter_artifact(self, payload: dict) -> None:
        chapter = str(payload.get("chapter", ""))
        self._record_artifact(
            f"chapter_draft:{payload.get('chapter_index', '')}:{chapter}",
            payload,
        )

    def _effective_institution_rules(self, variant) -> dict:
        """Keep learned style rules without forcing incompatible historical-report constraints."""
        if not variant:
            return {}
        rules = dict(variant.institution_rules or {})
        profile = self.task.get("task_profile") or {}
        if profile.get("report_mode") == "requirement_summary":
            rules.pop("must_include", None)
            rules.pop("word_count", None)
            rules.pop("inference_ratio", None)
        return rules

    def _update_business_coverage(self, facts: list[dict], inferences: list[dict]) -> None:
        profile = self.task.get("task_profile") or {}
        if not profile:
            return
        try:
            coverage = build_chapter_evidence_matrix(self._plan(), profile, facts, inferences)
        except Exception:
            return
        self._update(business_coverage=coverage)

    def _update_business_qa(self, report_id: int, plan: dict, facts: list[dict], inferences: list[dict]) -> None:
        profile = self.task.get("task_profile") or {}
        if not profile:
            return
        try:
            qa = build_business_qa(report_id, plan, profile, facts, inferences)
        except Exception:
            return
        self._update(business_qa=qa)

    def _apply_budget_control(self, report_id: int, plan: dict,
                              facts: list[dict], inferences: list[dict]) -> None:
        """报告规模控制:依据 ReportBudget 压缩超限内容并记录规模统计。

        超 hard_max_words 时优先删除:重复句(同节高相似)/多余过渡句;
        绝不硬截断正文、不删有依据句。统计写入 task.report_stats 供规模观测。
        """
        budget = plan.get("budget") or {}
        hard_max = int(budget.get("hard_max_words") or 0)
        with connect() as conn:
            rows = conn.execute(
                "SELECT id, section, content, source_refs, source_level FROM report_sentences "
                "WHERE report_id=? ORDER BY position", (report_id,),
            ).fetchall()
        actual = sum(_report_word_estimate(r["content"]) for r in rows)
        chapter_actuals: dict[str, int] = {}
        for r in rows:
            chapter_actuals[r["section"]] = chapter_actuals.get(r["section"], 0) + _report_word_estimate(r["content"])
        compressed = 0
        if hard_max and actual > hard_max:
            compressed = self._compress_report(rows, hard_max)
            with connect() as conn:
                rows = conn.execute(
                    "SELECT id, section, content, source_refs, source_level FROM report_sentences "
                    "WHERE report_id=? ORDER BY position", (report_id,),
                ).fetchall()
            actual = sum(_report_word_estimate(r["content"]) for r in rows)
            chapter_actuals = {}
            for r in rows:
                chapter_actuals[r["section"]] = chapter_actuals.get(r["section"], 0) + _report_word_estimate(r["content"])
        chapters = plan.get("chapter_plans") or []
        chapter_targets = {str(c.get("title", "")): int(c.get("target_words") or 0) for c in chapters}
        chapter_fact_counts: dict[str, int] = {}
        chapter_inference_counts: dict[str, int] = {}
        for r in rows:
            try:
                refs = json.loads(r["source_refs"] or "{}")
            except (TypeError, ValueError):
                refs = {}
            chapter_fact_counts[r["section"]] = chapter_fact_counts.get(r["section"], 0) + len(refs.get("fact_ids") or [])
            chapter_inference_counts[r["section"]] = chapter_inference_counts.get(r["section"], 0) + len(refs.get("inference_ids") or [])
        underfilled = []
        for title, target in chapter_targets.items():
            actual_chapter = int(chapter_actuals.get(title, 0))
            if target <= 0 or actual_chapter >= int(target * 0.72):
                continue
            support_count = chapter_fact_counts.get(title, 0) + chapter_inference_counts.get(title, 0)
            if support_count <= 2:
                reason = "evidence_severe_shortage"
            elif support_count <= 5:
                reason = "evidence_limited"
            else:
                reason = "writer_underexpanded"
            underfilled.append({
                "section": title,
                "target_words": target,
                "actual_words": actual_chapter,
                "support_items": support_count,
                "underfill_reason": reason,
            })
        self._update(report_stats={
            "target_words": budget.get("target_words", 0),
            "soft_max_words": budget.get("soft_max_words", 0),
            "hard_max_words": hard_max,
            "budget_authority": budget.get("budget_authority", ""),
            "budget_freeze_stage": budget.get("budget_freeze_stage", ""),
            "actual_words": actual,
            "chapter_targets": chapter_targets,
            "chapter_actuals": chapter_actuals,
            "underfilled_chapters": underfilled,
            "fact_count": len(facts),
            "inference_count": len(inferences),
            "facts_per_1000": round(len(facts) / max(actual, 1) * 1000, 1),
            "compress_count": compressed,
        })

    def _compress_report(self, rows, hard_max: int) -> int:
        """局部压缩:删同节高重复句与多余过渡句,直到 <= hard_max。

        返回删除句数;不删有事实/推断依据的句子,不做硬截断。
        """
        from app.quality import _cosine_similarity

        total = sum(len(r["content"]) for r in rows)
        removed = 0
        # 候选:过渡句(除每节第 1 句)优先,其次同节与前一语句高度相似句
        candidates: list[tuple[int, dict]] = []
        transition_seen: set[str] = set()
        for i, r in enumerate(rows):
            if r["source_level"] == "TRANSITION":
                if r["section"] in transition_seen:
                    candidates.append((1, r))
                else:
                    transition_seen.add(r["section"])
                continue
            try:
                refs = json.loads(r["source_refs"] or "{}")
            except (TypeError, ValueError):
                refs = {}
            if refs.get("fact_ids") or refs.get("inference_ids"):
                continue  # 有依据句不删
            if i > 0 and rows[i - 1]["section"] == r["section"]:
                if _cosine_similarity(r["content"], rows[i - 1]["content"]) > 0.7:
                    candidates.append((2, r))
        for _priority, r in sorted(candidates, key=lambda item: item[0]):
            if total <= hard_max:
                break
            self._delete_sentence(int(r["id"]))
            total -= len(r["content"])
            removed += 1
        return removed

    def _delete_sentence(self, sentence_id: int) -> None:
        with connect() as conn:
            conn.execute("DELETE FROM report_sentence_fact WHERE sentence_id=?", (sentence_id,))
            conn.execute("DELETE FROM report_sentence_inference WHERE sentence_id=?", (sentence_id,))
            conn.execute("DELETE FROM report_sentences WHERE id=?", (sentence_id,))

    def _timeline_block(self) -> str:
        """轻量事件时间线:从已沉淀事件(含时间)按时间排序生成,供 Writer 引用。"""
        from app.knowledge import load_events

        try:
            events = load_events(self.task_id)
        except Exception:
            events = []
        dated = [e for e in events if e.get("time")]
        if not dated:
            return ""
        dated.sort(key=lambda e: str(e.get("time", "")))
        lines = [f"- {e.get('time', '')}: {e.get('name', '')}" for e in dated[:15]]
        return "\n".join(lines)

    def _run_quality_check(self, variant, plan: dict, report_id: int) -> None:
        """报告质量检查:重复/模板缺失/术语违规/机构规则/逻辑跳跃 → qa_notes 供人工审核。"""
        from app.quality import run_quality_check

        forbidden = (variant.terminology or {}).get("forbidden", []) if variant else []
        institution_rules = self._effective_institution_rules(variant)
        report_policy = self.task.get("report_policy") or {}
        qa_policy = {
            "allow_symbolic_lists": bool(
                ((report_policy.get("template_policy") or {}).get("format_summary") or {})
                .get("export_hints", {})
                .get("allow_symbolic_lists")
            )
        }
        try:
            issues = run_quality_check(report_id, plan.get("structure") or [], forbidden, institution_rules, qa_policy)
        except Exception:
            issues = []
        issues.extend(append_business_issues(report_id, self.task.get("task_profile") or {}, self._facts()))
        self._update(qa_notes=issues)

    def _auto_quality_fix(self, report_id: int, plan: dict) -> bool:
        """低风险质量问题自动修正。

        三档原则:
        1. 确定性问题自动修;
        2. 低风险问题可逆修;
        3. 语义性问题只提示人工/模型复核。
        """
        fixed = {
            "redundant_hidden": self._hide_redundant_sentences(report_id),
        }
        if not any(fixed.values()):
            return False
        previous = self.task.get("auto_revision") or {}
        merged = dict(previous)
        merged["quality_fix"] = fixed
        self._update(auto_revision=merged)
        return True

    def _hide_redundant_sentences(self, report_id: int) -> int:
        """把低价值重复句从导出中排除,不删除数据库记录,方便人工恢复。"""
        from app.quality import _cosine_similarity

        number_re = re.compile(r"\d{1,4}(?:\.\d+)?%?|\d{1,2}月\d{1,2}日")
        with connect() as conn:
            rows = conn.execute(
                "SELECT id, section, paragraph, content, source_refs FROM report_sentences "
                "WHERE report_id=? AND selected=1 ORDER BY position, id",
                (report_id,),
            ).fetchall()
        section_counts: dict[str, int] = {}
        for row in rows:
            section_counts[row["section"]] = section_counts.get(row["section"], 0) + 1
        seen: list[dict] = []
        hide_ids: list[int] = []
        for row in rows:
            if section_counts.get(row["section"], 0) <= 2:
                seen.append(row)
                continue
            try:
                refs = json.loads(row["source_refs"] or "{}")
            except (TypeError, ValueError):
                refs = {}
            fact_ids = {int(fid) for fid in refs.get("fact_ids") or [] if str(fid).isdigit()}
            numbers = set(number_re.findall(row["content"] or ""))
            should_hide = False
            for prev in seen[-12:]:
                try:
                    prev_refs = json.loads(prev["source_refs"] or "{}")
                except (TypeError, ValueError):
                    prev_refs = {}
                prev_facts = {int(fid) for fid in prev_refs.get("fact_ids") or [] if str(fid).isdigit()}
                prev_numbers = set(number_re.findall(prev["content"] or ""))
                shares_facts = bool(fact_ids and prev_facts and fact_ids & prev_facts)
                repeats_numbers = bool(numbers and prev_numbers and numbers <= prev_numbers)
                near_duplicate = _cosine_similarity(row["content"] or "", prev["content"] or "") > 0.68
                if near_duplicate or (shares_facts and repeats_numbers and row["section"] != prev["section"]):
                    should_hide = True
                    break
            if should_hide:
                hide_ids.append(int(row["id"]))
                section_counts[row["section"]] -= 1
            else:
                seen.append(row)
        if hide_ids:
            with connect() as conn:
                for sentence_id in hide_ids:
                    conn.execute("UPDATE report_sentences SET selected=0 WHERE id=?", (sentence_id,))
        return len(hide_ids)

    def _auto_revision(self, report_id: int, plan: dict, facts: list[dict],
                       inferences: list[dict], variant) -> None:
        """验证闭环:自动修订明确可修的问题(Writer → QA → Revision)。

        1. 无依据句子(无 fact/inference 引用)→ 删除
        2. 数字与事实不一致句子 → LLM 重写(只修正数字,防模型改述出错)
        3. 模板/规划要求但缺失的章节 → 补写(复用 Writer 单章生成)
        修订结果记录到任务(auto_revision),剩余问题仍保留在 qa_notes 供人工。
        """
        import json as _json
        import re as _re

        digit_re = _re.compile(r"\d+(?:\.\d+)?")
        fact_by_id = {f["id"]: f["content"] for f in facts}
        with connect() as conn:
            rows = conn.execute(
                "SELECT id, section, content, source_refs, source_level FROM report_sentences "
                "WHERE report_id=? ORDER BY position", (report_id,),
            ).fetchall()
            # 数字基准 = fact 内容 ∪ 对应 evidence 原文片段(fact 提炼句常不含数字)
            fact_ids = [int(f["id"]) for f in facts if f.get("id") is not None]
            if fact_ids:
                ev_rows = conn.execute(
                    "SELECT fact_id, quote FROM evidence WHERE fact_id IN (%s)"
                    % ",".join("?" * len(fact_ids)),
                    fact_ids,
                ).fetchall()
            else:
                ev_rows = []
        quote_by_fact: dict[int, list[str]] = {}
        for ev in ev_rows:
            quote_by_fact.setdefault(ev["fact_id"], []).append(ev["quote"])
        removed = 0
        rewritten = 0
        removed_template_meta = 0
        for row in rows:
            try:
                refs = _json.loads(row["source_refs"] or "{}")
            except (TypeError, ValueError):
                refs = {}
            fact_ids = [int(x) for x in (refs.get("fact_ids") or []) if str(x).isdigit()]
            inf_ids = [int(x) for x in (refs.get("inference_ids") or []) if str(x).isdigit()]
            if not fact_ids and not inf_ids:
                if row["source_level"] == "TRANSITION":
                    continue  # 有意的过渡句(承上启下/导语),保留
                self._delete_sentence(int(row["id"]))
                removed += 1
                continue
            if (
                (self.task.get("task_profile") or {}).get("report_mode") == "requirement_summary"
                and looks_like_template_meta(row["content"])
            ):
                self._delete_sentence(int(row["id"]))
                removed_template_meta += 1
                continue
            sentence_digits = set(digit_re.findall(row["content"]))
            fact_digits: set[str] = set()
            for fid in fact_ids:
                fact_digits |= set(digit_re.findall(fact_by_id.get(fid, "")))
                for quote in quote_by_fact.get(fid, []):
                    fact_digits |= set(digit_re.findall(quote))
            missing = {d for d in sentence_digits - fact_digits if len(d) >= 2}
            if missing:
                new_text = self._rewrite_sentence(row["content"], [fact_by_id.get(f, "") for f in fact_ids])
                if new_text:
                    with connect() as conn:
                        conn.execute("UPDATE report_sentences SET content=? WHERE id=?", (new_text, row["id"]))
                    rewritten += 1
        # 缺失章节补写
        existing = {row["section"] for row in rows}
        style_block = variant.to_prompt_block() if variant else ""
        appended = 0
        for chapter_index, chapter in enumerate(plan.get("structure") or [], start=1):
            if chapter and chapter not in existing:
                if self._append_chapter(report_id, chapter, plan, facts, inferences, style_block, chapter_index):
                    appended += 1
        self._update(auto_revision={
            "removed": removed,
            "rewritten": rewritten,
            "appended": appended,
            "removed_template_meta": removed_template_meta,
        })

    def _rewrite_sentence(self, text: str, fact_texts: list[str]) -> str | None:
        """LLM 重写句子:仅修正数字与事实不一致,保持原意。"""
        from app.agents.base import BaseAgent

        agent = BaseAgent()
        agent.role = _REWRITE_SYSTEM
        prompt = (
            f"原句:{text}\n\n可引用事实:\n" + "\n".join(f"- {f}" for f in fact_texts if f) + "\n"
            "请修正句子中与事实不符的数字,输出 JSON。"
        )
        try:
            payload = agent.generate_json(prompt)
            new_text = str(payload.get("text", "")).strip()
            return new_text if new_text else None
        except Exception:
            return None

    def _append_chapter(self, report_id: int, chapter: str, plan: dict,
                        facts: list[dict], inferences: list[dict], style_block: str,
                        chapter_index: int = 999) -> bool:
        """补写缺失章节:检索该章事实 → Writer 单章生成 → 按规划位置插入。"""
        if self.cm is not None:
            chapter_facts, chapter_inferences, chapter_style = self.cm.for_writer_section(
                chapter, facts, inferences, style_block
            )
        else:
            chapter_facts, chapter_inferences, chapter_style = facts, inferences, style_block
        chapter_plan = next((c for c in (plan.get("chapter_plans") or []) if c.get("title") == chapter), {"title": chapter})
        sentences = writer_agent._generate_chapter(
            chapter, 1, 1, [chapter],
            chapter_facts, chapter_inferences, chapter_style,
            plan,
            {"core_judgment": plan.get("core_judgment", ""),
             "narrative_logic": plan.get("narrative_logic", ""),
             "unified_terms": [], "used_fact_ids": set(),
             "used_inference_ids": set(), "chapter_summaries": []},
            {f["id"] for f in facts}, {i["id"] for i in inferences},
            chapter_plan=chapter_plan,
            institution_rules={},
            business_block=business_block(self.task.get("task_profile") or {}),
        )
        if not sentences:
            return False
        import json as _json
        with connect() as conn:
            paragraph_number = 0
            local_position = 0
            for sent in sentences:
                if sent["paragraph"] != paragraph_number:
                    paragraph_number = sent["paragraph"]
                local_position += 1
                fact_ids = sent["fact_ids"]
                inf_ids = sent["inference_ids"]
                if fact_ids:
                    level = "MATERIAL_FACT"
                elif inf_ids:
                    level = "MATERIAL_INFERENCE"
                else:
                    level = "TRANSITION"
                conn.execute(
                    "INSERT INTO report_sentences(report_id, section, paragraph, position, "
                    "content, source_level, source_refs) VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (report_id, chapter, paragraph_number, _chapter_position(chapter_index, local_position),
                     sent["text"], level,
                     _json.dumps({"fact_ids": fact_ids, "inference_ids": inf_ids}, ensure_ascii=False)),
                )
        return True

    def _normalize_report_order(self, report_id: int, plan: dict) -> None:
        order = {
            str(title): index
            for index, title in enumerate(plan.get("structure") or [], start=1)
            if str(title).strip()
        }
        if not order:
            return
        with connect() as conn:
            rows = conn.execute(
                "SELECT id, section FROM report_sentences WHERE report_id=? ORDER BY position, id",
                (report_id,),
            ).fetchall()
            counters: dict[str, int] = {}
            for row in rows:
                section = str(row["section"])
                counters[section] = counters.get(section, 0) + 1
                conn.execute(
                    "UPDATE report_sentences SET position=? WHERE id=?",
                    (_chapter_position(order.get(section, 999), counters[section]), row["id"]),
                )

    def _sink_knowledge(self, facts: list[dict]) -> None:
        """知识沉淀:实体直接复用材料理解结果(零 LLM),事件/关系轻量抽取。

        材料理解阶段已稳定识别 entities/times,Knowledge 阶段只补充
        材料理解无法可靠完成的事件与关系(输入仅 facts+inferences,不重复整体理解)。
        """
        from app.knowledge import save_entity

        entity_count = 0
        for insight in self.task.get("material_insights", []):
            for name in insight.get("entities", []) or []:
                try:
                    save_entity(str(name), "", task_id=self.task_id)
                    entity_count += 1
                except Exception:
                    continue
        inferences = self._inferences()
        texts = [f["content"] for f in facts] + [i["content"] for i in inferences]
        knowledge_agent.task_id = self.task_id
        stats = knowledge_agent.extract(texts)
        stats["entities_from_insights"] = entity_count
        self._update(knowledge_stats=stats)

    def _start_background_post_review(self, facts: list[dict]) -> None:
        """Run non-critical post-review jobs without delaying TTFR."""
        self._update(background_jobs={
            "knowledge": {"status": "queued"},
        })

        def _run() -> None:
            import time as _time

            started = _time.time()
            try:
                self._update(background_jobs={
                    "knowledge": {"status": "running", "started_at": round(started, 1)},
                })
                with llm_priority(PRIORITY_BACKGROUND), self._token_context("knowledge"):
                    self._sink_knowledge(facts)
                self._update(background_jobs={
                    "knowledge": {
                        "status": "done",
                        "duration_seconds": round(_time.time() - started, 1),
                    },
                })
            except Exception as exc:
                self._update(background_jobs={
                    "knowledge": {
                        "status": "failed",
                        "duration_seconds": round(_time.time() - started, 1),
                        "error": str(exc),
                    },
                })

        threading.Thread(target=_run, daemon=True).start()

    def finalize(self) -> None:
        """收尾:报告置为 final,阶段完成(知识已在 _sink_knowledge 沉淀;增量更新预留点)。"""
        report_id = self.task.get("report_id")
        if report_id is not None:
            with connect() as conn:
                conn.execute("UPDATE reports SET status='final' WHERE id=?", (report_id,))
        self._update(stage=str(Stage.DONE))

    # ---------- 辅助 ----------

    def _record_artifact(self, stage: str, payload: dict, status: str = "done") -> None:
        """Record a task-scoped stage artifact for audit and breakpoint resume."""
        effective_inputs = {
            "theme": self.task.get("theme", ""),
            "requirements_hash": stable_hash(self.task.get("user_requirements", "")),
            "material_ids": self.task.get("material_ids", []),
            "variant_id": self.task.get("variant_id"),
            "plan_id": self.task.get("plan_id"),
            "fact_ids": self.task.get("fact_ids", []),
            "inference_ids": self.task.get("inference_ids", []),
            "external_ids": self.task.get("external_ids", []),
            "report_id": self.task.get("report_id"),
            "stage": stage,
        }
        try:
            input_hash = save_task_artifact(self.task_id, stage, effective_inputs, payload, status=status)
            artifacts = dict(self.task.get("artifact_status") or {})
            artifacts[stage] = {
                "status": status,
                "input_hash": input_hash,
                "summary": {
                    key: value for key, value in payload.items()
                    if key.endswith("_id") or key.endswith("_ids") or key.endswith("_count")
                },
            }
            self._update(artifact_status=artifacts)
        except Exception:
            # Artifact recording must never break the report generation path.
            return

    def _token_context(self, stage: str):
        profile = self.task.get("task_profile") or {}
        return token_context(
            task_id=self.task_id,
            stage=stage,
            report_mode=str(profile.get("report_mode", "")),
            material_count=len(self.task.get("material_ids", []) or []),
        )

    def _ensure_context_manager(self) -> None:
        if self.cm is not None:
            return
        from app.context import ContextManager

        units_by_material, filenames = self._load_units()
        self.cm = ContextManager(self.task, units_by_material, filenames)

    def _report_has_all_chapters(self) -> bool:
        report_id = self.task.get("report_id")
        if report_id is None or not self.task.get("plan_id"):
            return False
        try:
            plan = self._plan()
        except Exception:
            return False
        expected = [
            str(c.get("title", ""))
            for c in (plan.get("chapter_plans") or [{"title": t} for t in (plan.get("structure") or [])])
            if str(c.get("title", "")).strip()
        ]
        if not expected:
            return False
        with connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM task_artifacts "
                "WHERE task_id=? AND stage LIKE 'chapter_draft:%' AND status='done'",
                (self.task_id,),
            ).fetchall()
        actual = set()
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError):
                continue
            if payload.get("status") == "done" and payload.get("chapter"):
                actual.add(str(payload.get("chapter")))
        return set(expected) <= actual

    def _fetchone(self, sql: str, params: tuple = ()):
        with connect() as conn:
            return conn.execute(sql, params).fetchone()

    def _plan(self) -> dict:
        plan_id = self.task.get("plan_id")
        row = self._fetchone("SELECT * FROM report_plans WHERE id=?", (plan_id,))
        if row is None:
            raise ValueError(f"PLAN_NOT_FOUND: {plan_id}")
        return {
            "id": row["id"],
            "title": row["title"],
            "structure": _loads(row["structure"]),
            "dimensions": _loads(row["dimensions"]),
            "user_requirements": row["user_requirements"],
            "core_question": row["core_question"],
            "core_judgment": row["core_judgment"],
            "narrative_logic": row["narrative_logic"],
            "required_facts": _loads(row["required_facts"]),
            "chapter_plans": _loads(row["chapter_plans"]),
            "budget": _loads(row["budget"]),
            "plan_stage": row["plan_stage"] if "plan_stage" in row.keys() else "analysis",
            "plan_version": row["plan_version"] if "plan_version" in row.keys() else 1,
            "analysis_plan_json": _loads(row["analysis_plan_json"]) if "analysis_plan_json" in row.keys() else {},
            "final_plan_json": _loads(row["final_plan_json"]) if "final_plan_json" in row.keys() else {},
            "finalized_at": row["finalized_at"] if "finalized_at" in row.keys() else None,
        }

    def _safe_plan_snapshot(self) -> dict:
        try:
            plan = self._plan()
        except Exception:
            return {}
        return {
            "id": plan.get("id"),
            "title": plan.get("title"),
            "stage": plan.get("plan_stage"),
            "version": plan.get("plan_version"),
            "core_question": plan.get("core_question"),
            "core_judgment": plan.get("core_judgment"),
            "initial_or_final_logic": plan.get("narrative_logic"),
            "dimensions": plan.get("dimensions"),
            "required_facts": plan.get("required_facts"),
            "structure": plan.get("structure"),
            "chapter_plans": plan.get("chapter_plans"),
            "budget": plan.get("budget"),
            "analysis_plan_json": plan.get("analysis_plan_json"),
            "final_plan_json": plan.get("final_plan_json"),
        }

    def _facts(self) -> list[dict]:
        result = []
        role_by_material = {
            int(item.get("material_id")): item.get("material_role", "unknown")
            for item in self.task.get("material_insights", [])
            if item.get("material_id") is not None
        }
        support_by_material = {
            int(item.get("material_id")): item.get("claim_support", "unknown")
            for item in self.task.get("material_insights", [])
            if item.get("material_id") is not None
        }
        for fact_id in self.task.get("fact_ids", []):
            row = self._fetchone("SELECT * FROM facts WHERE id=?", (int(fact_id),))
            if row is None:
                continue
            with connect() as conn:
                ev_rows = conn.execute(
                    "SELECT material_id, source_file FROM evidence WHERE fact_id=?", (row["id"],)
                ).fetchall()
            source_roles = sorted({role_by_material.get(int(ev["material_id"]), "unknown") for ev in ev_rows})
            claim_supports = sorted({support_by_material.get(int(ev["material_id"]), "unknown") for ev in ev_rows})
            source_files = sorted({ev["source_file"] for ev in ev_rows if ev["source_file"]})
            result.append({
                "id": row["id"],
                "content": row["content"],
                "sources": load_evidence_quotes(row["id"]),
                "source_roles": source_roles,
                "claim_supports": claim_supports,
                "source_files": source_files,
            })
        return result

    def _inferences(self) -> list[dict]:
        result = []
        for inference_id in self.task.get("inference_ids", []) + self.task.get("external_ids", []):
            row = self._fetchone("SELECT * FROM inferences WHERE id=?", (int(inference_id),))
            if row is not None:
                result.append({
                    "id": row["id"],
                    "content": row["content"],
                    "source_level": row["source_level"],
                    "based_fact_ids": json.loads(row["based_fact_ids"] or "[]"),
                })
        return result

    def _style_block(self) -> str:
        variant = self._selected_variant()
        return variant.to_prompt_block() if variant else ""

    def _selected_variant(self):
        """变体选择优先级:任务指定 variant_id > 全局锁定变体。"""
        from app.memory.style import get_locked_variant, get_variant

        variant_id = self.task.get("variant_id")
        if variant_id is not None:
            variant = get_variant(int(variant_id))
            if variant is not None:
                return variant
        return get_locked_variant()

    def _load_units(self) -> tuple[dict[int, list[Unit]], dict[int, str]]:
        units_by_material: dict[int, list[Unit]] = {}
        filenames: dict[int, str] = {}
        with connect() as conn:
            for material_id in self.task.get("material_ids", []):
                material_id = int(material_id)
                material = conn.execute("SELECT filename FROM materials WHERE id=?", (material_id,)).fetchone()
                if material is not None:
                    filenames[material_id] = material["filename"]
                rows = conn.execute(
                    "SELECT * FROM units WHERE material_id=? ORDER BY id", (material_id,)
                ).fetchall()
                units_by_material[material_id] = [
                    Unit(
                        id=r["id"], material_id=r["material_id"], kind=r["kind"], content=r["content"],
                        page=r["page"], paragraph=r["paragraph"], image_desc=r["image_desc"],
                    )
                    for r in rows
                ]
        return units_by_material, filenames


def _loads(text: str):
    import json

    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return []


def _parse_stats(parse_results: list[dict], parse_errors: list[dict],
                 embed_errors: list[dict], total_seconds: float) -> dict:
    """Summarize parser health without mixing vectorization failures into parsing."""
    by_format: dict[str, dict] = {}
    total_units = 0
    ocr_triggered = 0
    durations: list[float] = []
    for item in parse_results:
        filename = str(item.get("filename") or "")
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else "unknown"
        bucket = by_format.setdefault(ext, {
            "total": 0,
            "success": 0,
            "reused": 0,
            "partial": 0,
            "failed": 0,
            "unit_count": 0,
            "avg_seconds": 0,
        })
        status = str(item.get("status") or "failed")
        bucket["total"] += 1
        if status in bucket:
            bucket[status] += 1
        else:
            bucket["failed"] += 1
        units = int(item.get("unit_count") or 0)
        total_units += units
        bucket["unit_count"] += units
        if item.get("ocr_enabled"):
            ocr_triggered += 1
        duration = float(item.get("duration_seconds") or 0)
        if duration > 0:
            durations.append(duration)
            bucket.setdefault("_durations", []).append(duration)
    for bucket in by_format.values():
        ds = bucket.pop("_durations", [])
        bucket["avg_seconds"] = round(sum(ds) / len(ds), 2) if ds else 0
        bucket["success_rate"] = round((bucket["success"] + bucket["partial"] + bucket["reused"]) / max(bucket["total"], 1), 3)
    success = sum(1 for item in parse_results if item.get("status") == "success")
    reused = sum(1 for item in parse_results if item.get("status") == "reused")
    partial = sum(1 for item in parse_results if item.get("status") == "partial")
    failed = sum(1 for item in parse_results if item.get("status") == "failed")
    return {
        "total_files": len(parse_results),
        "success_files": success,
        "reused_files": reused,
        "partial_files": partial,
        "failed_files": failed,
        "parse_error_count": len(parse_errors),
        "embed_error_count": len(embed_errors),
        "success_rate": round((success + partial + reused) / max(len(parse_results), 1), 3),
        "total_units": total_units,
        "avg_parse_seconds": round(sum(durations) / len(durations), 2) if durations else 0,
        "total_parse_seconds": total_seconds,
        "ocr_triggered_files": ocr_triggered,
        "ocr_trigger_rate": round(ocr_triggered / max(len(parse_results), 1), 3),
        "by_format": by_format,
        "failure_reasons": _reason_buckets(parse_errors),
        "embed_failure_reasons": _reason_buckets(embed_errors),
    }


def _reason_buckets(errors: list[dict]) -> dict:
    buckets: dict[str, int] = {}
    for item in errors:
        error = str(item.get("error") or "").lower()
        if "timed out" in error or "timeout" in error:
            key = "timeout"
        elif "database is locked" in error:
            key = "database_locked"
        elif "no route to host" in error or "connection reset" in error:
            key = "network"
        elif "unsupported" in error:
            key = "unsupported_format"
        elif "ocr" in error:
            key = "ocr"
        elif "layout" in error:
            key = "layout"
        else:
            key = "other"
        buckets[key] = buckets.get(key, 0) + 1
    return buckets


def _load_historical_material_insight(material_id: int, filename: str = "") -> dict | None:
    """Reuse stable material understanding across tasks.

    The row is treated as material-level cache. Downstream Task Profile/Planner
    still reinterprets material roles and evidence boundaries for the current
    user goal.
    """
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT material_id, doc_type, topic, key_sections, key_points, entities, times, "
                "material_role, claim_support, allowed_usage, forbidden_usage, missing_information, analysis_version, task_id "
                "FROM material_insights WHERE material_id=? ORDER BY id DESC LIMIT 1",
                (int(material_id),),
            ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    if row["analysis_version"] != _MATERIAL_ANALYSIS_PROMPT_VERSION:
        return None
    # 任务边界:材料固有属性(doc_type/topic/entities/times/结构)可跨任务复用;
    # 上下文相关字段(role/claim_support/usage 边界)清零,由当前任务重新解读,
    # 避免把上一任务的解读带进新任务。
    return {
        "material_id": int(row["material_id"]),
        "filename": filename,
        "doc_type": row["doc_type"],
        "topic": row["topic"],
        "key_points": _loads(row["key_points"] or "[]"),
        "key_sections": _loads(row["key_sections"] or "[]"),
        "entities": _loads(row["entities"] or "[]"),
        "times": _loads(row["times"] or "[]"),
        "material_role": "",
        "claim_support": "unknown",
        "allowed_usage": [],
        "forbidden_usage": [],
        "missing_information": _loads(row["missing_information"] or "[]"),
        "reused_from": row["task_id"] if "task_id" in row.keys() else "",
    }


def _report_word_estimate(text: str) -> int:
    """Chinese report length estimate: CJK chars plus latin/number word groups."""
    raw = text or ""
    return len(re.findall(r"[\u4e00-\u9fff]", raw)) + len(re.findall(r"[A-Za-z0-9]+", raw))


def _material_understanding_text(units: list[Unit], budget_chars: int = 4000,
                                 theme: str = "") -> tuple[str, dict]:
    """Select a bounded prompt view without truncating stored Units.

    采样原则(结构覆盖 + 任务相关性 + Token Budget):
    1. 结构锚点(标题/表格)全选——保证主要章节被代表性覆盖;
    2. 正文按任务主题相关度降序,预算内填充——任务重点优先;
    3. 预算不足时分批/截断只影响采样范围,不删存储数据。
    """
    non_empty = [u for u in units if (u.content or "").strip()]
    raw_chars = sum(len(u.content or "") for u in non_empty)
    if raw_chars <= budget_chars:
        text = "\n".join(u.content for u in non_empty)
        return text, {
            "raw_chars": raw_chars,
            "context_chars": len(text),
            "truncated": False,
            "selected_units": len(non_empty),
            "total_units": len(units),
        }
    anchors = [u for u in non_empty if u.kind in ("heading", "table")]
    body = [u for u in non_empty if u.kind not in ("heading", "table")]
    selected: list[Unit] = list(anchors)
    total = sum(len(u.content or "") for u in selected)
    # 任务相关性:正文按主题相关度降序(相关度高的先进入预算)
    if theme:
        body = sorted(body, key=lambda u: _text_relevance(theme, u.content or ""), reverse=True)
    for unit in body:
        block_len = len(unit.content or "")
        if total + block_len > budget_chars:
            continue
        selected.append(unit)
        total += block_len
    seen: set[int] = set()
    blocks: list[str] = []
    for unit in selected:
        key = id(unit)
        if key in seen:
            continue
        seen.add(key)
        if unit.page is not None:
            prefix = f"[第{unit.page}页] "
        elif unit.paragraph is not None:
            prefix = f"[段/块{unit.paragraph}] "
        else:
            prefix = ""
        blocks.append(prefix + (unit.content or ""))
    text = "\n".join(blocks)
    return text, {
        "raw_chars": raw_chars,
        "context_chars": len(text),
        "truncated": True,
        "selected_units": len(blocks),
        "total_units": len(units),
        "selection_strategy": "structure_anchors_task_relevance",
    }


def _text_relevance(query: str, text: str) -> float:
    """任务主题与正文的文本相关度(ngram + 关键词,确定性,无向量调用)。"""
    import re as _re

    from app.retrieval.rag import _ngram_similarity

    terms = [t for t in _re.split(r"[、/，,；;()\s]+", query or "") if len(t) >= 2]
    hits = sum(1 for t in terms if t and t in (text or ""))
    keyword_score = min(1.0, hits / max(len(terms), 1)) if terms else 0.0
    return keyword_score * 0.5 + _ngram_similarity(query, text) * 0.5
