"""Workflow Controller:单任务串行编排。

阶段:解析 → 去重 → 规划 → 材料理解 → 事实 → 冲突 → 分析 → 写作 → 在线评审 → 完成。
Agent 由控制器顺序驱动,阶段产物写入短期记忆(task payload)。
"""
import json
import re
import threading
import time

import numpy as np
from sqlalchemy import delete, func, insert, select, update

from app.analysis.analyzer import AnalysisAgent
from app.evidence.extractor import EvidenceAgent, load_evidence_quotes
from app.planning.planner import PlannerAgent
from app.writing.writer import WriterAgent, _chapter_position
from app.report_tools import build_task_profile, business_block, looks_like_template_meta
from app.cache import get_cached, set_cached, stable_hash
from app.config import settings
from app.db import session_scope
from app.infrastructure.orm import (
    Base,
    ORMEvidence,
    ORMFact,
    ORMInference,
    ORMInsight,
    ORMLLMCall,
    ORMMaterial,
    ORMMaterialScan,
    ORMPlan,
    ORMSentence,
    ORMSentenceFact,
    ORMSentenceInference,
    ORMTaskArtifact,
    ORMUnit,
)
from app.llm_queue import PRIORITY_BACKGROUND, llm_priority
from app.memory import short_term
from app.models import Stage, Unit
from app.parser import PARSER_VERSION, parse_file_with_profile
from app.policy import build_report_policy, policy_prompt_block
from app.retrieval import vector_store
from app.report_versions import (
    attach_delta_version, build_incremental_impact, ensure_report_version,
    reconcile_incremental_lifecycle, refresh_incremental_delta,
)

from app.task_artifacts import save_task_artifact
from app.task_runs import update_task_run
from app.token_monitor import build_token_efficiency, build_workload_profile, token_context
from app.context_budget import count_tokens, publish_context_audit, tokenizer_method

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

planner = PlannerAgent()
evidence_agent = EvidenceAgent()
analysis_agent = AnalysisAgent()
writer_agent = WriterAgent()


def _analysis_groups(facts: list[dict], max_group_size: int = 45) -> dict[str, list[dict]]:
    """Group facts for local analysis without domain-specific keywords."""
    groups: dict[str, list[dict]] = {}
    for fact in facts:
        dimension = str(fact.get("dimension") or "未分类")
        groups.setdefault(dimension, []).append(fact)
    refined: dict[str, list[dict]] = {}
    for dimension, items in groups.items():
        if len(items) <= max_group_size:
            refined[dimension] = items
            continue
        buckets: dict[str, list[dict]] = {}
        for fact in items:
            need_id = int(fact.get("need_id") or 0)
            fact_type = str(fact.get("fact_type") or "STATEMENT")
            key = f"{dimension} / need:{need_id or 'open'} / type:{fact_type}"
            buckets.setdefault(key, []).append(fact)
        for key, bucket in buckets.items():
            if len(bucket) <= max_group_size:
                refined[key] = bucket
                continue
            for index in range(0, len(bucket), max_group_size):
                refined[f"{key} / batch:{index // max_group_size + 1}"] = bucket[index:index + max_group_size]
    return refined


