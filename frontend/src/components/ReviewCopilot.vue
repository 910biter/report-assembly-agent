<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { api, jsonInit } from "@/api/http";

const props = defineProps<{
  taskId?: string;
  reportId?: number;
  artifactType: string;
  artifactVersion?: string;
  objectId?: string | number;
  current?: Record<string, any>;
  focus?: Record<string, any>;
  suggestedPrompts?: string[];
  compact?: boolean;
}>();
const emit = defineEmits<{ applied: [proposal: any] }>();
const thread = ref<any>(null);
const threads = ref<any[]>([]);
const message = ref("");
const pending = ref(false);
const loadingHistory = ref(false);
const error = ref("");
const historyOpen = ref(false);
const conversationStream = ref<HTMLElement | null>(null);
const expandedProposalIds = ref<Set<number>>(new Set());
const agentPending = computed(() => Boolean(thread.value?.pending));
const decisionSummary = computed(() => {
  const memory = thread.value?.decision_memory || {};
  const confirmed = (memory.confirmed_decisions || []).length;
  const waiting = (memory.pending_decisions || []).length;
  if (!confirmed && !waiting) return "";
  return `${confirmed ? `已确认 ${confirmed}` : ""}${confirmed && waiting ? " · " : ""}${waiting ? `待确认 ${waiting}` : ""}`;
});
const proposals = computed<any[]>(() => thread.value?.proposals || []);
const conversationItems = computed(() => {
  const messages = (thread.value?.messages || []).map((item: any) => ({
    ...item,
    itemType: "message",
  }));
  const linked = new Map<number, any[]>();
  const unlinked: any[] = [];
  for (const proposal of proposals.value) {
    let sourceId = Number(proposal?.impact?.source_message_id || 0);
    if (!sourceId) {
      const prior = [...messages]
        .reverse()
        .find(
          (item: any) =>
            item.role === "assistant" &&
            String(item.created_at || "") <= String(proposal.created_at || ""),
        );
      sourceId = Number(prior?.id || 0);
    }
    if (!sourceId) unlinked.push({ ...proposal, itemType: "proposal" });
    else
      linked.set(sourceId, [
        ...(linked.get(sourceId) || []),
        { ...proposal, itemType: "proposal" },
      ]);
  }
  const result: any[] = [];
  for (const item of messages) {
    result.push(
      item,
      ...(linked.get(Number(item.id)) || []).sort(
        (a, b) => Number(a.id) - Number(b.id),
      ),
    );
  }
  return [
    ...result,
    ...unlinked.sort((a, b) => {
      const byTime = String(a.created_at || "").localeCompare(
        String(b.created_at || ""),
      );
      return byTime || Number(a.id || 0) - Number(b.id || 0);
    }),
  ];
});
const readOnly = computed(() => thread.value?.status === "closed");
const scopeKey = computed(
  () =>
    `${props.taskId || "draft"}:${props.reportId || ""}:${props.artifactType}:${props.artifactVersion || ""}:${props.objectId || "root"}`,
);
const artifactLabels: Record<string, string> = {
  task_draft: "任务需求",
  task_brief: "任务需求",
  task_control: "协作对话",
  material_role: "材料理解",
  analysis_plan: "分析规划",
  fact: "事实",
  inference: "分析判断",
  final_plan: "报告目录",
  narrative_plan: "成文组织",
  report_title: "报告标题",
  section_title: "章节标题",
  paragraph: "正文段落",
  sentence: "正文句子",
  qa_issue: "质量问题",
  comparison_item: "增量对比",
};
let timer: number | undefined;

watch(
  scopeKey,
  async () => {
    thread.value = null;
    message.value = "";
    error.value = "";
    historyOpen.value = false;
    await loadThreads(true);
  },
  { immediate: true },
);
watch(
  () => conversationItems.value.length,
  async (next, previous) => {
    if (next <= previous) return;
    await nextTick();
    const element = conversationStream.value;
    if (element) element.scrollTop = element.scrollHeight;
  },
);
watch(agentPending, (isPending) => {
  if (!isPending && error.value.startsWith("助手仍在处理当前问题"))
    error.value = "";
});

timer = window.setInterval(async () => {
  if (
    !thread.value?.id ||
    (!thread.value.pending &&
      !(thread.value.proposals || []).some((item: any) =>
        ["waiting", "queued", "running"].includes(item.execution_status),
      ))
  )
    return;
  try {
    thread.value = await api(`/api/interactions/${thread.value.id}`);
    await loadThreads(false);
  } catch {
    /* Passive refresh must not interrupt editing. */
  }
}, 1800);
onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer);
});

