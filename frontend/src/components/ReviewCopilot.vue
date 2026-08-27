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
const proposals = computed<any[]>(() => thread.value?.proposals || []);
const conversationItems = computed(() => {
  const messages = (thread.value?.messages || []).map((item: any) => ({ ...item, itemType: "message" }));
  const linked = new Map<number, any[]>();
  const unlinked: any[] = [];
  for (const proposal of proposals.value) {
    let sourceId = Number(proposal?.impact?.source_message_id || 0);
    if (!sourceId) {
      const prior = [...messages].reverse().find((item: any) =>
        item.role === "assistant" && String(item.created_at || "") <= String(proposal.created_at || ""),
      );
      sourceId = Number(prior?.id || 0);
    }
    if (!sourceId) unlinked.push({ ...proposal, itemType: "proposal" });
    else linked.set(sourceId, [...(linked.get(sourceId) || []), { ...proposal, itemType: "proposal" }]);
  }
  const result: any[] = [];
  for (const item of messages) {
    result.push(item, ...(linked.get(Number(item.id)) || []).sort((a, b) => Number(a.id) - Number(b.id)));
  }
  return [...result, ...unlinked.sort((a, b) => {
    const byTime = String(a.created_at || "").localeCompare(String(b.created_at || ""));
    return byTime || Number(a.id || 0) - Number(b.id || 0);
  })];
});
const readOnly = computed(() => thread.value?.status === "closed");
const scopeKey = computed(() =>
  `${props.taskId || "draft"}:${props.reportId || ""}:${props.artifactType}:${props.artifactVersion || ""}:${props.objectId || "root"}`,
);
const artifactLabels: Record<string, string> = {
  task_draft: "任务需求", task_brief: "任务需求", task_control: "任务操作", material_role: "材料理解",
  analysis_plan: "分析规划", fact: "事实", inference: "分析判断",
  final_plan: "报告目录", narrative_plan: "成文组织", report_title: "报告标题",
  section_title: "章节标题", paragraph: "正文段落", sentence: "正文句子",
  qa_issue: "质量问题", comparison_item: "增量对比",
};
let timer: number | undefined;

watch(scopeKey, async () => {
  thread.value = null;
  message.value = "";
  error.value = "";
  historyOpen.value = false;
  await loadThreads(true);
}, { immediate: true });
watch(() => conversationItems.value.length, async (next, previous) => {
  if (next <= previous) return;
  await nextTick();
  const element = conversationStream.value;
  if (element) element.scrollTop = element.scrollHeight;
});

timer = window.setInterval(async () => {
  if (!thread.value?.id || (!thread.value.pending && !(thread.value.proposals || []).some(
    (item: any) => ["waiting", "queued", "running"].includes(item.execution_status),
  ))) return;
  try {
    thread.value = await api(`/api/interactions/${thread.value.id}`);
    await loadThreads(false);
  } catch {
    /* Passive refresh must not interrupt editing. */
  }
}, 1800);
onBeforeUnmount(() => { if (timer) window.clearInterval(timer); });