def _graph_artifact_status(graph_status: dict) -> str:
    status = str((graph_status or {}).get("status") or "unknown")
    if status in {"ready", "reused"}:
        return "done"
    if status == "partial_ready":
        return "partial"
    if status in {"pending", "queued", "running", "unknown"}:
        return "pending"
    if status == "skipped":
        return "skipped"
    return "failed"


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

    def _control_boundary(self) -> None:
        """Honor pauses here; the gateway also checks after every LLM call."""
        task = short_term.load_task(self.task_id) or self.task
        if (task.get("control_request") if task else "") == "pause":
            self._update(stage="paused", queue_status={"status": "paused"}, control_request="")
            raise RuntimeError("TASK_PAUSED")

    def run_to_review(self) -> None:
        """跑完 解析→去重→规划→事实→冲突→分析→写作,停在评审阶段等待用户。

        各阶段耗时记录到 task.stage_timings(自阶段起点累计秒数),便于定位热点。
        """
        import time as _time

        from app.agents.base import llm_stats, reset_llm_stats

        reset_llm_stats()  # 任务级 LLM 调用统计起点(调用次数 + 输入规模)
        update_task_run(str(self.task.get("run_id") or ""), status="running")
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
            samples = list(self.task.get("resource_samples") or [])
            samples.append(_resource_sample(name, _durations[name]))
            self._update(stage_timings=_marks, stage_durations=_durations, resource_samples=samples[-30:])

        with self._token_context("parse"):
            self.parse_materials()
        self._control_boundary()
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
        self._control_boundary()
        self._record_artifact("dedup", {"dedup_pairs": self.task.get("dedup_pairs", [])})
        _mark("dedup")
        with self._token_context("material_analysis"):
            self.analyze_materials()
        self._control_boundary()
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
            "analysis_plan_snapshot": self._safe_plan_snapshot(),
        })
        _mark("plan")
        self._control_boundary()
        if (self.task.get("incremental_update")
                and not self.task.get("incremental_added_material_ids")
                and not self.task.get("intervention_force_evidence")):
            # 增量补写模式(无新增材料):直接复用 base 任务已提取的 facts,
            # 不做 evidence 提取/引入(省重跑)。fact_ids 已在建任务时预置为 base 事实全集,
            # self._facts() 按 task 注册的 fact_ids 取出,无需新确认。
            self._ensure_context_manager()
            facts = self._facts()
            self._update(
                stage=str(Stage.EVIDENCE),
                evidence_progress={"status": "reused", "fact_count": len(facts)},
            )
        elif self.task.get("fact_ids"):
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
        self._control_boundary()
        if self.task.get("incremental_update") and self.task.get("incremental_new_fact_ids"):
            lifecycle = reconcile_incremental_lifecycle(
                list(self.task.get("incremental_inherited_fact_ids") or []),
                list(self.task.get("incremental_new_fact_ids") or []),
                list(self.task.get("incremental_inherited_inference_ids") or []),
            )
            self._update(incremental_lifecycle=lifecycle)
            facts = self._facts()
        self._record_artifact("evidence", {
            "fact_ids": self.task.get("fact_ids", []),
            "fact_count": len(facts),
        })
        _mark("evidence")
        # Build the task graph from validated Facts before Analysis. A graph
        # failure is recorded and degrades to ordinary Hybrid RAG; it never
        # blocks the report path or weakens evidence requirements.
        if settings.graph_build_before_analysis:
            with self._token_context("graph_build"):
                self._build_task_graph(facts)
        else:
            from app.graph import graph_service

            if graph_service.mode == "off":
                self._update(graph_status={"status": "skipped", "reason": "graph_mode_off"})
            elif graph_service.has_task_graph(self.task_id):
                stats = graph_service.task_graph(self.task_id).get("stats") or {}
                self._update(graph_status={"status": "reused", **stats})
            else:
                self._update(graph_status={"status": "pending", "reason": "post_review_background"})
        graph_status = self.task.get("graph_status", {})
        self._record_artifact(
            "graph", {"graph_status": graph_status},
            status=_graph_artifact_status(graph_status),
        )
        _mark("graph")
        if self.task.get("conflict_ids"):
            self._update(stage=str(Stage.CONFLICT), resume={"stage": "conflict", "status": "reused"})
        else:
            with self._token_context("conflict"):
                self.detect_conflicts()
        self._record_artifact("conflict", {"conflict_ids": self.task.get("conflict_ids", [])})
        _mark("conflict")
        if self.task.get("run_mode") == "material_comparison":
            # Comparison is a fact-and-provenance product. Ordinary report
            # inferences do not participate in baseline sentence matching.
            from app.material_comparison import complete_comparison_task, mark_comparison_failed
            try:
                comparison = complete_comparison_task(self.task_id)
            except Exception as exc:
                mark_comparison_failed(self.task_id, str(exc))
                raise
            _mark("material_comparison")
            run_id = str(self.task.get("run_id") or "")
            self._update(
                stage=str(Stage.REVIEW),
                material_comparison=comparison,
                stage_timings=_marks,
                stage_durations=_durations,
                llm_stats=llm_stats(),
                token_efficiency=build_token_efficiency(self.task_id, None, run_id=run_id),
                workload_profile=build_workload_profile(self.task_id, run_id=run_id),
                critical_path_done=True,
            )
            update_task_run(
                run_id, status="review",
                metadata={"comparison_id": comparison.get("id"), "report_id": comparison.get("report_id")},
                finished=True,
            )
            return
        if self.task.get("analysis_done"):
            self._update(stage=str(Stage.ANALYSIS), resume={"stage": "analysis", "status": "reused"})
        elif self.task.get("incremental_update"):
            if self.task.get("intervention_force_analysis"):
                self._update(
                    inference_ids=[], external_ids=[], analysis_done=False,
                    incremental_generated_inference_ids=[],
                    incremental_generated_external_ids=[],
                    analysis_global_meta={},
                )
                with self._token_context("analysis"):
                    self.analyze(facts)
            elif not self.task.get("incremental_added_material_ids"):
                # 补写模式(无新增材料):继承 base 全部推断,不重新分析。
                self._update(
                    stage=str(Stage.ANALYSIS),
                    resume={"stage": "analysis", "status": "reused"},
                    inference_ids=list(self.task.get("incremental_inherited_inference_ids") or []),
                    external_ids=list(self.task.get("incremental_inherited_external_ids") or []),
                    analysis_done=True,
                )
            else:
                # 增量轮次只分析本轮新事实。旧推断是基线资产,不能走普通
                # 断点逻辑被删除;若本轮曾部分落库,只清理本轮已登记的产物。
                generated_ids = list(self.task.get("incremental_generated_inference_ids") or [])
                generated_ids += list(self.task.get("incremental_generated_external_ids") or [])
                if generated_ids:
                    self._delete_inferences_by_ids(generated_ids)
                    self._update(
                        inference_ids=list(self.task.get("incremental_inherited_inference_ids") or []),
                        external_ids=list(self.task.get("incremental_inherited_external_ids") or []),
                        incremental_generated_inference_ids=[],
                        incremental_generated_external_ids=[],
                        analysis_global_meta={},
                    )
                with self._token_context("analysis"):
                    self.analyze(facts)
        else:
            if self.task.get("inference_ids"):
                # 中断的部分分析:删除本任务旧推断后重跑(幂等,避免部分/重复推断)
                self._delete_task_inferences()
                self._update(inference_ids=[], external_ids=[], analysis_global_meta={})
            with self._token_context("analysis"):
                self.analyze(facts)
        self._control_boundary()
        self._record_artifact("analysis", {
            "inference_ids": self.task.get("inference_ids", []),
            "external_ids": self.task.get("external_ids", []),
        })
        _mark("analysis")
        if self.task.get("incremental_update"):
            # 增量 final_plan 策略:
            # - 无新增材料(补写模式):复用 base 规划,结构不变,只补写内容。
            # - 有新增材料:基于新 facts 重新规划 final_plan,让新结构参与
            #   build_incremental_impact 的结构对比(章节增删重组才能被真实检测)。
            if self.task.get("intervention_force_final_plan"):
                with self._token_context("final_planning"):
                    self.finalize_report_structure()
            elif not self.task.get("incremental_added_material_ids"):
                self._update(stage=str(Stage.PLANNING), resume={"stage": "final_plan", "status": "reused"})
            else:
                with self._token_context("final_planning"):
                    self.finalize_report_structure()
        elif self.task.get("final_plan_frozen"):
            self._update(stage=str(Stage.PLANNING), resume={"stage": "final_plan", "status": "reused"})
        else:
            with self._token_context("final_planning"):
                self.finalize_report_structure()
        self._control_boundary()
        self._record_artifact("final_plan", {
            "plan_id": self.task.get("plan_id"),
            "plan_title": self.task.get("plan_title", ""),
            "final_plan_frozen": self.task.get("final_plan_frozen", False),
            "final_plan_snapshot": self._safe_plan_snapshot(),
        })
        _mark("final_plan")
        target_chapters = self._prepare_incremental_write_scope()
        if self.task.get("report_id") and self._report_has_all_chapters() and not self.task.get("incremental_update"):
            self._update(stage=str(Stage.WRITING), write_progress={"status": "resumed"})
        else:
            with self._token_context("writing"):
                self.write(target_chapter_titles=target_chapters)
        self._control_boundary()
        self._record_artifact("write", {
            "report_id": self.task.get("report_id"),
            "report_stats": self.task.get("report_stats", {}),
            "qa_notes": self.task.get("qa_notes", []),
        })
        if self.task.get("incremental_update") and self.task.get("incremental_delta_id") and self.task.get("report_id"):
            try:
                delta = refresh_incremental_delta(
                    int(self.task["report_id"]),
                    int(self.task["incremental_delta_id"]),
                    task_id=self.task_id,
                )
                self._update(incremental_delta=delta or {})
            except Exception as exc:
                self._update(incremental_delta_error=str(exc)[:200])
        # writer 每轮写完即定稿一个"大版本"(draft 快照),人工后续保存为小版本。
        report_id_v = self.task.get("report_id")
        if report_id_v is not None:
            try:
                version = ensure_report_version(
                    int(report_id_v),
                    task_id=self.task_id,
                    status="draft",
                    change_summary="writer 本轮定稿快照" if self.task.get("incremental_update") else "报告草稿快照",
                    kind="major",
                )
                self._update(current_draft_version_id=version.version_id)
                update_task_run(
                    str(self.task.get("run_id") or ""),
                    candidate_version_id=version.version_id,
                )
                if self.task.get("run_mode") == "interaction_revision":
                    from app.interaction import complete_recompute_for_run
                    complete_recompute_for_run(str(self.task.get("run_id") or ""), version.version_id)
                if self.task.get("incremental_delta_id"):
                    attach_delta_version(int(self.task["incremental_delta_id"]), version.version_id, status="applied")
            except Exception as exc:
                self._update(version_error=str(exc)[:200])
        _mark("write")
        ttfr = round(_time.time() - _t0, 1)
        run_id = str(self.task.get("run_id") or "")
        token_efficiency = build_token_efficiency(self.task_id, self.task.get("report_id"), run_id=run_id)
        workload_profile = build_workload_profile(self.task_id, run_id=run_id)
        if self.task.get("resource_samples"):
            workload_profile["resource_samples"] = self.task.get("resource_samples")
        self._update(
            stage=str(Stage.REVIEW),
            stage_timings=_marks,
            stage_durations=_durations,
            llm_stats=llm_stats(),
            token_efficiency=token_efficiency,
            workload_profile=workload_profile,
            ttfr_seconds=ttfr,
            critical_path_done=True,
        )
        update_task_run(
            str(self.task.get("run_id") or ""), status="review",
            metadata={"report_id": self.task.get("report_id"), "ttfr_seconds": ttfr},
            finished=True,
        )
        self._start_background_post_review(facts)

    def _update(self, **fields) -> None:
        """更新短期记忆并同步内存快照,保证后续阶段读到最新产物。"""
        version_fields = {
            "stage", "parse_progress", "material_analysis_progress", "evidence_progress",
            "write_progress", "material_ids", "material_insights", "fact_ids",
            "inference_ids", "external_ids", "conflict_ids", "report_id",
            "qa_notes", "report_stats",
            "stage_timings", "stage_durations", "llm_stats", "token_efficiency", "workload_profile", "resource_samples", "error",
            "ttfr_seconds", "critical_path_done", "background_jobs", "knowledge_stats",
            "artifact_status", "queue_status",
            "final_plan_frozen", "incremental_plan", "incremental_delta", "incremental_structure_review_required",
        }
        if set(fields) & version_fields:
            current_versions = dict(self.task.get("versions") or {})
            current_versions["task"] = int(current_versions.get("task", 0)) + 1
            if set(fields) & {"material_ids", "material_insights", "parse_progress"}:
                current_versions["materials"] = int(current_versions.get("materials", 0)) + 1
            if set(fields) & {"fact_ids", "inference_ids", "external_ids", "conflict_ids", "qa_notes"}:
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
        if self.task.get("incremental_update"):
            added_ids = {int(i) for i in self.task.get("incremental_added_material_ids") or []}
            material_ids = [mid for mid in material_ids if mid in added_ids]
        total = len(material_ids)
        reused = 0
        parse_started = time.time()
        self._update(parse_progress={"done": 0, "total": total})
        for index, material_id in enumerate(material_ids, start=1):
            item_started = time.time()
            with session_scope() as s:
                material = s.execute(
                    select(ORMMaterial).where(ORMMaterial.c.id == material_id)
                ).mappings().first()
            if material is None:
                continue
            # 复用:同材料已解析过(units 存在)→ 跳过解析与向量化(材料库二次任务直接引用)
            with session_scope() as s:
                pv_row = s.execute(
                    select(ORMMaterial.c.parser_version).where(ORMMaterial.c.id == material_id)
                ).mappings().first()
                already = s.execute(
                    select(func.count().label("c")).select_from(ORMUnit)
                    .where(ORMUnit.c.material_id == material_id)
                ).mappings().first()["c"]
                material_parser_version = pv_row["parser_version"] if pv_row is not None else None
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
            with session_scope() as s:
                s.execute(delete(ORMUnit).where(ORMUnit.c.material_id == material_id))
                for unit in units:
                    unit.material_id = int(material_id)
                    result = s.execute(
                        insert(ORMUnit).values(
                            material_id=unit.material_id, kind=unit.kind, content=unit.content,
                            page=unit.page, paragraph=unit.paragraph, image_desc=unit.image_desc,
                            metadata_json=unit.metadata_json,
                        )
                    )
                    unit_ids.append(result.inserted_primary_key[0])
                s.execute(
                    update(ORMMaterial)
                    .where(ORMMaterial.c.id == int(material_id))
                    .values(
                        parser_version=parse_profile.get("parser") or PARSER_VERSION,
                        parsed_at=func.now(),
                    )
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
            file_nodes = Base.metadata.tables["file_nodes"]
            file_parse_profiles = Base.metadata.tables["file_parse_profiles"]
            with session_scope() as s:
                row = s.execute(
                    select(file_nodes.c.id).where(file_nodes.c.material_id == int(material_id))
                    .order_by(file_nodes.c.id.desc()).limit(1)
                ).mappings().first()
                node_id = int(row["id"]) if row else 0
                s.execute(
                    insert(file_parse_profiles).values(
                        node_id=node_id,
                        material_id=int(material_id),
                        parser=str(profile.get("parser") or PARSER_VERSION),
                        status=status,
                        page_count=int(profile.get("page_count") or 0),
                        text_count=int(profile.get("text_count") or 0),
                        table_count=int(profile.get("table_count") or 0),
                        image_count=int(profile.get("image_count") or 0),
                        markdown_chars=int(profile.get("markdown_chars") or 0),
                        structure_json=json.dumps(structure, ensure_ascii=False),
                        error=error,
                        parsed_at=func.now(),
                    )
                )
        except Exception:
            pass

    def dedup(self) -> None:
        self._update(stage=str(Stage.DEDUP))
        # 材料重复是语义判断(实质内容重复),不由向量相似度阈值(如 0.88)自动判定。
        # 同主题材料相似度天然高,硬编码阈值会误标;重复/印证语义由
        # Intelligence Consolidation 的 FactCluster/corroborates 关系承担。
        self._update(dedup_pairs=[])

    def plan(self) -> None:
        self._update(stage=str(Stage.PLANNING))
        from app.context import ContextManager

        profile = self.task.get("task_profile") or {}
        cm = ContextManager(self.task)
        self.cm = cm
        variant = self._selected_variant()
        policy = build_report_policy(self.task, variant, profile)
        self._update(report_policy=policy)
        requirements = self.task.get("user_requirements", "")
        if self.task.get("intervention_recompute_from"):
            requirements = (
                f"{requirements}\n\n本轮经用户批准的调整：\n"
                f"{self.task.get('incremental_update_reason', '')}"
            ).strip()
        context_block = cm.for_planner(
            self.task.get("theme", ""),
            requirements,
            self.task.get("material_insights", []),
            variant.planner_prompt_block() if variant else self._style_block(),
            business_block(profile),
            policy_prompt_block(policy),
        )
        try:
            plan = planner.plan(context_block, user_requirements=requirements)
        except Exception:
            raise
        self._update(plan_id=plan.id, plan_title=plan.title)

    def finalize_report_structure(self) -> None:
        """Freeze final chapters after Evidence + Analysis, not before."""
        self._update(stage=str(Stage.PLANNING))
        from app.context import ContextManager

        plan = self._plan()
        if self.task.get("incremental_update_reason"):
            plan = {**plan, "update_instruction": str(self.task.get("incremental_update_reason") or "")}
        plan = {**plan, "analysis_global_meta": self.task.get("analysis_global_meta") or {}}
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
            variant.planner_prompt_block() if variant else self._style_block(),
            policy_prompt_block(policy),
        )
        final_plan = planner.finalize_report_plan(
            int(plan["id"]), context_block,
            required_structure=list(self.task.get("intervention_required_structure") or []),
        )
        self._update(plan_title=final_plan.title, final_plan_frozen=True)

    def analyze_materials(self) -> None:
        """材料理解层:识别每份材料的类型/主题/重要章节/关键实体/时间/价值排序。

        结果存 material_insights,并引导 Evidence 提取(高价值材料优先、按主题聚焦)。
        """
        self._update(stage=str(Stage.MATERIAL_ANALYSIS))
        from app.agents.base import BaseAgent

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
            with session_scope() as s:
                for r in s.execute(
                    select(
                        ORMInsight.c.material_id, ORMInsight.c.doc_type, ORMInsight.c.topic,
                        ORMInsight.c.key_sections, ORMInsight.c.key_points, ORMInsight.c.entities,
                        ORMInsight.c.times, ORMInsight.c.material_role, ORMInsight.c.claim_support,
                        ORMInsight.c.allowed_usage, ORMInsight.c.forbidden_usage,
                        ORMInsight.c.missing_information,
                    ).where(ORMInsight.c.task_id == self.task_id)
                ).mappings().all():
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
            from app.runtime_profiles import stage_input_budget_tokens
            text, text_meta = _material_understanding_text(
                units,
                budget_tokens=stage_input_budget_tokens("material_analyzer"),
                theme=self.task.get("theme") or "",
            )
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
                publish_context_audit({
                    "stage": "material_analyzer",
                    "tokenizer_method": tokenizer_method(),
                    "budget_tokens": text_meta.get("budget_tokens", 0),
                    "actual_tokens": text_meta.get("context_tokens", 0),
                    "utilization": text_meta.get("utilization", 0),
                    "truncated": text_meta.get("truncated", False),
                    "sections": {"材料单元": {
                        "candidate_items": text_meta.get("total_units", 0),
                        "selected_items": text_meta.get("selected_units", 0),
                        "omitted_items": max(0, text_meta.get("total_units", 0) - text_meta.get("selected_units", 0)),
                    }},
                })
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
            with session_scope() as s:
                s.execute(
                    insert(ORMInsight).values(
                        material_id=insight["material_id"],
                        doc_type=insight["doc_type"],
                        topic=insight["topic"],
                        key_sections=json.dumps(insight["key_sections"], ensure_ascii=False),
                        key_points=json.dumps(insight.get("key_points", []), ensure_ascii=False),
                        entities=json.dumps(insight["entities"], ensure_ascii=False),
                        times=json.dumps(insight["times"], ensure_ascii=False),
                        value_rank=insight["value_rank"],
                        material_role=insight.get("material_role", ""),
                        claim_support=insight.get("claim_support", "unknown"),
                        allowed_usage=json.dumps(insight.get("allowed_usage", []), ensure_ascii=False),
                        forbidden_usage=json.dumps(insight.get("forbidden_usage", []), ensure_ascii=False),
                        missing_information=json.dumps(insight.get("missing_information", []), ensure_ascii=False),
                        task_id=self.task_id,
                        analysis_version=_MATERIAL_ANALYSIS_PROMPT_VERSION,
                    )
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
        if self.task.get("incremental_update") and self.task.get("incremental_added_material_ids"):
            added_ids = {int(i) for i in self.task.get("incremental_added_material_ids") or []}
            units_by_material = {mid: units for mid, units in units_by_material.items() if int(mid) in added_ids}
            filenames = {mid: name for mid, name in filenames.items() if int(mid) in added_ids}
        insights = self.task.get("material_insights", [])
        self.cm = ContextManager(self.task, units_by_material, filenames)
        # Evidence Needs:结构化待证实需求(优先使用 planner 输出,缺失时退回维度+开放发现)
        needs = plan.get("evidence_needs") or [
            {"need": d, "dimension": d, "priority": "medium"} for d in plan.get("dimensions", [])
        ]
        facts = evidence_agent.extract_facts(
            needs, units_by_material, filenames,
            cm=self.cm, insights=insights,
            required_facts=plan.get("required_facts", []),
            task_id=self.task_id,
            start_dimension=start_dimension,
            progress_callback=lambda done, total: self._update(evidence_progress={"done": done, "total": total}),
        )
        # A batch can persist Facts before a later batch fails. Recover those
        # task-owned rows on resume instead of relying only on the final task
        # payload update, which is intentionally written after extraction.
        with session_scope() as s:
            persisted_ids = [
                int(value)
                for value in s.execute(
                    select(ORMFact.c.id).where(ORMFact.c.task_id == self.task_id)
                ).scalars().all()
            ]
        existing_ids = [int(x) for x in (self.task.get("fact_ids") or [])]
        new_ids = [int(f.id) for f in facts if f.id is not None]
        recovered_ids = list(dict.fromkeys(existing_ids + persisted_ids + new_ids))
        self._update(fact_ids=recovered_ids)
        if self.task.get("incremental_update"):
            prior_new_ids = [int(x) for x in (self.task.get("incremental_new_fact_ids") or [])]
            self._update(incremental_new_fact_ids=list(dict.fromkeys(prior_new_ids + persisted_ids + new_ids)))
        fact_payload = [{"id": f.id, "content": f.content, "sources": load_evidence_quotes(f.id)} for f in facts]
        # Intelligence Consolidation:Fact 聚簇(多源印证)+ Ledger 情报底稿
        self._consolidate_intelligence(facts)
        return fact_payload

    def reflow_evidence_gaps(self) -> list[dict]:
        """Explicit WriteHERE boundary: retrieve open writing gaps, then refresh ledger.

        This is intentionally bounded to the gap contract and does not restart
        parsing or planner stages. A caller may invoke it after QA and then
        request a local chapter rewrite.
        """
        task = self.task or {}
        gaps = task.get("qa_evidence_gaps") or []
        if not gaps:
            return []
        from app.context import ContextManager
        from app.evidence.extractor import EvidenceAgent
        units_by_material, filenames = self._load_units()
        cm = ContextManager(task, units_by_material, filenames)
        needs = [{"need": str(g), "dimension": "writing_evidence_gap", "priority": "high"} for g in gaps if str(g).strip()]
        facts = EvidenceAgent().extract_facts(
            needs, units_by_material, filenames, cm=cm,
            insights=task.get("material_insights", []),
            task_id=self.task_id,
        )
        self._update(evidence_gap_contract={"status": "retrieved", "gaps": gaps,
                                            "new_fact_ids": [int(f.id) for f in facts if f.id is not None]})
        return [{"id": f.id, "content": f.content} for f in facts]


    def _consolidate_intelligence(self, facts) -> None:
        """Fact Consolidation + Intelligence Ledger(多源印证/覆盖/缺口/置信度)。

        Analysis 读 Ledger 而非散乱 Fact;重复/印证语义在此层处理(非向量阈值)。
        """
        try:
            from app.intelligence.consolidate import cluster_facts, save_cluster
            from app.intelligence.ledger import build_ledger, persist_ledger
            from app.evidence.extractor import load_evidence_quotes
            plan = self._plan()
            needs = plan.get("evidence_needs") or [
                {"need": d, "dimension": d, "priority": "medium"} for d in plan.get("dimensions", [])
            ]
            fact_rows = [
                {"id": int(f.id), "content": f.content, "dimension": f.dimension or "",
                 "need_id": int(getattr(f, "need_id", 0) or 0),
                 "sources": load_evidence_quotes(f.id) if f.id else []}
                for f in facts if f.id is not None
            ]
            clusters = cluster_facts(fact_rows)
            for cluster in clusters:
                save_cluster(cluster, self.task_id)
            # 关系判定(确定性冲突检测:同主题不同数值 → contradicts 落库)
            from app.intelligence.consolidate import resolve_relations
            resolve_relations(clusters, fact_rows, self.task_id)
            ledger = build_ledger(self.task_id, needs, fact_rows)
            persist_ledger(self.task_id, ledger)
            self._update(intelligence_ledger=ledger)
        except Exception:
            pass  # 整编失败不阻塞主流程(evidence 本身已落库)

    def _build_task_graph(self, facts: list[dict]) -> dict:
        """Create an evidence-grounded task graph and publish an outbox event."""
        try:
            from app.graph import graph_service

            graph_facts = facts
            if self.task.get("incremental_update"):
                # Incremental runs only construct graph deltas for newly
                # extracted facts. Inherited facts already have their task
                # memberships and must not incur another extraction pass.
                new_ids = {int(value) for value in self.task.get("incremental_new_fact_ids") or [] if str(value).isdigit()}
                graph_facts = [fact for fact in facts if int(fact.get("id") or 0) in new_ids]
                if not graph_facts:
                    if graph_service.has_task_graph(self.task_id):
                        status = {"status": "reused", **(graph_service.task_graph(self.task_id).get("stats") or {})}
                        self._update(graph_status=status)
                        return status
                    graph_facts = facts  # one-time backfill for pre-graph tasks
            result = graph_service.build_task_graph(
                self.task_id,
                graph_facts,
                workspace_id=str(self.task.get("workspace_id") or ""),
                active_fact_ids={int(fact.get("id") or 0) for fact in facts if fact.get("id") is not None},
            )
            status = result.as_dict()
            self._update(graph_status=status)
            return status
        except Exception as exc:
            status = {"status": "degraded", "error": str(exc)[:300]}
            self._update(graph_status=status)
            return status


    def detect_conflicts(self) -> None:
        self._update(stage=str(Stage.CONFLICT))
        from app.evidence.extractor import load_claims_for_tasks
        from app.intelligence.consolidate import save_fact_relation

        inherited_ids = [int(i) for i in self.task.get("incremental_inherited_conflict_ids") or []]
        if self.task.get("incremental_update") and not self.task.get("incremental_new_fact_ids"):
            self._update(conflict_ids=inherited_ids)
            return
        claim_task_ids = [self.task_id]
        base_task_id = str(self.task.get("incremental_base_task_id") or "")
        if self.task.get("incremental_update") and base_task_id and base_task_id != self.task_id:
            claim_task_ids.append(base_task_id)
        claims = load_claims_for_tasks(claim_task_ids)
        conflicts = evidence_agent.detect_conflicts(claims, task_id=self.task_id)
        new_ids = [int(c.id) for c in conflicts if c.id is not None]
        self._update(conflict_ids=list(dict.fromkeys(inherited_ids + new_ids)))
        # Only comparable, direct contradictions become graph relations.
        # Qualifications and scope/time differences remain review records so
        # the graph cannot turn epistemic nuance into a false contradiction.
        claim_fact_ids = {
            int(claim.get("id")): int(claim.get("fact_id"))
            for claim in claims
            if str(claim.get("id") or "").isdigit()
            and str(claim.get("fact_id") or "").isdigit()
            and int(claim.get("fact_id") or 0) > 0
        }
        for conflict in conflicts:
            if conflict.conflict_type != "direct_contradiction":
                continue
            fact_ids = list(dict.fromkeys(
                claim_fact_ids[claim_id]
                for claim_id in (conflict.claim_ids or [])
                if claim_id in claim_fact_ids
            ))
            for index, source_id in enumerate(fact_ids):
                for target_id in fact_ids[index + 1:]:
                    save_fact_relation(
                        source_id, target_id, "contradicts", self.task_id,
                        evidence_quote=conflict.fact_key,
                    )

    def analyze(self, facts: list[dict]) -> None:
        self._update(stage=str(Stage.ANALYSIS))
        if not facts:
            if self.task.get("incremental_update"):
                self._update(
                    analysis_done=True,
                    incremental_generated_inference_ids=[],
                    incremental_generated_external_ids=[],
                )
            return
        conflicts = self._conflicts()
        memory_block = self._knowledge_block()
        timeline_block = self._timeline_block()
        # 情报底稿:Analysis 读 Ledger(聚簇/关系/覆盖/缺口),而非散乱 Fact
        ledger = self.task.get("intelligence_ledger") or {}
        ledger_block = ""
        if ledger:
            gaps = "、".join(str(g) for g in (ledger.get("information_gaps") or [])[:6])
            clusters = "; ".join(
                f"{c.get('key', '')[:40]}({c.get('status', '')})"
                for c in (ledger.get("fact_clusters") or [])[:6]
            )
            relations = "; ".join(
                f"{r.get('relation_type', '')}" for r in (ledger.get("fact_relations") or [])[:6]
            )
            ledger_block = (
                f"情报底稿(供分析参考):\n"
                f"- 信息缺口:{gaps or '无'}\n"
                f"- 事实聚簇:{clusters or '无'}\n"
                f"- 事实关系:{relations or '无'}\n"
            )
        # Map:仅在 Analysis 阶段补充通用元数据,避免影响后续 Planner/Writer。
        analysis_facts = self._facts_for_analysis(facts)
        groups = _analysis_groups(analysis_facts)
        local_inferences: list = []
        for dimension, group_facts in groups.items():
            context_block = self.cm.for_analysis(group_facts, conflicts, timeline_block, memory_block) if self.cm else ""
            try:
                from app.graph import graph_service
                graph_block = graph_service.context_for_analysis(self.task_id, group_facts)
                if graph_block:
                    context_block = f"{graph_block}\n\n{context_block}"
            except Exception:
                pass
            if ledger_block:
                context_block = f"{ledger_block}\n{context_block}"
            local_inferences.extend(analysis_agent.analyze_local(dimension, group_facts, context_block))
        # Reduce:跨维度综合(局部推断 → 全局推断 + 覆盖状态元数据)
        global_inferences, external, global_meta = analysis_agent.analyze_global(
            local_inferences, facts,
            "、".join(str(c.get("description") or c.get("note") or "") for c in (conflicts or [])[:5]),
        )
        inferences = local_inferences + global_inferences
        generated_ids = [int(i.id) for i in inferences if i.id is not None]
        generated_external_ids = [int(i.id) for i in external if i.id is not None]
        if self.task.get("incremental_update"):
            inherited_ids = [int(i) for i in self.task.get("incremental_inherited_inference_ids") or []]
            inherited_external_ids = [int(i) for i in self.task.get("incremental_inherited_external_ids") or []]
            self._update(
                inference_ids=list(dict.fromkeys(inherited_ids + generated_ids)),
                external_ids=list(dict.fromkeys(inherited_external_ids + generated_external_ids)),
                incremental_generated_inference_ids=generated_ids,
                incremental_generated_external_ids=generated_external_ids,
                analysis_global_meta=global_meta,
            )
        else:
            self._update(
                inference_ids=generated_ids,
                external_ids=generated_external_ids,
                analysis_global_meta=global_meta,
            )
        self._update_fact_dispositions()
        self._update_coverage_audit()
        self._update(analysis_done=True)

    def _delete_task_inferences(self) -> None:
        """删除本任务全部推断(分析中断后重跑前的幂等清理)。"""
        try:
            with session_scope() as s:
                call_ids = select(ORMLLMCall.c.call_id).where(ORMLLMCall.c.task_id == self.task_id)
                inf_ids = [r["id"] for r in s.execute(
                    select(ORMInference.c.id).where(ORMInference.c.origin_call_id.in_(call_ids))
                ).mappings().all()]
                if inf_ids:
                    inference_fact = Base.metadata.tables["inference_fact"]
                    s.execute(
                        delete(inference_fact).where(inference_fact.c.inference_id.in_(inf_ids))
                    )
                    s.execute(delete(ORMInference).where(ORMInference.c.id.in_(inf_ids)))
        except Exception:
            pass

    def _delete_inferences_by_ids(self, inference_ids: list[int]) -> None:
        """删除当前增量轮次的推断,保留从基线版本继承的推断。"""
        ids = [int(i) for i in inference_ids if str(i).isdigit()]
        if not ids:
            return
        try:
            with session_scope() as s:
                inference_fact = Base.metadata.tables["inference_fact"]
                s.execute(delete(inference_fact).where(inference_fact.c.inference_id.in_(ids)))
                s.execute(delete(ORMInference).where(ORMInference.c.id.in_(ids)))
        except Exception:
            pass


    def _update_fact_dispositions(self) -> None:
        """Fact 去向标记(规格:每条有效 Fact 必须有明确去向)。"""
        try:
            from app.retrieval.rag import _ngram_similarity

            facts = self._facts()
            inferences = self._inferences()
            used_ids = {int(fid) for inf in inferences for fid in (inf.get("based_fact_ids") or [])}
            theme = self.task.get("theme") or ""
            with session_scope() as s:
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
                    s.execute(update(ORMFact).where(ORMFact.c.id == fid).values(disposition=disp))
        except Exception:
            pass

    def _update_coverage_audit(self) -> None:
        """三类覆盖可观测性:材料覆盖 / 维度覆盖 / 需求覆盖(规格 5/9)。"""
        try:
            # 材料扫描状态读独立表(evidence 阶段写入,避免 task payload 膨胀)
            with session_scope() as s:
                scan_rows = s.execute(
                    select(
                        ORMMaterialScan.c.material_id, ORMMaterialScan.c.scanned,
                        ORMMaterialScan.c.units_selected, ORMMaterialScan.c.fact_count,
                    ).where(ORMMaterialScan.c.task_id == self.task_id)
                ).mappings().all()
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
            with session_scope() as s:
                fact_ids = [int(f["id"]) for f in facts if f.get("id") is not None]
                if fact_ids:
                    ev_rows = s.execute(
                        select(
                            ORMEvidence.c.fact_id,
                            func.count(func.distinct(ORMEvidence.c.material_id)).label("c"),
                        )
                        .where(ORMEvidence.c.fact_id.in_(fact_ids))
                        .group_by(ORMEvidence.c.fact_id)
                    ).mappings().all()
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

    def _conflicts(self) -> list[dict]:
        conflict_ids = [int(value) for value in self.task.get("conflict_ids", []) if str(value).isdigit()]
        if not conflict_ids:
            return []
        from app.evidence.extractor import load_conflict_records
        return load_conflict_records(conflict_ids)

    def _knowledge_block(self) -> str:
        """Graph RAG is injected per analysis group; no stale global block."""
        return ""

    def _prepare_incremental_write_scope(self) -> list[str] | None:
        """Build the incremental write plan and gate major structure changes."""
        if not (self.task.get("incremental_update") and self.task.get("incremental_delta_id") and self.task.get("report_id")):
            return None
        impact = build_incremental_impact(
            int(self.task["report_id"]),
            int(self.task["incremental_delta_id"]),
            task_id=self.task_id,
        )
        plan = self._plan()
        instruction_policy = self._instruction_update_policy(plan)
        self.task["incremental_execution_policy"] = instruction_policy
        impact["execution_policy"] = instruction_policy
        impact_chapters = [str(title) for title in impact.get("rewrite_sections") or [] if str(title).strip()]
        instruction_chapters = [
            str(title) for title in instruction_policy.get("rewrite_sections") or [] if str(title).strip()
        ]
        target_chapters = list(dict.fromkeys([*impact_chapters, *instruction_chapters]))
        if self.task.get("intervention_rewrite_all"):
            target_chapters = [str(title) for title in (plan.get("structure") or []) if str(title).strip()]
            impact["rewrite_scope_reason"] = "approved_upstream_change_rewrite_all"
        elif not target_chapters:
            target_chapters = [str(title) for title in (plan.get("structure") or []) if str(title).strip()]
            impact["rewrite_scope_reason"] = "instruction_scope_uncertain_rewrite_all"
        elif impact_chapters and instruction_chapters:
            impact["rewrite_scope_reason"] = "material_impact_and_user_instruction_union"
        elif instruction_chapters:
            impact["rewrite_scope_reason"] = "update_instruction_selected_sections"
        else:
            impact["rewrite_scope_reason"] = "affected_sections_only"
        self._update(
            incremental_plan=impact,
            incremental_delta={**(self.task.get("incremental_delta") or {}), "impact": impact},
            incremental_execution_policy=instruction_policy,
        )
        if impact.get("requires_structure_review"):
            # Long-running tasks should not block waiting for a mid-run decision.
            # Generate a candidate draft, then expose a version diff for review.
            plan = self._plan()
            target_chapters = [str(title) for title in (plan.get("structure") or []) if str(title).strip()]
            impact["rewrite_scope_reason"] = "structure_changed_candidate_rewrite_all"
            impact["review_after_generation"] = True
            self._update(
                incremental_plan=impact,
                incremental_delta={**(self.task.get("incremental_delta") or {}), "impact": impact},
                incremental_structure_review_required=True,
            )
        return target_chapters

    def _instruction_update_policy(self, plan: dict) -> dict:
        """Let the model map update semantics; program code stays domain-neutral."""
        reason = str(self.task.get("incremental_update_reason") or "").strip()
        chapters = [
            {"title": chapter.get("title", ""), "purpose": chapter.get("judgment", "")}
            for chapter in (plan.get("chapter_plans") or []) if chapter.get("title")
        ]
        if not reason or not chapters:
            return {"rewrite_sections": [], "content_intent": "preserve", "reason": ""}
        from app.agents.base import BaseAgent

        agent = BaseAgent()
        system = (
            "根据用户本轮更新目标,从给定章节中选择确实需要重写的章节。"
            "不得创造章节名。若更新是全文语言、篇幅或整体结构调整,返回全部章节。"
            "同时判断内容意图:expand=扩充深化,preserve=保持规模的完善,condense=明确精简,"
            "restructure=允许重组。严格输出 JSON:"
            "{\"rewrite_sections\":[\"原章节标题\"],\"content_intent\":\"expand|preserve|condense|restructure\",\"reason\":\"简述\"}"
        )
        try:
            with self._token_context("incremental_scope"):
                payload = agent.generate_json(
                    f"本轮更新目标:{reason}\n现有章节:{json.dumps(chapters, ensure_ascii=False)}",
                    system=system,
                )
        except Exception:
            return {"rewrite_sections": [], "content_intent": "preserve", "reason": "scope_model_failed"}
        allowed = {str(item["title"]) for item in chapters}
        intent = str(payload.get("content_intent") or "preserve").lower()
        if intent not in {"expand", "preserve", "condense", "restructure"}:
            intent = "preserve"
        return {
            "rewrite_sections": [
                str(title) for title in payload.get("rewrite_sections") or [] if str(title) in allowed
            ],
            "content_intent": intent,
            "reason": str(payload.get("reason") or ""),
        }

    def write(self, target_chapter_titles: list[str] | None = None) -> None:
        self._update(stage=str(Stage.WRITING), write_progress={
            "done": 0,
            "total": 0,
            "chapter": "",
            "status": "starting",
            "elapsed_seconds": 0,
        })
        from app.planning.scale import normalize_execution_plan

        plan = normalize_execution_plan(self._plan())
        if self.task.get("incremental_update_reason"):
            plan = {**plan, "update_instruction": str(self.task.get("incremental_update_reason") or "")}
        facts = self._facts()
        inferences = self._inferences()
        variant = self._selected_variant()
        # Editorial examples are selected after Narrative Plan exists, where
        # chapter/subsection purpose is known. Keep only non-style runtime
        # context here to avoid injecting one generic example set everywhere.
        style_block = ""
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
            target_chapter_titles=target_chapter_titles,
            run_id=str(self.task.get("run_id") or ""),
        )
        self._update(report_id=report.id)
        # 验证闭环:只执行确定性、可逆修复；事实与数字疑点保留原文并进入复核。
        self._auto_revision(report.id, plan, facts, inferences, variant)
        self._normalize_report_order(report.id, plan)
        # 规模控制:超 hard_max 局部压缩(删重复/过渡句,不硬截断)+ 规模统计
        self._run_quality_check(variant, plan, report.id)
        if self._auto_quality_fix(report.id, plan):
            self._normalize_report_order(report.id, plan)
        self._run_quality_check(variant, plan, report.id)

        # QA 闭环:四类质检 + Repair 路由 + 缺口回流
        self._run_report_qa(report.id, plan, facts)


    def _run_report_qa(self, report_id: int, plan: dict, facts: list[dict]) -> None:
        """写作后 QA:逐章四类质检(一次 LLM 调用/章)→ Repair Router → 缺口回流。

        QA 只发现问题;修复动作由程序路由(记录);证据缺口回流到
        Intelligence Ledger 的 information_gaps(不自动编造)。
        """
        try:
            from app.qa.qa import ReportQA, route_repairs
            qa_agent = ReportQA()
            qa_results: list[dict] = []
            repair_actions: list[dict] = []
            with session_scope() as s:
                sentence_rows = s.execute(
                    select(ORMSentence.c.section, ORMSentence.c.content, ORMSentence.c.source_refs)
                    .where(ORMSentence.c.report_id == report_id)
                    .order_by(ORMSentence.c.position, ORMSentence.c.id)
                ).mappings().all()
            sections: dict[str, dict] = {}
            for row in sentence_rows:
                section = sections.setdefault(str(row["section"] or ""), {
                    "section": str(row["section"] or ""), "texts": [], "fact_ids": set(), "inference_ids": set(),
                })
                section["texts"].append(str(row["content"] or ""))
                try:
                    refs = json.loads(row["source_refs"] or "{}")
                except (TypeError, ValueError):
                    refs = {}
                section["fact_ids"].update(int(x) for x in refs.get("fact_ids", []) if str(x).isdigit())
                section["inference_ids"].update(int(x) for x in refs.get("inference_ids", []) if str(x).isdigit())
            for section in sections.values():
                result = qa_agent.check(
                    {"content": "\n".join(section["texts"]),
                     "fact_ids": sorted(section["fact_ids"]),
                     "inference_ids": sorted(section["inference_ids"])},
                    facts=facts,
                )
                qa_results.append({"section": section["section"], "qa": result})
                for action in route_repairs(result):
                    action["section"] = section["section"]
                    repair_actions.append(action)
            evidence_gaps = [
                a.get("reason", "") for a in repair_actions
                if a.get("action") in ("attribution_repair", "narrative_rewrite")
            ]
            from app.planning.structure import evidence_gap_contract, evaluation_contract
            gap_contract = evidence_gap_contract(evidence_gaps)
            self._update(qa_results=qa_results, repair_actions=repair_actions,
                         qa_evidence_gaps=evidence_gaps,
                         evidence_gap_contract=gap_contract,
                         evaluation_contract=evaluation_contract())
            # Repair 真执行(不编造):引用问题 → 标记无依据句子(edit_history 留痕,供人工复核)
            repaired = 0
            for action in repair_actions:
                if action.get("action") == "attribution_repair":
                    with session_scope() as s:
                        rows = s.execute(
                            select(
                                ORMSentence.c.id, ORMSentence.c.content, ORMSentence.c.source_refs
                            )
                            .where(
                                ORMSentence.c.report_id == report_id,
                                ORMSentence.c.section == action.get("section", ""),
                                ORMSentence.c.source_refs.in_(["{}", ""]),
                            )
                        ).mappings().all()
                        for row in rows:
                            s.execute(
                                update(ORMSentence)
                                .where(ORMSentence.c.id == row["id"])
                                .values(
                                    edit_history=func.json_patch(
                                        ORMSentence.c.edit_history,
                                        '[{"qa": "attribution_repair", "note": "无引用句子,待人工复核"}]',
                                    )
                                )
                            )
                            repaired += 1
            self._update(qa_repaired_sentences=repaired)
        except Exception:
            pass  # QA 失败不阻塞交付(报告已生成)


    def _record_chapter_artifact(self, payload: dict) -> None:
        # 每章写毕的边界:检查暂停(不打断单次 LLM 调用,只在章/章之间暂停)
        self._control_boundary()
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

    def _timeline_block(self) -> str:
        """Timeline context is supplied by evidence-grounded Graph RAG when active."""
        return ""

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
        with session_scope() as s:
            rows = s.execute(select(
                ORMSentence.c.section, ORMSentence.c.content, ORMSentence.c.user_edit,
            ).where(
                ORMSentence.c.report_id == report_id, ORMSentence.c.selected == 1,
            )).mappings().all()
        from app.writing.scale_execution import measure_text_words

        chapter_parts: dict[str, list[str]] = {}
        for row in rows:
            chapter_parts.setdefault(str(row["section"]), []).append(str(row["user_edit"] or row["content"] or ""))
        actual_by_chapter = {
            section: measure_text_words("".join(parts)) for section, parts in chapter_parts.items()
        }
        execution_by_chapter: dict[str, dict] = {}
        with session_scope() as s:
            execution_rows = s.execute(
                select(ORMTaskArtifact.c.payload).where(
                    ORMTaskArtifact.c.task_id == self.task_id,
                    ORMTaskArtifact.c.run_id == str(self.task.get("run_id") or ""),
                    ORMTaskArtifact.c.stage.like("chapter_draft:%"),
                ).order_by(ORMTaskArtifact.c.id)
            ).mappings().all()
        for row in execution_rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError):
                continue
            chapter_name = str(payload.get("chapter") or "")
            if chapter_name:
                execution_by_chapter[chapter_name] = payload
        chapter_stats = []
        for chapter in plan.get("chapter_plans") or []:
            target = int(chapter.get("target_words") or 0)
            chapter_name = str(chapter.get("title") or "")
            actual = int(actual_by_chapter.get(chapter_name, 0))
            execution = execution_by_chapter.get(chapter_name) or {}
            unit_stats = list(execution.get("generation_unit_stats") or [])
            chapter_stats.append({
                "chapter": chapter_name,
                "target_words": target,
                "actual_words": actual,
                "completion_rate": round(actual / target, 4) if target else None,
                "generation_units": int(execution.get("generation_units") or 0),
                "generation_calls": int(execution.get("generation_calls") or 0),
                "generation_unit_stats": unit_stats,
                "planned_fact_count": sum(int(item.get("planned_fact_count") or 0) for item in unit_stats),
                "used_fact_count": sum(int(item.get("used_fact_count") or 0) for item in unit_stats),
                "unused_planned_fact_count": sum(
                    len(item.get("unused_planned_fact_ids") or []) for item in unit_stats
                ),
                "underfill_reason": str(execution.get("underfill_reason") or ""),
                "generated_words": int(execution.get("actual_words") or actual),
                "previous_words": int(execution.get("previous_words") or 0),
            })
        budget = dict(plan.get("budget") or {})
        target_words = int(budget.get("target_words") or sum(item["target_words"] for item in chapter_stats))
        minimum_words = int(
            budget.get("min_words")
            or round(target_words * settings.writer_min_budget_completion_ratio)
        ) if target_words else 0
        actual_words = sum(actual_by_chapter.values())
        completion_rate = round(actual_words / max(target_words, 1), 4) if target_words else 0
        planned_fact_total = sum(item["planned_fact_count"] for item in chapter_stats)
        unused_planned_fact_total = sum(item["unused_planned_fact_count"] for item in chapter_stats)
        planned_fact_coverage = (
            round((planned_fact_total - unused_planned_fact_total) / planned_fact_total, 4)
            if planned_fact_total else None
        )
        if minimum_words and actual_words < minimum_words:
            issues.append({
                "type": "SCALE_UNDERFILL", "severity": "high", "section": "", "quote": "",
                "note": f"正文 {actual_words} 字，仅完成最终规模计划 {target_words} 字的 {completion_rate:.0%}",
                "underfill_reason": budget.get("underfill_reason") or "Writer 未充分执行最终规模计划，需复核证据容量与章节展开",
            })
        self._update(qa_notes=issues, report_stats={
            "target_words": target_words, "minimum_words": minimum_words, "actual_words": actual_words,
            "completion_rate": completion_rate, "chapters": chapter_stats,
            "planned_fact_coverage": planned_fact_coverage,
            "evidence_status": budget.get("evidence_status", ""),
            "underfill_reason": budget.get("underfill_reason", ""),
        })

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
        """Hide only exact duplicate rows; semantic repetition remains a QA issue."""
        with session_scope() as s:
            rows = s.execute(
                select(
                    ORMSentence.c.id, ORMSentence.c.section, ORMSentence.c.paragraph,
                    ORMSentence.c.content, ORMSentence.c.source_refs,
                )
                .where(ORMSentence.c.report_id == report_id, ORMSentence.c.selected == 1)
                .order_by(ORMSentence.c.position, ORMSentence.c.id)
            ).mappings().all()
        section_counts: dict[str, int] = {}
        for row in rows:
            section_counts[row["section"]] = section_counts.get(row["section"], 0) + 1
        seen: set[tuple[str, str, str]] = set()
        hide_ids: list[int] = []
        for row in rows:
            if section_counts.get(row["section"], 0) <= 2:
                continue
            try:
                refs = json.loads(row["source_refs"] or "{}")
            except (TypeError, ValueError):
                refs = {}
            identity = (
                str(row["section"] or ""),
                re.sub(r"\s+", "", str(row["content"] or "")),
                json.dumps(refs, ensure_ascii=False, sort_keys=True),
            )
            if identity in seen:
                hide_ids.append(int(row["id"]))
                section_counts[row["section"]] -= 1
            else:
                seen.add(identity)
        if hide_ids:
            with session_scope() as s:
                s.execute(
                    update(ORMSentence).where(ORMSentence.c.id.in_(hide_ids)).values(selected=0)
                )
        return len(hide_ids)

    def _auto_revision(self, report_id: int, plan: dict, facts: list[dict],
                       inferences: list[dict], variant) -> None:
        """Record semantic risks without silently rewriting report meaning."""
        import json as _json
        import re as _re

        digit_re = _re.compile(r"\d+(?:\.\d+)?")
        fact_by_id = {f["id"]: f["content"] for f in facts}
        with session_scope() as s:
            rows = s.execute(
                select(
                    ORMSentence.c.id, ORMSentence.c.section, ORMSentence.c.content,
                    ORMSentence.c.source_refs, ORMSentence.c.source_level,
                )
                .where(ORMSentence.c.report_id == report_id)
                .order_by(ORMSentence.c.position)
            ).mappings().all()
            # 数字基准 = fact 内容 ∪ 对应 evidence 原文片段(fact 提炼句常不含数字)
            fact_ids = [int(f["id"]) for f in facts if f.get("id") is not None]
            if fact_ids:
                ev_rows = s.execute(
                    select(ORMEvidence.c.fact_id, ORMEvidence.c.quote)
                    .where(ORMEvidence.c.fact_id.in_(fact_ids))
                ).mappings().all()
            else:
                ev_rows = []
        quote_by_fact: dict[int, list[str]] = {}
        for ev in ev_rows:
            quote_by_fact.setdefault(ev["fact_id"], []).append(ev["quote"])
        untraced_issues = 0
        numeric_issues = 0
        template_meta_issues = 0
        for row in rows:
            try:
                refs = _json.loads(row["source_refs"] or "{}")
            except (TypeError, ValueError):
                refs = {}
            fact_ids = [int(x) for x in (refs.get("fact_ids") or []) if str(x).isdigit()]
            inf_ids = [int(x) for x in (refs.get("inference_ids") or []) if str(x).isdigit()]
            if not fact_ids and not inf_ids:
                if row["source_level"] in {"TRANSITION", "SUBHEADING"}:
                    continue
                untraced_issues += 1
                continue
            if (
                (self.task.get("task_profile") or {}).get("report_mode") == "requirement_summary"
                and looks_like_template_meta(row["content"])
            ):
                template_meta_issues += 1
                continue
            sentence_digits = set(digit_re.findall(row["content"]))
            fact_digits: set[str] = set()
            for fid in fact_ids:
                fact_digits |= set(digit_re.findall(fact_by_id.get(fid, "")))
                for quote in quote_by_fact.get(fid, []):
                    fact_digits |= set(digit_re.findall(quote))
            missing = {d for d in sentence_digits - fact_digits if len(d) >= 2}
            if missing:
                numeric_issues += 1
        self._update(auto_revision={
            "deterministic_fixes": 0,
            "untraced_issues": untraced_issues,
            "numeric_issues": numeric_issues,
            "template_meta_issues": template_meta_issues,
        })

    def _normalize_report_order(self, report_id: int, plan: dict) -> None:
        order = {
            str(title): index
            for index, title in enumerate(plan.get("structure") or [], start=1)
            if str(title).strip()
        }
        if not order:
            return
        with session_scope() as s:
            rows = s.execute(
                select(ORMSentence.c.id, ORMSentence.c.section)
                .where(ORMSentence.c.report_id == report_id)
                .order_by(ORMSentence.c.position, ORMSentence.c.id)
            ).mappings().all()
            stale_ids = [int(row["id"]) for row in rows if str(row["section"]) not in order]
            if stale_ids:
                # Historical content belongs in immutable report versions, not
                # behind the current draft at a synthetic sort position.
                s.execute(delete(ORMSentenceFact).where(ORMSentenceFact.c.sentence_id.in_(stale_ids)))
                s.execute(delete(ORMSentenceInference).where(ORMSentenceInference.c.sentence_id.in_(stale_ids)))
                s.execute(delete(ORMSentence).where(ORMSentence.c.id.in_(stale_ids)))
            counters: dict[str, int] = {}
            for row in rows:
                section = str(row["section"])
                if section not in order:
                    continue
                counters[section] = counters.get(section, 0) + 1
                s.execute(
                    update(ORMSentence)
                    .where(ORMSentence.c.id == row["id"])
                    .values(position=_chapter_position(order[section], counters[section]))
                )

    def _sink_knowledge(self, facts: list[dict]) -> dict:
        """Publish reviewed task assertions into the workspace graph.

        Build the evidence-grounded graph after TTFR when it was intentionally
        kept off the critical path, then project its validated assertions.
        """
        from app.graph import graph_service

        if not settings.graph_build_before_analysis and not graph_service.has_task_graph(self.task_id):
            with self._token_context("graph_build"):
                graph_status = self._build_task_graph(facts)
        else:
            graph_status = dict(self.task.get("graph_status") or {})
            if graph_service.has_task_graph(self.task_id) and graph_status.get("status") not in {"ready", "partial_ready"}:
                stats = graph_service.task_graph(self.task_id).get("stats") or {}
                graph_status = {"status": "reused", **stats}
                self._update(graph_status=graph_status)

        # Review has not yet been accepted by a human. Keep assertions
        # validated and projectable for this task, but do not publish them into
        # the workspace's confirmed long-term graph until finalize().
        has_graph = graph_service.has_task_graph(self.task_id)
        projected = graph_service.project_pending() if has_graph else 0
        self._record_artifact(
            "graph", {"graph_status": graph_status},
            status=_graph_artifact_status(graph_status),
        )
        self._update(knowledge_stats={
            "validated_task_graph": has_graph,
            "graph_status": graph_status.get("status", "unknown"),
            "projected_events": projected,
        })
        return graph_status

    def _start_background_post_review(self, facts: list[dict]) -> None:
        """Run non-critical post-review jobs without delaying TTFR."""
        import time as _time

        jobs = dict(self.task.get("background_jobs") or {})
        jobs["knowledge"] = {"status": "queued", "queued_at": round(_time.time(), 1)}
        self._update(background_jobs=jobs)

        def _run() -> None:
            started = _time.time()
            try:
                jobs = dict(self.task.get("background_jobs") or {})
                jobs["knowledge"] = {"status": "running", "started_at": round(started, 1)}
                self._update(background_jobs=jobs)
                with llm_priority(PRIORITY_BACKGROUND), self._token_context("knowledge"):
                    graph_status = self._sink_knowledge(facts)
                graph_state = str(graph_status.get("status") or "unknown")
                jobs = dict(self.task.get("background_jobs") or {})
                jobs["knowledge"] = {
                    "status": (
                        "done" if graph_state in {"ready", "reused", "skipped"}
                        else "partial" if graph_state == "partial_ready"
                        else "failed"
                    ),
                    "duration_seconds": round(_time.time() - started, 1),
                    "graph_status": graph_state,
                    "error": graph_status.get("error", ""),
                }
                self._update(background_jobs=jobs)
            except Exception as exc:
                jobs = dict(self.task.get("background_jobs") or {})
                jobs["knowledge"] = {
                    "status": "failed",
                    "duration_seconds": round(_time.time() - started, 1),
                    "error": str(exc),
                }
                self._update(background_jobs=jobs)

        threading.Thread(target=_run, daemon=True).start()

    def finalize(self) -> None:
        """审核完成后发布确认断言并生成最终报告版本。"""
        report_id = self.task.get("report_id")
        version_info = {}
        if report_id is not None:
            version = ensure_report_version(
                int(report_id),
                task_id=self.task_id,
                status="final",
                change_summary="完成审核生成版本快照",
            )
            version_info = {"current_version_id": version.version_id, "current_version_no": version.version_no}
            if self.task.get("incremental_delta_id"):
                try:
                    attach_delta_version(int(self.task["incremental_delta_id"]), version.version_id, status="applied")
                except Exception:
                    pass
            try:
                from app.graph import graph_service
                published = graph_service.promote_task_graph(self.task_id, version.version_id)
                projected = graph_service.project_pending()
                version_info["published_graph_assertions"] = published
                version_info["projected_graph_events"] = projected
            except Exception as exc:
                version_info["graph_publish_error"] = str(exc)[:200]
        self._update(stage=str(Stage.DONE), report_version=version_info)

    # ---------- 辅助 ----------

    def _record_artifact(self, stage: str, payload: dict, status: str = "done") -> None:
        """Record a task-scoped stage artifact for audit and breakpoint resume."""
        effective_inputs = {
            "run_revision": int(self.task.get("run_revision") or 1),
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
            input_hash = save_task_artifact(
                self.task_id, stage, effective_inputs, payload, status=status,
                run_id=str(self.task.get("run_id") or ""),
            )
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
            run_id=str(self.task.get("run_id") or ""),
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
        with session_scope() as s:
            rows = s.execute(
                select(ORMTaskArtifact.c.payload)
                .where(
                    ORMTaskArtifact.c.task_id == self.task_id,
                    ORMTaskArtifact.c.stage.like("chapter_draft:%"),
                    ORMTaskArtifact.c.status == "done",
                )
            ).mappings().all()
        actual = set()
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError):
                continue
            if payload.get("status") == "done" and payload.get("chapter"):
                actual.add(str(payload.get("chapter")))
        return set(expected) <= actual

    def _plan(self) -> dict:
        plan_id = self.task.get("plan_id")
        with session_scope() as s:
            row = s.execute(select(ORMPlan).where(ORMPlan.c.id == plan_id)).mappings().first()
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
            "evidence_needs": _loads(row["evidence_needs"]) if "evidence_needs" in row.keys() else [],
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
        fact_ids = [int(fact_id) for fact_id in self.task.get("fact_ids", []) if str(fact_id).isdigit()]
        if not fact_ids:
            return []
        with session_scope() as s:
            fact_rows = s.execute(select(ORMFact).where(ORMFact.c.id.in_(fact_ids))).mappings().all()
            evidence_rows = s.execute(select(ORMEvidence).where(ORMEvidence.c.fact_id.in_(fact_ids))).mappings().all()
        evidence_by_fact: dict[int, list[dict]] = {}
        for evidence in evidence_rows:
            evidence_by_fact.setdefault(int(evidence["fact_id"]), []).append(evidence)
        for row in fact_rows:
            if str(row.get("lifecycle_status") or "active") == "superseded":
                continue
            ev_rows = evidence_by_fact.get(int(row["id"]), [])
            source_roles = sorted({role_by_material.get(int(ev["material_id"]), "unknown") for ev in ev_rows})
            claim_supports = sorted({support_by_material.get(int(ev["material_id"]), "unknown") for ev in ev_rows})
            source_files = sorted({ev["source_file"] for ev in ev_rows if ev["source_file"]})
            result.append({
                "id": row["id"],
                "content": row["content"],
                "dimension": row.get("dimension") or "",
                "fact_type": row.get("fact_type") or "",
                "need_id": row.get("need_id") or 0,
                "sources": [ev["quote"] for ev in ev_rows if ev["quote"]],
                "source_roles": source_roles,
                "claim_supports": claim_supports,
                "source_files": source_files,
            })
        return result

    def _facts_for_analysis(self, facts: list[dict]) -> list[dict]:
        fact_ids = [int(f["id"]) for f in facts if f.get("id") is not None]
        if not fact_ids:
            return facts
        with session_scope() as s:
            rows = s.execute(
                select(ORMFact.c.id, ORMFact.c.dimension, ORMFact.c.fact_type, ORMFact.c.need_id)
                .where(ORMFact.c.id.in_(fact_ids))
            ).mappings().all()
        meta = {
            int(row["id"]): {
                "dimension": row["dimension"] or "",
                "fact_type": row["fact_type"] or "",
                "need_id": row["need_id"] or 0,
            }
            for row in rows
        }
        return [dict(fact, **meta.get(int(fact.get("id") or 0), {})) for fact in facts]

    def _inferences(self) -> list[dict]:
        inference_ids = [
            int(value) for value in self.task.get("inference_ids", []) + self.task.get("external_ids", [])
            if str(value).isdigit()
        ]
        if not inference_ids:
            return []
        with session_scope() as s:
            rows = s.execute(select(ORMInference).where(ORMInference.c.id.in_(inference_ids))).mappings().all()
        return [{
            "id": row["id"], "content": row["content"], "source_level": row["source_level"],
            "based_fact_ids": json.loads(row["based_fact_ids"] or "[]"),
            "dimension": row.get("dimension") or "",
            "analysis_type": row.get("analysis_type") or "",
            "confidence_level": row.get("confidence_level") or "medium",
            "confidence_reason": row.get("confidence_reason") or "",
            "uncertainty": row.get("uncertainty") or "",
        } for row in rows if str(row.get("lifecycle_status") or "active") == "active"]

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
        with session_scope() as s:
            for material_id in self.task.get("material_ids", []):
                material_id = int(material_id)
                material = s.execute(
                    select(ORMMaterial.c.filename).where(ORMMaterial.c.id == material_id)
                ).mappings().first()
                if material is not None:
                    filenames[material_id] = material["filename"]
                rows = s.execute(
                    select(ORMUnit).where(ORMUnit.c.material_id == material_id)
                    .order_by(ORMUnit.c.id)
                ).mappings().all()
                units_by_material[material_id] = [
                    Unit(
                        id=r["id"], material_id=r["material_id"], kind=r["kind"], content=r["content"],
                        page=r["page"], paragraph=r["paragraph"], image_desc=r["image_desc"],
                    )
                    for r in rows
                ]
        return units_by_material, filenames