function artifactLabel(item: any = thread.value) {
  return (
    artifactLabels[item?.artifact_type || props.artifactType] ||
    item?.artifact_type ||
    "当前产物"
  );
}
function historyUrl() {
  const params = new URLSearchParams();
  if (props.taskId) params.set("task_id", props.taskId);
  else if (props.artifactType === "task_draft" && props.objectId)
    params.set("draft_id", String(props.objectId));
  else if (props.reportId != null)
    params.set("report_id", String(props.reportId));
  return params.size ? `/api/interactions?${params.toString()}` : "";
}
function matchesCurrentScope(item: any) {
  return (
    item.status === "open" &&
    item.artifact_type === props.artifactType &&
    String(item.artifact_version || "") ===
      String(props.artifactVersion || "") &&
    String(item.object_id || "") === String(props.objectId || "")
  );
}
async function loadThreads(autoSelect: boolean) {
  const url = historyUrl();
  if (!url) return;
  loadingHistory.value = true;
  try {
    threads.value = await api<any[]>(url);
    if (autoSelect && !thread.value) {
      const active = threads.value.find(matchesCurrentScope);
      if (active) await selectThread(active, false);
    }
  } catch (e: any) {
    error.value = e.message || "会话历史加载失败";
  } finally {
    loadingHistory.value = false;
  }
}
async function selectThread(item: any, closeDrawer = true) {
  if (!item?.id || item.id === thread.value?.id) {
    if (closeDrawer) historyOpen.value = false;
    return;
  }
  try {
    thread.value = await api(`/api/interactions/${item.id}`);
    if (
      thread.value.artifact_type === "task_draft" &&
      thread.value.scope?.current
    ) {
      emit("applied", { after: thread.value.scope.current });
    }
    message.value = "";
    expandedProposalIds.value = new Set();
    if (closeDrawer) historyOpen.value = false;
  } catch (e: any) {
    error.value = e.message || "无法打开会话";
  }
}
function executionLabel(proposal: any) {
  return (
    (
      {
        waiting: "等待当前轮次结束",
        queued: "已进入后台队列",
        running: "正在后台重算",
        completed: "新版本已生成",
        failed: "后台处理失败",
      } as any
    )[proposal.execution_status] || "已记录"
  );
}
function proposalStatusLabel(proposal: any) {
  if (proposal?.status === "rejected") return "已拒绝";
  if (proposal?.status === "superseded") return "已被后续提案替代";
  return proposal?.status === "proposed" ? "待确认" : "已确认";
}
function proposalExpanded(proposal: any) {
  return (
    proposal?.artifact_type === "task_draft" ||
    expandedProposalIds.value.has(Number(proposal.id))
  );
}
function toggleProposal(proposal: any) {
  const next = new Set(expandedProposalIds.value);
  const id = Number(proposal.id);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  expandedProposalIds.value = next;
}
function formatTime(value: string) {
  if (!value) return "";
  const date = new Date(value.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  if (date.toDateString() === new Date().toDateString()) {
    return date.toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}
function diffValue(value: any, fallback: string) {
  if (value == null || value === "") return fallback;
  if (Array.isArray(value)) {
    const rows = value
      .map((item: any, index: number) => {
        if (item == null) return "";
        if (typeof item !== "object") return String(item);
        const content =
          item.display_title ||
          item.title ||
          item.content ||
          item.summary ||
          item.instruction;
        return content ? `${index + 1}. ${content}` : "";
      })
      .filter(Boolean);
    return rows.length ? rows.join("\n") : fallback;
  }
  if (typeof value === "object") {
    const rows = Object.entries(value).flatMap(([key, item]) => {
      const label = fieldLabels[key];
      if (!label || item == null || typeof item === "object") return [];
      return [`${label}：${String(item)}`];
    });
    return rows.length ? rows.join("\n") : fallback;
  }
  return String(value);
}
const fieldLabels: Record<string, string> = {
  theme: "报告主题",
  requirements: "报告要求",
  content: "内容",
  title: "标题",
  tool_name: "执行能力",
  arguments: "执行参数",
  instruction: "调整要求",
  required_structure: "目录结构",
  required_chapter_count: "章节数量",
  required_dimensions: "确认后的分析维度",
  required_dimension_count: "分析维度数量",
  material_role: "材料角色",
  claim_support: "事实边界",
  allowed_usage: "允许用途",
  forbidden_usage: "禁止用途",
  missing_information: "缺失信息",
  chapter_plans: "章节规划",
  narrative_logic: "叙事逻辑",
  budget: "规模预算",
  confidence_level: "置信度",
};
const toolLabels: Record<string, string> = {
  pause_task: "暂停任务",
  resume_task: "恢复任务",
  retry_task: "重试任务",
  revise_task_requirements: "更新任务目标与报告要求",
  revise_analysis_plan: "调整分析规划",
  recheck_fact: "复核事实与证据",
  recheck_inference: "复核分析判断",
  rewrite_sentence: "改写所选句子",
  rewrite_paragraph: "改写所选段落",
  update_report_title: "修改报告标题",
  update_section_title: "修改章节标题",
  update_section_titles: "批量修改章节标题",
  regenerate_chapter: "重新生成章节",
  rerun_final_plan: "重新规划报告结构",
};
const impactLabels: Record<string, string> = {
  material_analysis: "材料理解",
  analysis_plan: "分析规划",
  evidence: "事实与证据",
  conflict: "冲突核验",
  analysis: "综合分析",
  final_plan: "报告目录",
  narrative_plan: "成文组织",
  writing: "报告正文",
  qa: "质量检查",
  render: "文档导出",
  lineage_check: "溯源检查",
};
function impactText(proposal: any) {
  const policyLabel = String(proposal?.impact?.policy?.label || "").trim();
  if (policyLabel) return policyLabel;
  const values = (proposal?.impact?.invalidates || [])
    .map((item: string) => impactLabels[item])
    .filter(Boolean);
  return values.length ? values.join("、") : "仅检查当前内容";
}
function riskLabel(value: string) {
  return ({ low: "低", medium: "中", high: "高" } as any)[value] || "待评估";
}
function proposalDiffRows(proposal: any) {
  const after =
    proposal?.after && typeof proposal.after === "object" ? proposal.after : {};
  const before =
    proposal?.before && typeof proposal.before === "object"
      ? proposal.before
      : {};
  const isStructureChange =
    Array.isArray(after.required_structure) &&
    after.required_structure.length > 0;
  return Object.keys(after)
    .filter(
      (key) =>
        fieldLabels[key] && !(isStructureChange && key === "instruction"),
    )
    .map((key) => {
      const beforeValue =
        key === "instruction"
          ? before.content || before.title || before.instruction
          : key === "required_structure"
            ? before.structure || before.chapter_plans
            : before[key];
      const beforeChapterCount = Array.isArray(before.structure)
        ? before.structure.length
        : Array.isArray(before.chapter_plans)
          ? before.chapter_plans.length
          : 0;
      const beforeText =
        key === "required_chapter_count"
          ? beforeChapterCount
            ? `${beforeChapterCount} 章`
            : "尚无目录"
          : diffValue(
              beforeValue,
              key === "instruction" ? "当前选定范围" : "尚无内容",
            );
      const afterText =
        key === "required_chapter_count"
          ? `${Number(after[key]) || after.required_structure?.length || 0} 章`
          : key === "tool_name"
            ? toolLabels[String(after[key])] || String(after[key])
            : diffValue(after[key], "尚无内容");
      return {
        key,
        label: fieldLabels[key],
        before: beforeText,
        after: afterText,
      };
    });
}
function proposalToolName(proposal: any) {
  return String(proposal?.impact?.scope?.tool_call?.tool_name || "");
}
function proposalKind(proposal: any) {
  const tool = proposalToolName(proposal);
  if (tool === "rerun_final_plan") return "structure";
  if (tool === "update_section_titles") return "section_titles";
  if (tool === "revise_task_requirements") return "requirements";
  if (tool === "revise_analysis_plan") return "analysis";
  if (
    ["rewrite_sentence", "rewrite_paragraph", "regenerate_chapter"].includes(
      tool,
    )
  )
    return "content";
  if (["recheck_fact", "recheck_inference"].includes(tool))
    return "verification";
  if (["pause_task", "resume_task", "retry_task"].includes(tool))
    return "control";
  return "generic";
}
function titleList(value: any) {
  const source = Array.isArray(value?.structure)
    ? value.structure
    : Array.isArray(value?.chapter_plans)
      ? value.chapter_plans
      : [];
  return source
    .map((item: any) => (typeof item === "object" ? item?.title : item))
    .filter(Boolean);
}
function numberedText(values: any[]) {
  return values
    .map((item, index) => `${index + 1}. ${String(item)}`)
    .join("\n");
}
function proposalDisplayRows(proposal: any) {
  const before =
    proposal?.before && typeof proposal.before === "object"
      ? proposal.before
      : {};
  const after =
    proposal?.after && typeof proposal.after === "object" ? proposal.after : {};
  const args = proposal?.impact?.scope?.tool_call?.arguments || {};
  const kind = proposalKind(proposal);
  if (kind === "structure") {
    const currentTitles = titleList(before);
    const nextTitles = Array.isArray(after.required_structure)
      ? after.required_structure
      : [];
    const count = Number(
      after.required_chapter_count ||
        args.required_chapter_count ||
        nextTitles.length ||
        0,
    );
    return [
      {
        key: "structure",
        label: "报告目录",
        before: numberedText(currentTitles) || "尚未形成目录",
        after: nextTitles.length
          ? numberedText(nextTitles)
          : `由报告规划器重新设计为 ${count || "合适数量的"} 章，标题不预设`,
        beforeLabel: "当前目录",
        afterLabel: "确认目标",
      },
    ];
  }
  if (kind === "section_titles") {
    const changes = Array.isArray(after.changes) ? after.changes : [];
    return changes.map((change: any, index: number) => ({
      key: `section-title-${index}`,
      label: `第 ${index + 1} 章标题`,
      before: diffValue(change?.old_title, "当前标题未识别"),
      after: diffValue(change?.new_title, "调整后标题未识别"),
      beforeLabel: "当前标题",
      afterLabel: "调整后标题",
    }));
  }
  if (kind === "requirements")
    return [
      {
        key: "theme",
        label: "报告主题",
        before: diffValue(before.theme, "尚未填写"),
        after: diffValue(after.theme, "保持不变"),
        beforeLabel: "当前",
        afterLabel: "调整后",
      },
      {
        key: "requirements",
        label: "完整报告要求",
        before: diffValue(before.requirements, "尚未填写"),
        after: diffValue(after.requirements, "保持不变"),
        beforeLabel: "当前",
        afterLabel: "调整后",
      },
    ];
  if (kind === "analysis")
    return [
      {
        key: "analysis",
        label: "分析规划要求",
        before: diffValue(
          before.dimensions || before.chapter_plans,
          "沿用当前分析规划",
        ),
        after: [
          String(after.instruction || args.instruction || "按本轮要求重新规划"),
          Array.isArray(after.required_dimensions) &&
          after.required_dimensions.length
            ? `确认维度：${after.required_dimensions.join("、")}`
            : "",
          Number(after.required_dimension_count || 0)
            ? `维度数量：${after.required_dimension_count}`
            : "",
        ]
          .filter(Boolean)
          .join("\n"),
        beforeLabel: "当前依据",
        afterLabel: "确认目标",
      },
    ];
  if (kind === "content")
    return [
      {
        key: "content",
        label: toolLabels[proposalToolName(proposal)] || "正文修改",
        before: diffValue(
          before.content || before.paragraph || before.title || before.section,
          "当前选定内容",
        ),
        after: String(
          after.instruction || args.instruction || "按本轮要求修改",
        ),
        beforeLabel: "修改范围",
        afterLabel: "修改要求",
      },
    ];
  if (kind === "verification")
    return [
      {
        key: "verification",
        label:
          proposalToolName(proposal) === "recheck_fact"
            ? "事实与证据复核"
            : "分析判断复核",
        before: diffValue(before.content, "当前选定内容"),
        after: String(after.instruction || args.instruction || "重新核验依据"),
        beforeLabel: "待复核内容",
        afterLabel: "复核问题",
      },
    ];
  if (kind === "control")
    return [
      {
        key: "control",
        label: "任务操作",
        before: "保持当前任务状态",
        after: toolLabels[proposalToolName(proposal)] || "执行任务操作",
        beforeLabel: "当前",
        afterLabel: "确认后",
      },
    ];
  return proposalDiffRows(proposal).map((row: any) => ({
    ...row,
    beforeLabel: "当前",
    afterLabel: "调整后",
  }));
}
function proposalTitle(proposal: any) {
  if (proposal?.artifact_type === "task_draft") return "需求草案";
  const tool = proposalToolName(proposal);
  return toolLabels[tool] || `修改${artifactLabel(proposal)}`;
}
async function ensureThread() {
  if (thread.value?.status === "open") return thread.value;
  if (props.artifactType === "task_draft" && !props.objectId) {
    throw new Error("草稿会话尚未初始化，请稍后重试。");
  }
  if (
    props.artifactType !== "task_draft" &&
    !props.taskId &&
    props.reportId == null
  ) {
    throw new Error("任务会话尚未初始化，请稍后重试。");
  }
  thread.value = await api<any>(
    "/api/interactions",
    jsonInit("POST", {
      task_id: props.taskId || "",
      report_id: props.reportId,
      artifact_type: props.artifactType,
      artifact_version: props.artifactVersion || "",
      object_id: String(props.objectId || ""),
      scope: { current: props.current || {} },
    }),
  );
  await loadThreads(false);
  return thread.value;
}
async function send() {
  const content = message.value.trim();
  if (!content || pending.value || agentPending.value || readOnly.value) return;
  pending.value = true;
  error.value = "";
  try {
    const active = await ensureThread();
    const result = await api<any>(
      `/api/interactions/${active.id}/messages`,
      jsonInit("POST", {
        content,
        async: true,
      request_id:
        globalThis.crypto?.randomUUID?.() ||
        `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      context: props.focus || {},
      draft_current:
        props.artifactType === "task_draft" ? props.current || {} : undefined,
    }),
    );
    thread.value = result.thread;
    if (
      thread.value?.artifact_type === "task_draft" &&
      thread.value?.scope?.current
    ) {
      emit("applied", { after: thread.value.scope.current });
    }
    message.value = "";
    await loadThreads(false);
  } catch (e: any) {
    error.value = e.message || "讨论失败";
  } finally {
    pending.value = false;
  }
}
async function decide(proposal: any, decision: "accepted" | "rejected") {
  try {
    const result = await api<any>(
      `/api/change-proposals/${proposal.id}/decision`,
      jsonInit("POST", { decision }),
    );
    thread.value = await api(`/api/interactions/${thread.value.id}`);
    await loadThreads(false);
    if (decision === "accepted") emit("applied", result);
  } catch (e: any) {
    error.value = e.message || "处理提案失败";
  }
}
async function startNewConversation() {
  if (thread.value?.status === "open") {
    if (thread.value.pending) {
      error.value = "助手仍在处理当前问题，完成后再归档会话。";
      return;
    }
    const hasContent = Boolean(
      thread.value.messages?.length || thread.value.proposals?.length,
    );
    if (
      hasContent &&
      !confirm("归档当前会话并开始新对话？历史消息和提案仍会保留。")
    )
      return;
    try {
      await api(`/api/interactions/${thread.value.id}/close`, {
        method: "POST",
      });
    } catch (e: any) {
      error.value =
        e?.payload?.error === "INTERACTION_THREAD_BUSY"
          ? "助手仍在处理当前问题，完成后再开始新对话。"
          : e.message || "无法归档当前会话";
      return;
    }
  }
  thread.value = null;
  message.value = "";
  expandedProposalIds.value = new Set();
  error.value = "";
  historyOpen.value = false;
  await loadThreads(false);
}
async function archiveCurrent() {
  if (thread.value?.status !== "open") return;
  if (thread.value.pending) {
    error.value = "助手仍在处理当前问题，完成后再归档会话。";
    return;
  }
  if (!confirm("归档当前会话？消息和提案会保留在历史中。")) return;
  try {
    await api(`/api/interactions/${thread.value.id}/close`, { method: "POST" });
    thread.value = await api(`/api/interactions/${thread.value.id}`);
    await loadThreads(false);
  } catch (e: any) {
    error.value =
      e?.payload?.error === "INTERACTION_THREAD_BUSY"
        ? "助手仍在处理当前问题，完成后再归档。"
        : e.message || "无法归档会话";
  }
}
</script>

<template>
  <section class="copilot" :class="{ compact }">
    <header class="copilot-toolbar">
      <div class="session-title">
        <b>{{ thread?.id ? artifactLabel() : "新对话" }}</b>
        <small v-if="thread?.id">{{
          thread.status === "closed"
            ? "已归档，只读"
            : decisionSummary || "修改前需确认提案"
        }}</small>
        <small v-else>围绕当前产物开始讨论</small>
      </div>
      <div class="copilot-head-actions">
        <button
          type="button"
          :class="{ active: historyOpen }"
          @click="historyOpen = !historyOpen"
        >
          历史<span v-if="threads.length">{{ threads.length }}</span>
        </button>
        <button class="new-chat" type="button" @click="startNewConversation">
          新对话
        </button>
      </div>
    </header>

    <div class="conversation-workspace">
      <div ref="conversationStream" class="conversation-stream">
        <div v-if="conversationItems.length" class="messages">
          <template
            v-for="item in conversationItems"
            :key="`${item.itemType}:${item.id}`"
          >
            <div
              v-if="item.itemType === 'message'"
              class="message"
              :class="item.role"
            >
              <small>{{ item.role === "user" ? "你" : "助手" }}</small>
              <p>{{ item.content }}</p>
            </div>
            <article v-else class="proposal conversation-proposal">
              <div class="proposal-head">
                <div>
                  <small>变更建议</small
                  ><b>{{
                    item.status === "proposed"
                      ? proposalTitle(item)
                      : `${proposalTitle(item)} · ${proposalStatusLabel(item)}`
                  }}</b>
                  <p>{{ item.rationale }}</p>
                </div>
                <span
                  class="badge"
                  :class="
                    item.risk_level === 'high'
                      ? 'danger'
                      : item.risk_level === 'medium'
                        ? 'warning'
                        : ''
                  "
                  >{{ riskLabel(item.risk_level) }}风险</span
                >
              </div>
              <div
                v-if="proposalDisplayRows(item).length"
                class="changed-fields"
              >
                <span v-for="row in proposalDisplayRows(item)" :key="row.key">{{
                  row.label
                }}</span
                ><span class="impact-chip">{{ impactText(item) }}</span>
              </div>
              <button
                v-if="
                  proposalDisplayRows(item).length &&
                  item.artifact_type !== 'task_draft'
                "
                class="proposal-toggle"
                type="button"
                @click="toggleProposal(item)"
              >
                {{ proposalExpanded(item) ? "收起详情" : "查看范围与目标" }}
              </button>
              <template
                v-if="
                  proposalDisplayRows(item).length && proposalExpanded(item)
                "
              >
                <div class="diff-preview">
                  <section
                    v-for="row in proposalDisplayRows(item)"
                    :key="row.key"
                  >
                    <b>{{ row.label }}</b>
                    <div class="diff-side before">
                      <small>{{ row.beforeLabel }}</small
                      ><del>{{ row.before }}</del>
                    </div>
                    <div class="diff-side after">
                      <small>{{ row.afterLabel }}</small
                      ><ins>{{ row.after }}</ins>
                    </div>
                  </section>
                </div>
                <small class="proposal-boundary"
                  >执行边界：{{ impactText(item) }}。现有版本不会被覆盖。</small
                >
              </template>
              <div v-if="item.status === 'proposed'" class="button-row">
                <button class="btn primary" @click="decide(item, 'accepted')">
                  接受</button
                ><button class="btn" @click="decide(item, 'rejected')">
                  拒绝
                </button>
              </div>
              <div v-else class="proposal-state">
                <span
                  class="badge"
                  :class="
                    item.execution_status === 'failed' ? 'danger' : 'success'
                  "
                  >{{
                    item.status === "superseded"
                      ? "已替代"
                      : item.status === "rejected"
                        ? "已拒绝"
                        : executionLabel(item)
                  }}</span
                ><a
                  v-if="item.candidate_version_id && item.report_id"
                  :href="`/reports/${item.report_id}?version=${item.candidate_version_id}&review=1`"
                  >审阅新版本</a
                ><small v-else-if="item.candidate_version_id"
                  >新版本已生成，可在报告版本审阅中决定最终保留内容。</small
                ><small
                  v-if="item.execution_error && item.status !== 'superseded'"
                  >后台修改未完成，请回到任务页查看异常并决定是否重试。</small
                >
              </div>
            </article>
          </template>
        </div>
        <div v-else class="copilot-empty">
          <b>{{ threads.length ? "开始一段新讨论" : "与助手讨论当前产物" }}</b>
          <p>可以询问依据、讨论组织方式，或要求提出局部修改方案。</p>
          <button
            v-if="threads.length"
            type="button"
            @click="historyOpen = true"
          >
            查看 {{ threads.length }} 个历史会话
          </button>
        </div>
        <div
          v-if="suggestedPrompts?.length && !thread?.messages?.length"
          class="prompt-chips"
        >
          <button
            v-for="prompt in suggestedPrompts"
            :key="prompt"
            type="button"
            @click="message = prompt"
          >
            {{ prompt }}
          </button>
        </div>
        <div v-if="agentPending" class="agent-working">
          <span class="working-dot"></span
          >助手正在结合当前产物与证据分析，结果会自动保存。
        </div>
      </div>

      <div
        v-if="historyOpen"
        class="drawer-backdrop"
        @click="historyOpen = false"
      ></div>
      <aside v-if="historyOpen" class="side-drawer history-drawer">
        <div class="drawer-head">
          <div><b>历史会话</b><small>按最近更新排序</small></div>
          <span class="drawer-actions"
            ><button v-if="thread?.status === 'open'" @click="archiveCurrent">
              归档当前</button
            ><button @click="historyOpen = false">关闭</button></span
          >
        </div>
        <div v-if="loadingHistory" class="drawer-empty">正在加载…</div>
        <div v-else-if="threads.length" class="thread-list">
          <button
            v-for="item in threads"
            :key="item.id"
            type="button"
            :class="{ active: item.id === thread?.id }"
            @click="selectThread(item)"
          >
            <span class="thread-line"
              ><b>{{ artifactLabel(item) }}</b
              ><time>{{ formatTime(item.updated_at) }}</time></span
            >
            <span class="thread-preview">{{
              item.last_message?.content || "尚无消息"
            }}</span>
            <span class="thread-meta"
              >{{ item.message_count }} 条消息<i
                v-if="item.pending_proposal_count"
                >{{ item.pending_proposal_count }} 个待确认提案</i
              ><i v-else-if="item.proposal_count"
                >{{ item.proposal_count }} 个提案</i
              ><em v-if="item.status === 'closed'">已归档</em></span
            >
          </button>
        </div>
        <div v-else class="drawer-empty">暂无历史会话。</div>
      </aside>
    </div>

    <div v-if="readOnly" class="readonly-notice">
      <span>该会话已归档，仅供查看。</span
      ><button @click="startNewConversation">开始新对话</button>
    </div>
    <form v-else class="composer" @submit.prevent="send">
      <textarea
        v-model="message"
        rows="3"
        placeholder="输入问题或修改要求…"
      ></textarea
      ><button
        class="btn primary"
        :disabled="pending || agentPending || !message.trim()"
      >
        {{ pending ? "正在提交…" : agentPending ? "助手分析中" : "发送" }}
      </button>
    </form>
    <p v-if="error" class="error-text">{{ error }}</p>
  </section>
</template>

<style scoped>
.copilot {
  display: grid;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  grid-template-rows: auto minmax(0, 1fr) auto;
  gap: 12px;
}
.copilot.compact {
  gap: 8px;
}
.copilot-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--color-border);
}
.copilot.compact .copilot-toolbar {
  min-height: 30px;
  padding-bottom: 7px;
}
.session-title {
  min-width: 0;
}
.session-title b,
.session-title small {
  display: block;
}
.copilot.compact .session-title small {
  display: none;
}
.session-title b {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-title small {
  margin-top: 2px;
  color: var(--color-muted);
  font-size: 11px;
}
.copilot-head-actions {
  display: flex;
  align-items: center;
  gap: 3px;
}
.copilot-head-actions button {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 7px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--color-muted);
  font-size: 12px;
  white-space: nowrap;
}
.copilot-head-actions button:hover,
.copilot-head-actions button.active {
  background: var(--color-surface-soft);
  color: var(--color-primary);
}
.copilot-head-actions button.new-chat {
  margin-left: 3px;
  border: 1px solid var(--border-strong);
  background: var(--surface-raised);
  color: var(--foreground);
}
.copilot-head-actions span {
  display: grid;
  place-items: center;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 9px;
  background: var(--surface-hover);
  color: var(--muted-foreground);
  font-size: 10px;
}
.copilot-head-actions span.alert {
  background: var(--warning-soft);
  color: var(--warning);
}
.conversation-workspace {
  position: relative;
  display: grid;
  min-height: 0;
  overflow: hidden;
}
.conversation-stream {
  min-height: 0;
  height: 100%;
  overflow: auto;
  overscroll-behavior: contain;
  padding-right: 6px;
  scrollbar-gutter: stable;
}
.messages {
  display: grid;
  gap: 11px;
  min-width: 0;
}
.message {
  max-width: 88%;
  padding: 10px 12px;
  border-left: 2px solid var(--color-border-strong);
  background: var(--color-surface-soft);
  overflow-wrap: anywhere;
}
.message.user {
  justify-self: end;
  border-left: 0;
  border-right: 2px solid var(--primary);
  background: var(--color-primary-soft);
}
.message small {
  color: var(--color-muted);
}
.message p {
  margin: 3px 0;
  line-height: 1.65;
  white-space: pre-wrap;
}
.copilot-empty {
  padding: 28px 18px;
  text-align: center;
  color: var(--color-muted);
  background: var(--color-surface-soft);
}
.copilot-empty b {
  display: block;
  color: var(--color-text);
}
.copilot-empty p {
  margin: 6px 0 12px;
}
.copilot-empty button {
  border: 0;
  background: transparent;
  color: var(--color-primary);
}
.prompt-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}
.prompt-chips button {
  padding: 6px 9px;
  border: 1px solid var(--color-border);
  border-radius: 5px;
  background: var(--surface-raised);
  color: var(--color-muted);
  font-size: 12px;
  text-align: left;
}
.prompt-chips button:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
  background: var(--color-primary-soft);
}
.agent-working {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding: 10px 12px;
  border-left: 2px solid var(--color-primary);
  background: var(--color-surface-soft);
  color: var(--color-text-secondary);
}
.working-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-primary);
  animation: working-pulse 1.2s ease-in-out infinite;
}
@keyframes working-pulse {
  50% {
    opacity: 0.35;
  }
}
.drawer-backdrop {
  position: absolute;
  inset: 0;
  z-index: 5;
  background: rgba(0, 0, 0, 0.56);
}
.side-drawer {
  position: absolute;
  top: 0;
  bottom: 0;
  z-index: 6;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  width: min(330px, 92%);
  overflow: hidden;
  border: 1px solid var(--color-border);
  background: var(--card);
  box-shadow: var(--shadow-float);
}
.history-drawer {
  left: 0;
}
.proposal-drawer {
  right: 0;
}
.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 13px;
  border-bottom: 1px solid var(--color-border);
}
.drawer-head b,
.drawer-head small {
  display: block;
}
.drawer-head small {
  margin-top: 1px;
  color: var(--color-muted);
  font-size: 11px;
}
.drawer-head button {
  padding: 4px;
  border: 0;
  background: transparent;
  color: var(--color-muted);
  font-size: 12px;
}
.drawer-empty {
  padding: 24px;
  color: var(--color-muted);
}
.thread-list,
.proposal-list {
  overflow: auto;
}
.thread-list > button {
  display: grid;
  width: 100%;
  gap: 5px;
  padding: 12px 13px;
  border: 0;
  border-bottom: 1px solid var(--color-border);
  background: var(--card);
  text-align: left;
}
.thread-list > button:hover {
  background: var(--surface-hover);
}
.thread-list > button.active {
  box-shadow: inset 3px 0 var(--color-primary);
  background: var(--color-primary-soft);
}
.thread-line {
  display: flex;
  justify-content: space-between;
  gap: 10px;
}
.thread-line time {
  color: var(--color-muted);
  font-size: 11px;
  font-style: normal;
}
.thread-preview {
  overflow: hidden;
  color: var(--color-text-secondary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.thread-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-muted);
  font-size: 10px;
}
.thread-meta i,
.thread-meta em {
  font-style: normal;
}
.thread-meta i {
  color: var(--warning);
}
.thread-meta em {
  margin-left: auto;
}
.proposal-list {
  display: grid;
  gap: 8px;
  padding: 8px;
}
.proposal {
  padding: 13px;
  border: 1px solid var(--color-border);
  background: var(--surface-raised);
}
.proposal-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
.proposal-head > div {
  min-width: 0;
}
.proposal-head p {
  display: -webkit-box;
  overflow: hidden;
  margin: 3px 0 0;
  color: var(--color-muted);
  line-height: 1.5;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}
.changed-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 9px;
}
.changed-fields span {
  padding: 2px 6px;
  border: 1px solid var(--color-border);
  border-radius: 3px;
  background: var(--surface-hover);
  color: var(--muted-foreground);
  font-size: 11px;
}
.changed-fields .impact-chip {
  border-color: color-mix(in srgb, var(--primary) 40%, var(--border));
  background: var(--primary-soft);
  color: var(--accent);
}
.proposal-toggle {
  margin-top: 8px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-primary);
  font-size: 12px;
}
.diff-preview {
  display: grid;
  gap: 12px;
  max-height: 340px;
  margin: 10px 0;
  padding-right: 4px;
  overflow: auto;
  overscroll-behavior: contain;
}
.diff-preview section {
  display: grid;
  gap: 6px;
}
.diff-preview section > b {
  font-size: 11px;
  color: var(--color-text-secondary);
}
.diff-side {
  display: grid;
  grid-template-columns: 60px minmax(0, 1fr);
  align-items: start;
}
.diff-side > small {
  padding: 9px 7px;
  color: var(--color-muted);
  font-size: 10px;
}
.diff-side del,
.diff-side ins {
  padding: 8px 10px;
  text-decoration: none;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.diff-side.before {
  background: var(--muted);
}
.diff-side.before del {
  color: var(--muted-foreground);
}
.diff-side.after {
  border-left: 2px solid var(--success);
  background: var(--success-soft);
}
.diff-side.after ins {
  color: var(--success);
}
.proposal-boundary {
  display: block;
  padding: 8px 10px;
  border-left: 2px solid var(--color-primary);
  background: var(--color-primary-soft);
  color: var(--color-text-secondary);
}
.proposal-state {
  display: grid;
  gap: 6px;
  margin-top: 10px;
}
.proposal-state small {
  color: var(--color-muted);
}
.proposal-state a {
  width: max-content;
  color: var(--color-primary);
  font-size: 12px;
  font-weight: 650;
  text-decoration: none;
}
.proposal-state a:hover {
  text-decoration: underline;
}
.composer {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: end;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--color-border);
  background: var(--card);
}
.composer textarea {
  min-height: 66px;
  resize: vertical;
}
.composer .btn {
  height: 36px;
}
.readonly-notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  border-top: 1px solid var(--color-border);
  background: var(--color-surface-soft);
  color: var(--color-muted);
}
.readonly-notice button {
  border: 0;
  background: transparent;
  color: var(--color-primary);
  font-weight: 650;
}
.error-text {
  margin: 0;
  color: var(--color-danger);
  font-size: 12px;
}
.drawer-actions {
  display: flex;
  gap: 6px;
}
.drawer-actions button:first-child {
  color: var(--color-primary);
}
.conversation-proposal {
  width: 92%;
  box-sizing: border-box;
  border-left: 3px solid var(--warning);
  background: color-mix(in srgb, var(--warning-soft) 48%, var(--surface-raised));
}
.conversation-proposal > .proposal-head small {
  display: block;
  margin-bottom: 2px;
  color: var(--warning);
}
@media (max-width: 520px) {
  .session-title small {
    display: none;
  }
  .copilot-head-actions button {
    padding: 5px;
  }
  .message {
    max-width: 95%;
  }
  .side-drawer {
    width: 100%;
  }
}
</style>