function artifactLabel(item: any = thread.value) {
  return artifactLabels[item?.artifact_type || props.artifactType] || item?.artifact_type || "当前产物";
}
function historyUrl() {
  const params = new URLSearchParams();
  if (props.taskId) params.set("task_id", props.taskId);
  else if (props.artifactType === "task_draft" && props.objectId) params.set("draft_id", String(props.objectId));
  else if (props.reportId != null) params.set("report_id", String(props.reportId));
  return params.size ? `/api/interactions?${params.toString()}` : "";
}
function matchesCurrentScope(item: any) {
  return item.status === "open" && item.artifact_type === props.artifactType &&
    String(item.artifact_version || "") === String(props.artifactVersion || "") &&
    String(item.object_id || "") === String(props.objectId || "");
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
    if (thread.value.artifact_type === "task_draft" && thread.value.scope?.current) {
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
  return ({ waiting: "等待当前轮次结束", queued: "已进入后台队列", running: "正在后台重算",
    completed: "候选版本已生成", failed: "后台处理失败" } as any)[proposal.execution_status] || "已记录";
}
function proposalExpanded(proposal: any) {
  return expandedProposalIds.value.has(Number(proposal.id));
}
function toggleProposal(proposal: any) {
  const next = new Set(expandedProposalIds.value);
  const id = Number(proposal.id);
  if (next.has(id)) next.delete(id); else next.add(id);
  expandedProposalIds.value = next;
}
function formatTime(value: string) {
  if (!value) return "";
  const date = new Date(value.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  if (date.toDateString() === new Date().toDateString()) {
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}
function diffValue(value: any, fallback: string) {
  if (value == null || value === "") return fallback;
  if (Array.isArray(value)) {
    const rows = value.map((item: any, index: number) => {
      if (item == null) return "";
      if (typeof item !== "object") return String(item);
      const content = item.display_title || item.title || item.content || item.summary || item.instruction;
      return content ? `${index + 1}. ${content}` : "";
    }).filter(Boolean);
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
  theme: "报告主题", requirements: "报告要求", content: "内容", title: "标题",
  tool_name: "执行能力", arguments: "执行参数", instruction: "调整要求",
  material_role: "材料角色", claim_support: "事实边界", allowed_usage: "允许用途",
  forbidden_usage: "禁止用途", missing_information: "缺失信息", chapter_plans: "章节规划",
  narrative_logic: "叙事逻辑", budget: "规模预算", confidence_level: "置信度",
};
const toolLabels: Record<string, string> = {
  pause_task: "暂停任务", resume_task: "恢复任务", retry_task: "重试任务",
  regenerate_chapter: "重新生成章节", rerun_final_plan: "重新规划报告结构",
};
const impactLabels: Record<string, string> = {
  material_analysis: "材料理解", analysis_plan: "分析规划", evidence: "事实与证据",
  conflict: "冲突核验", analysis: "综合分析", final_plan: "报告目录",
  narrative_plan: "成文组织", writing: "报告正文", qa: "质量检查", render: "文档导出",
  lineage_check: "溯源检查",
};
function impactText(proposal: any) {
  const values = (proposal?.impact?.invalidates || []).map((item: string) => impactLabels[item]).filter(Boolean);
  return values.length ? values.join("、") : "仅检查当前内容";
}
function riskLabel(value: string) {
  return ({ low: "低", medium: "中", high: "高" } as any)[value] || "待评估";
}
function proposalDiffRows(proposal: any) {
  const after = proposal?.after && typeof proposal.after === "object" ? proposal.after : {};
  const before = proposal?.before && typeof proposal.before === "object" ? proposal.before : {};
  return Object.keys(after).filter((key) => fieldLabels[key]).map((key) => ({
    key, label: fieldLabels[key],
    before: diffValue(before[key], "未设置"),
    after: key === "tool_name" ? (toolLabels[String(after[key])] || String(after[key])) : diffValue(after[key], "未设置"),
  }));
}
async function ensureThread() {
  if (thread.value?.status === "open") return thread.value;
  if (props.artifactType === "task_draft" && !props.objectId) {
    throw new Error("草稿会话尚未初始化，请稍后重试。");
  }
  if (props.artifactType !== "task_draft" && !props.taskId && props.reportId == null) {
    throw new Error("任务会话尚未初始化，请稍后重试。");
  }
  thread.value = await api<any>("/api/interactions", jsonInit("POST", {
    task_id: props.taskId || "", report_id: props.reportId, artifact_type: props.artifactType,
    artifact_version: props.artifactVersion || "", object_id: String(props.objectId || ""),
    scope: { current: props.current || {} },
  }));
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
    const result = await api<any>(`/api/interactions/${active.id}/messages`, jsonInit("POST", {
      content, async: true,
      request_id: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      context: props.focus || {},
    }));
    thread.value = result.thread;
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
    const result = await api<any>(`/api/change-proposals/${proposal.id}/decision`, jsonInit("POST", { decision }));
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
    const hasContent = Boolean(thread.value.messages?.length || thread.value.proposals?.length);
    if (hasContent && !confirm("归档当前会话并开始新对话？历史消息和提案仍会保留。")) return;
    try {
      await api(`/api/interactions/${thread.value.id}/close`, { method: "POST" });
    } catch (e: any) {
      error.value = e?.payload?.error === "INTERACTION_THREAD_BUSY"
        ? "助手或后台修改仍在处理，完成后再开始新对话。" : e.message || "无法归档当前会话";
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
    error.value = e?.payload?.error === "INTERACTION_THREAD_BUSY"
      ? "助手或后台修改仍在处理，完成后再归档。" : e.message || "无法归档会话";
  }
}
</script>

<template>
  <section class="copilot" :class="{ compact }">
    <header class="copilot-toolbar">
      <div class="session-title">
        <b>{{ thread?.id ? artifactLabel() : "新对话" }}</b>
        <small v-if="thread?.id">{{ thread.status === "closed" ? "已归档，只读" : "修改前需确认提案" }}</small>
        <small v-else>围绕当前产物开始讨论</small>
      </div>
      <div class="copilot-head-actions">
        <button type="button" :class="{ active: historyOpen }" @click="historyOpen = !historyOpen">历史<span v-if="threads.length">{{ threads.length }}</span></button>
        <button class="new-chat" type="button" @click="startNewConversation">新对话</button>
      </div>
    </header>

    <div class="conversation-workspace">
      <div ref="conversationStream" class="conversation-stream">
        <div v-if="conversationItems.length" class="messages">
          <template v-for="item in conversationItems" :key="`${item.itemType}:${item.id}`">
            <div v-if="item.itemType === 'message'" class="message" :class="item.role">
              <small>{{ item.role === "user" ? "你" : "助手" }}</small><p>{{ item.content }}</p>
            </div>
            <article v-else class="proposal conversation-proposal">
              <div class="proposal-head"><div><small>变更建议</small><b>{{ item.status === "proposed" ? `建议修改${artifactLabel(item)}` : "提案处理结果" }}</b><p>{{ item.rationale }}</p></div><span class="badge" :class="item.risk_level === 'high' ? 'danger' : item.risk_level === 'medium' ? 'warning' : ''">{{ riskLabel(item.risk_level) }}风险</span></div>
              <div v-if="proposalDiffRows(item).length" class="changed-fields"><span v-for="row in proposalDiffRows(item)" :key="row.key">{{ row.label }}</span></div>
              <button v-if="proposalDiffRows(item).length" class="proposal-toggle" type="button" @click="toggleProposal(item)">{{ proposalExpanded(item) ? "收起修改详情" : "查看修改前后" }}</button>
              <template v-if="proposalDiffRows(item).length && proposalExpanded(item)">
                <div class="diff-preview">
                  <section v-for="row in proposalDiffRows(item)" :key="row.key"><b>{{ row.label }}</b><del>{{ row.before }}</del><ins>{{ row.after }}</ins></section>
                </div>
                <small>接受后将重新检查：{{ impactText(item) }}</small>
              </template>
              <div v-if="item.status === 'proposed'" class="button-row"><button class="btn primary" @click="decide(item, 'accepted')">接受</button><button class="btn" @click="decide(item, 'rejected')">拒绝</button></div>
              <div v-else class="proposal-state"><span class="badge" :class="item.execution_status === 'failed' ? 'danger' : 'success'">{{ item.status === "rejected" ? "已拒绝" : executionLabel(item) }}</span><small v-if="item.candidate_version_id">新版本已生成，请进入版本审阅决定最终保留内容。</small><small v-if="item.execution_error">后台修改未完成，请回到任务页查看异常并决定是否重试。</small></div>
            </article>
          </template>
        </div>
        <div v-else class="copilot-empty">
          <b>{{ threads.length ? "开始一段新讨论" : "与助手讨论当前产物" }}</b>
          <p>可以询问依据、讨论组织方式，或要求提出局部修改方案。</p>
          <button v-if="threads.length" type="button" @click="historyOpen = true">查看 {{ threads.length }} 个历史会话</button>
        </div>
        <div v-if="suggestedPrompts?.length && !thread?.messages?.length" class="prompt-chips">
          <button v-for="prompt in suggestedPrompts" :key="prompt" type="button" @click="message = prompt">{{ prompt }}</button>
        </div>
        <div v-if="agentPending" class="agent-working"><span class="working-dot"></span>助手正在结合当前产物与证据分析，结果会自动保存。</div>
      </div>

      <div v-if="historyOpen" class="drawer-backdrop" @click="historyOpen = false"></div>
      <aside v-if="historyOpen" class="side-drawer history-drawer">
        <div class="drawer-head"><div><b>历史会话</b><small>按最近更新排序</small></div><span class="drawer-actions"><button v-if="thread?.status === 'open'" @click="archiveCurrent">归档当前</button><button @click="historyOpen = false">关闭</button></span></div>
        <div v-if="loadingHistory" class="drawer-empty">正在加载…</div>
        <div v-else-if="threads.length" class="thread-list">
          <button v-for="item in threads" :key="item.id" type="button" :class="{ active: item.id === thread?.id }" @click="selectThread(item)">
            <span class="thread-line"><b>{{ artifactLabel(item) }}</b><time>{{ formatTime(item.updated_at) }}</time></span>
            <span class="thread-preview">{{ item.last_message?.content || "尚无消息" }}</span>
            <span class="thread-meta">{{ item.message_count }} 条消息<i v-if="item.pending_proposal_count">{{ item.pending_proposal_count }} 个待确认提案</i><i v-else-if="item.proposal_count">{{ item.proposal_count }} 个提案</i><em v-if="item.status === 'closed'">已归档</em></span>
          </button>
        </div>
        <div v-else class="drawer-empty">暂无历史会话。</div>
      </aside>

    </div>

    <div v-if="readOnly" class="readonly-notice"><span>该会话已归档，仅供查看。</span><button @click="startNewConversation">开始新对话</button></div>
    <form v-else class="composer" @submit.prevent="send"><textarea v-model="message" rows="3" placeholder="输入问题或修改要求…"></textarea><button class="btn primary" :disabled="pending || agentPending || !message.trim()">{{ pending ? "正在提交…" : agentPending ? "助手分析中" : "发送" }}</button></form>
    <p v-if="error" class="error-text">{{ error }}</p>
  </section>
</template>

<style scoped>
.copilot{display:grid;height:100%;min-height:0;grid-template-rows:auto minmax(0,1fr) auto;gap:12px}.copilot-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;padding-bottom:10px;border-bottom:1px solid var(--color-border)}.session-title{min-width:0}.session-title b,.session-title small{display:block}.session-title b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.session-title small{margin-top:2px;color:var(--color-muted);font-size:11px}.copilot-head-actions{display:flex;align-items:center;gap:3px}.copilot-head-actions button{display:flex;align-items:center;gap:4px;padding:6px 7px;border:0;border-radius:4px;background:transparent;color:var(--color-muted);font-size:12px;white-space:nowrap}.copilot-head-actions button:hover,.copilot-head-actions button.active{background:var(--color-surface-soft);color:var(--color-primary)}.copilot-head-actions button.new-chat{margin-left:3px;background:var(--color-primary);color:#fff}.copilot-head-actions span{display:grid;place-items:center;min-width:17px;height:17px;padding:0 4px;border-radius:9px;background:#e8edf3;color:var(--color-text-secondary);font-size:10px}.copilot-head-actions span.alert{background:#fff0dc;color:#8a5714}
.conversation-workspace{position:relative;min-height:0;overflow:hidden}.conversation-stream{height:100%;overflow:auto;overscroll-behavior:contain;padding-right:6px;scrollbar-gutter:stable}.messages{display:grid;gap:11px;min-width:0}.message{max-width:88%;padding:10px 12px;border-left:2px solid var(--color-border-strong);background:var(--color-surface-soft);overflow-wrap:anywhere}.message.user{justify-self:end;border-left:0;border-right:2px solid var(--color-primary);background:var(--color-primary-soft)}.message small{color:var(--color-muted)}.message p{margin:3px 0;line-height:1.65;white-space:pre-wrap}.copilot-empty{padding:28px 18px;text-align:center;color:var(--color-muted);background:var(--color-surface-soft)}.copilot-empty b{display:block;color:var(--color-text)}.copilot-empty p{margin:6px 0 12px}.copilot-empty button{border:0;background:transparent;color:var(--color-primary)}.prompt-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.prompt-chips button{padding:6px 9px;border:1px solid var(--color-border);border-radius:5px;background:#fff;color:var(--color-muted);font-size:12px;text-align:left}.prompt-chips button:hover{border-color:var(--color-primary);color:var(--color-primary);background:var(--color-primary-soft)}.agent-working{display:flex;align-items:center;gap:8px;margin-top:10px;padding:10px 12px;border-left:2px solid var(--color-primary);background:var(--color-surface-soft);color:var(--color-text-secondary)}.working-dot{width:7px;height:7px;border-radius:50%;background:var(--color-primary);animation:working-pulse 1.2s ease-in-out infinite}@keyframes working-pulse{50%{opacity:.35}}
.drawer-backdrop{position:absolute;inset:0;z-index:5;background:rgba(31,35,41,.12)}.side-drawer{position:absolute;top:0;bottom:0;z-index:6;display:grid;grid-template-rows:auto minmax(0,1fr);width:min(330px,92%);overflow:hidden;border:1px solid var(--color-border);background:#fff;box-shadow:var(--shadow-float)}.history-drawer{left:0}.proposal-drawer{right:0}.drawer-head{display:flex;align-items:center;justify-content:space-between;padding:12px 13px;border-bottom:1px solid var(--color-border)}.drawer-head b,.drawer-head small{display:block}.drawer-head small{margin-top:1px;color:var(--color-muted);font-size:11px}.drawer-head button{padding:4px;border:0;background:transparent;color:var(--color-muted);font-size:12px}.drawer-empty{padding:24px;color:var(--color-muted)}.thread-list,.proposal-list{overflow:auto}.thread-list>button{display:grid;width:100%;gap:5px;padding:12px 13px;border:0;border-bottom:1px solid var(--color-border);background:#fff;text-align:left}.thread-list>button:hover{background:#f7f9fb}.thread-list>button.active{box-shadow:inset 3px 0 var(--color-primary);background:var(--color-primary-soft)}.thread-line{display:flex;justify-content:space-between;gap:10px}.thread-line time{color:var(--color-muted);font-size:11px;font-style:normal}.thread-preview{overflow:hidden;color:var(--color-text-secondary);font-size:12px;text-overflow:ellipsis;white-space:nowrap}.thread-meta{display:flex;align-items:center;gap:8px;color:var(--color-muted);font-size:10px}.thread-meta i,.thread-meta em{font-style:normal}.thread-meta i{color:#8a5714}.thread-meta em{margin-left:auto}
.proposal-list{display:grid;gap:8px;padding:8px}.proposal{padding:13px;border:1px solid var(--color-border);background:#fff}.proposal-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.proposal-head>div{min-width:0}.proposal-head p{display:-webkit-box;overflow:hidden;margin:3px 0 0;color:var(--color-muted);line-height:1.5;-webkit-line-clamp:3;-webkit-box-orient:vertical}.changed-fields{display:flex;flex-wrap:wrap;gap:5px;margin-top:9px}.changed-fields span{padding:2px 6px;border:1px solid var(--color-border);border-radius:3px;background:#fff;color:var(--color-text-secondary);font-size:11px}.proposal-toggle{margin-top:8px;padding:0;border:0;background:transparent;color:var(--color-primary);font-size:12px}.diff-preview{display:grid;gap:10px;max-height:260px;margin:10px 0;padding-right:4px;overflow:auto;overscroll-behavior:contain}.diff-preview section{display:grid;gap:5px}.diff-preview section>b{font-size:11px;color:var(--color-text-secondary)}.diff-preview del,.diff-preview ins{padding:8px 10px;text-decoration:none;white-space:pre-wrap;overflow-wrap:anywhere}.diff-preview del{background:#fff1ef;color:#8e3f36}.diff-preview ins{background:#edf7ef;color:#2f6740}.proposal-state{display:grid;gap:6px;margin-top:10px}.proposal-state small{color:var(--color-muted)}
.composer{display:grid;grid-template-columns:1fr auto;align-items:end;gap:8px;padding-top:10px;border-top:1px solid var(--color-border);background:#fff}.composer textarea{min-height:66px;resize:vertical}.composer .btn{height:36px}.readonly-notice{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;border-top:1px solid var(--color-border);background:var(--color-surface-soft);color:var(--color-muted)}.readonly-notice button{border:0;background:transparent;color:var(--color-primary);font-weight:650}.error-text{margin:0;color:var(--color-danger);font-size:12px}
.drawer-actions{display:flex;gap:6px}.drawer-actions button:first-child{color:var(--color-primary)}
.conversation-proposal{width:92%;box-sizing:border-box;border-left:3px solid #b77724;background:#fffdf8}.conversation-proposal>.proposal-head small{display:block;margin-bottom:2px;color:#8a5714}
@media(max-width:520px){.session-title small{display:none}.copilot-head-actions button{padding:5px}.message{max-width:95%}.side-drawer{width:100%}}
</style>