def _resource_sample(stage: str, duration_seconds: float) -> dict:
    """Low-frequency resource snapshot at stage boundaries."""
    sample = {
        "stage": stage,
        "duration_seconds": round(float(duration_seconds or 0), 2),
        "cpu_percent": 0.0,
        "memory_percent": 0.0,
        "gpu": [],
    }
    try:
        import psutil
        sample["cpu_percent"] = round(float(psutil.cpu_percent(interval=0.05)), 1)
        sample["memory_percent"] = round(float(psutil.virtual_memory().percent), 1)
    except Exception:
        pass
    try:
        import subprocess
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2,
        )
        for line in out.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                used = int(float(parts[1]))
                total = int(float(parts[2]))
                sample["gpu"].append({
                    "util_percent": int(float(parts[0])),
                    "memory_used_mb": used,
                    "memory_total_mb": total,
                    "memory_free_mb": int(float(parts[3])) if len(parts) > 3 else max(total - used, 0),
                    "memory_percent": round(used / total * 100, 1) if total else 0.0,
                })
    except Exception:
        pass
    return sample


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
    file_summaries: list[dict] = []
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
        file_summaries.append({
            "material_id": int(item.get("material_id") or 0),
            "filename": filename,
            "format": ext,
            "status": status,
            "duration_seconds": round(duration, 2),
            "unit_count": units,
            "page_count": int(item.get("page_count") or 0),
            "text_count": int(item.get("text_count") or 0),
            "table_count": int(item.get("table_count") or 0),
            "image_count": int(item.get("image_count") or 0),
            "ocr_enabled": bool(item.get("ocr_enabled")),
            "parser": item.get("parser") or PARSER_VERSION,
            "error": str(item.get("error") or "")[:300],
            "embed_status": item.get("embed_status") or "",
        })
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
        "by_status": {
            "success": success,
            "reused": reused,
            "partial": partial,
            "failed": failed,
        },
        "file_summaries": file_summaries,
        "slowest_files": sorted(file_summaries, key=lambda item: item["duration_seconds"], reverse=True)[:10],
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
        with session_scope() as s:
            row = s.execute(
                select(
                    ORMInsight.c.material_id, ORMInsight.c.doc_type, ORMInsight.c.topic,
                    ORMInsight.c.key_sections, ORMInsight.c.key_points, ORMInsight.c.entities,
                    ORMInsight.c.times, ORMInsight.c.material_role, ORMInsight.c.claim_support,
                    ORMInsight.c.allowed_usage, ORMInsight.c.forbidden_usage,
                    ORMInsight.c.missing_information, ORMInsight.c.analysis_version,
                    ORMInsight.c.task_id,
                )
                .where(ORMInsight.c.material_id == int(material_id))
                .order_by(ORMInsight.c.id.desc())
                .limit(1)
            ).mappings().first()
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


def _material_understanding_text(units: list[Unit], budget_tokens: int = 4000,
                                 theme: str = "") -> tuple[str, dict]:
    """Select a bounded prompt view without truncating stored Units.

    采样原则(结构覆盖 + 任务相关性 + Token Budget):
    1. 结构锚点(标题/表格)全选——保证主要章节被代表性覆盖;
    2. 正文按任务主题相关度降序,预算内填充——任务重点优先;
    3. 预算不足时分批/截断只影响采样范围,不删存储数据。
    """
    non_empty = [u for u in units if (u.content or "").strip()]
    raw_chars = sum(len(u.content or "") for u in non_empty)
    raw_tokens = sum(count_tokens(u.content or "") for u in non_empty)
    if raw_tokens <= budget_tokens:
        text = "\n".join(u.content for u in non_empty)
        return text, {
            "raw_chars": raw_chars,
            "context_chars": len(text),
            "raw_tokens": raw_tokens,
            "context_tokens": count_tokens(text),
            "budget_tokens": budget_tokens,
            "utilization": round(count_tokens(text) / max(budget_tokens, 1), 4),
            "truncated": False,
            "selected_units": len(non_empty),
            "total_units": len(units),
        }
    anchors = [u for u in non_empty if u.kind in ("heading", "table")]
    body = [u for u in non_empty if u.kind not in ("heading", "table")]
    selected: list[Unit] = []
    total = 0
    # 任务相关性:正文按主题相关度降序(相关度高的先进入预算)
    if theme:
        body = sorted(body, key=lambda u: _text_relevance(theme, u.content or ""), reverse=True)
    for unit in [*anchors, *body]:
        block_tokens = count_tokens(unit.content or "")
        if total + block_tokens > budget_tokens:
            continue
        selected.append(unit)
        total += block_tokens
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
        "raw_tokens": raw_tokens,
        "context_tokens": count_tokens(text),
        "budget_tokens": budget_tokens,
        "utilization": round(count_tokens(text) / max(budget_tokens, 1), 4),
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
