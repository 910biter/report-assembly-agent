import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { api, jsonInit } from "@/api/http";
const props = defineProps();
const emit = defineEmits();
const thread = ref(null);
const threads = ref([]);
const message = ref("");
const pending = ref(false);
const loadingHistory = ref(false);
const error = ref("");
const historyOpen = ref(false);
const conversationStream = ref(null);
const expandedProposalIds = ref(new Set());
const agentPending = computed(() => Boolean(thread.value?.pending));
const proposals = computed(() => thread.value?.proposals || []);
const conversationItems = computed(() => {
    const messages = (thread.value?.messages || []).map((item) => ({ ...item, itemType: "message" }));
    const linked = new Map();
    const unlinked = [];
    for (const proposal of proposals.value) {
        let sourceId = Number(proposal?.impact?.source_message_id || 0);
        if (!sourceId) {
            const prior = [...messages].reverse().find((item) => item.role === "assistant" && String(item.created_at || "") <= String(proposal.created_at || ""));
            sourceId = Number(prior?.id || 0);
        }
        if (!sourceId)
            unlinked.push({ ...proposal, itemType: "proposal" });
        else
            linked.set(sourceId, [...(linked.get(sourceId) || []), { ...proposal, itemType: "proposal" }]);
    }
    const result = [];
    for (const item of messages) {
        result.push(item, ...(linked.get(Number(item.id)) || []).sort((a, b) => Number(a.id) - Number(b.id)));
    }
    return [...result, ...unlinked.sort((a, b) => {
            const byTime = String(a.created_at || "").localeCompare(String(b.created_at || ""));
            return byTime || Number(a.id || 0) - Number(b.id || 0);
        })];
});
const readOnly = computed(() => thread.value?.status === "closed");
const scopeKey = computed(() => `${props.taskId || "draft"}:${props.reportId || ""}:${props.artifactType}:${props.artifactVersion || ""}:${props.objectId || "root"}`);
const artifactLabels = {
    task_draft: "任务需求", task_brief: "任务需求", material_role: "材料理解",
    analysis_plan: "分析规划", fact: "事实", inference: "分析判断",
    final_plan: "报告目录", narrative_plan: "成文组织", report_title: "报告标题",
    section_title: "章节标题", paragraph: "正文段落", sentence: "正文句子",
    qa_issue: "质量问题", comparison_item: "增量对比",
};
let timer;
watch(scopeKey, async () => {
    thread.value = null;
    message.value = "";
    error.value = "";
    await loadThreads(true);
}, { immediate: true });
watch(() => conversationItems.value.length, async (next, previous) => {
    if (next <= previous)
        return;
    await nextTick();
    const element = conversationStream.value;
    if (element)
        element.scrollTop = element.scrollHeight;
});
timer = window.setInterval(async () => {
    if (!thread.value?.id || (!thread.value.pending && !(thread.value.proposals || []).some((item) => ["waiting", "queued", "running"].includes(item.execution_status))))
        return;
    try {
        thread.value = await api(`/api/interactions/${thread.value.id}`);
        await loadThreads(false);
    }
    catch {
        /* Passive refresh must not interrupt editing. */
    }
}, 1800);
onBeforeUnmount(() => { if (timer)
    window.clearInterval(timer); });
function artifactLabel(item = thread.value) {
    return artifactLabels[item?.artifact_type || props.artifactType] || item?.artifact_type || "当前产物";
}
function historyUrl() {
    const params = new URLSearchParams();
    if (props.taskId)
        params.set("task_id", props.taskId);
    else if (props.artifactType === "task_draft" && props.objectId)
        params.set("draft_id", String(props.objectId));
    else if (props.reportId != null)
        params.set("report_id", String(props.reportId));
    return params.size ? `/api/interactions?${params.toString()}` : "";
}
function matchesCurrentScope(item) {
    return item.status === "open" && item.artifact_type === props.artifactType &&
        String(item.artifact_version || "") === String(props.artifactVersion || "") &&
        String(item.object_id || "") === String(props.objectId || "");
}
async function loadThreads(autoSelect) {
    const url = historyUrl();
    if (!url)
        return;
    loadingHistory.value = true;
    try {
        threads.value = await api(url);
        if (autoSelect && !thread.value) {
            const active = threads.value.find(matchesCurrentScope);
            if (active)
                await selectThread(active, false);
            else if (threads.value.length)
                historyOpen.value = true;
        }
    }
    catch (e) {
        error.value = e.message || "会话历史加载失败";
    }
    finally {
        loadingHistory.value = false;
    }
}
async function selectThread(item, closeDrawer = true) {
    if (!item?.id || item.id === thread.value?.id) {
        if (closeDrawer)
            historyOpen.value = false;
        return;
    }
    try {
        thread.value = await api(`/api/interactions/${item.id}`);
        if (thread.value.artifact_type === "task_draft" && thread.value.scope?.current) {
            emit("applied", { after: thread.value.scope.current });
        }
        message.value = "";
        expandedProposalIds.value = new Set();
        if (closeDrawer)
            historyOpen.value = false;
    }
    catch (e) {
        error.value = e.message || "无法打开会话";
    }
}
function executionLabel(proposal) {
    return { waiting: "等待当前轮次结束", queued: "已进入后台队列", running: "正在后台重算",
        completed: "候选版本已生成", failed: "后台处理失败" }[proposal.execution_status] || "已记录";
}
function proposalExpanded(proposal) {
    return expandedProposalIds.value.has(Number(proposal.id));
}
function toggleProposal(proposal) {
    const next = new Set(expandedProposalIds.value);
    const id = Number(proposal.id);
    if (next.has(id))
        next.delete(id);
    else
        next.add(id);
    expandedProposalIds.value = next;
}
function formatTime(value) {
    if (!value)
        return "";
    const date = new Date(value.replace(" ", "T"));
    if (Number.isNaN(date.getTime()))
        return value.slice(0, 16);
    if (date.toDateString() === new Date().toDateString()) {
        return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    }
    return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}
function diffValue(value, fallback) {
    if (value == null || value === "")
        return fallback;
    if (typeof value === "object")
        return JSON.stringify(value, null, 2);
    return String(value);
}
const fieldLabels = {
    theme: "报告主题", requirements: "报告要求", content: "内容", title: "标题",
    material_role: "材料角色", claim_support: "事实边界", allowed_usage: "允许用途",
    forbidden_usage: "禁止用途", missing_information: "缺失信息", chapter_plans: "章节规划",
    narrative_logic: "叙事逻辑", budget: "规模预算", confidence_level: "置信度",
};
function proposalDiffRows(proposal) {
    const after = proposal?.after && typeof proposal.after === "object" ? proposal.after : {};
    const before = proposal?.before && typeof proposal.before === "object" ? proposal.before : {};
    return Object.keys(after).map((key) => ({
        key, label: fieldLabels[key] || key,
        before: diffValue(before[key], "未设置"),
        after: diffValue(after[key], "未设置"),
    }));
}
async function ensureThread() {
    if (thread.value?.status === "open")
        return thread.value;
    if (props.artifactType === "task_draft" && !props.objectId) {
        throw new Error("草稿会话尚未初始化，请稍后重试。");
    }
    if (props.artifactType !== "task_draft" && !props.taskId && props.reportId == null) {
        throw new Error("任务会话尚未初始化，请稍后重试。");
    }
    thread.value = await api("/api/interactions", jsonInit("POST", {
        task_id: props.taskId || "", report_id: props.reportId, artifact_type: props.artifactType,
        artifact_version: props.artifactVersion || "", object_id: String(props.objectId || ""),
        scope: { current: props.current || {} },
    }));
    await loadThreads(false);
    return thread.value;
}
async function send() {
    const content = message.value.trim();
    if (!content || pending.value || agentPending.value || readOnly.value)
        return;
    pending.value = true;
    error.value = "";
    try {
        const active = await ensureThread();
        const result = await api(`/api/interactions/${active.id}/messages`, jsonInit("POST", {
            content, async: true,
            request_id: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`,
        }));
        thread.value = result.thread;
        message.value = "";
        await loadThreads(false);
    }
    catch (e) {
        error.value = e.message || "讨论失败";
    }
    finally {
        pending.value = false;
    }
}
async function decide(proposal, decision) {
    try {
        const result = await api(`/api/change-proposals/${proposal.id}/decision`, jsonInit("POST", { decision }));
        thread.value = await api(`/api/interactions/${thread.value.id}`);
        await loadThreads(false);
        if (decision === "accepted")
            emit("applied", result);
    }
    catch (e) {
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
        if (hasContent && !confirm("归档当前会话并开始新对话？历史消息和提案仍会保留。"))
            return;
        try {
            await api(`/api/interactions/${thread.value.id}/close`, { method: "POST" });
        }
        catch (e) {
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
    if (thread.value?.status !== "open")
        return;
    if (thread.value.pending) {
        error.value = "助手仍在处理当前问题，完成后再归档会话。";
        return;
    }
    if (!confirm("归档当前会话？消息和提案会保留在历史中。"))
        return;
    try {
        await api(`/api/interactions/${thread.value.id}/close`, { method: "POST" });
        thread.value = await api(`/api/interactions/${thread.value.id}`);
        await loadThreads(false);
    }
    catch (e) {
        error.value = e?.payload?.error === "INTERACTION_THREAD_BUSY"
            ? "助手或后台修改仍在处理，完成后再归档。" : e.message || "无法归档会话";
    }
}
const __VLS_ctx = {
    ...{},
    ...{},
    ...{},
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['session-title']} */ ;
/** @type {__VLS_StyleScopedClasses['session-title']} */ ;
/** @type {__VLS_StyleScopedClasses['session-title']} */ ;
/** @type {__VLS_StyleScopedClasses['session-title']} */ ;
/** @type {__VLS_StyleScopedClasses['copilot-head-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['copilot-head-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['copilot-head-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['copilot-head-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['copilot-head-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['copilot-head-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['message']} */ ;
/** @type {__VLS_StyleScopedClasses['message']} */ ;
/** @type {__VLS_StyleScopedClasses['message']} */ ;
/** @type {__VLS_StyleScopedClasses['copilot-empty']} */ ;
/** @type {__VLS_StyleScopedClasses['copilot-empty']} */ ;
/** @type {__VLS_StyleScopedClasses['copilot-empty']} */ ;
/** @type {__VLS_StyleScopedClasses['prompt-chips']} */ ;
/** @type {__VLS_StyleScopedClasses['prompt-chips']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-head']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-head']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-head']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-head']} */ ;
/** @type {__VLS_StyleScopedClasses['thread-list']} */ ;
/** @type {__VLS_StyleScopedClasses['thread-list']} */ ;
/** @type {__VLS_StyleScopedClasses['thread-list']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['thread-line']} */ ;
/** @type {__VLS_StyleScopedClasses['thread-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['thread-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['thread-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['thread-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['proposal-list']} */ ;
/** @type {__VLS_StyleScopedClasses['proposal-head']} */ ;
/** @type {__VLS_StyleScopedClasses['proposal-head']} */ ;
/** @type {__VLS_StyleScopedClasses['changed-fields']} */ ;
/** @type {__VLS_StyleScopedClasses['diff-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['diff-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['diff-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['diff-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['diff-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['diff-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['proposal-state']} */ ;
/** @type {__VLS_StyleScopedClasses['composer']} */ ;
/** @type {__VLS_StyleScopedClasses['composer']} */ ;
/** @type {__VLS_StyleScopedClasses['readonly-notice']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['conversation-proposal']} */ ;
/** @type {__VLS_StyleScopedClasses['proposal-head']} */ ;
/** @type {__VLS_StyleScopedClasses['session-title']} */ ;
/** @type {__VLS_StyleScopedClasses['copilot-head-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['message']} */ ;
/** @type {__VLS_StyleScopedClasses['side-drawer']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
    ...{ class: "copilot" },
    ...{ class: ({ compact: __VLS_ctx.compact }) },
});
/** @type {__VLS_StyleScopedClasses['copilot']} */ ;
/** @type {__VLS_StyleScopedClasses['compact']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({
    ...{ class: "copilot-toolbar" },
});
/** @type {__VLS_StyleScopedClasses['copilot-toolbar']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "session-title" },
});
/** @type {__VLS_StyleScopedClasses['session-title']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
(__VLS_ctx.thread?.id ? __VLS_ctx.artifactLabel() : "新对话");
if (__VLS_ctx.thread?.id) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
    (__VLS_ctx.thread.status === "closed" ? "已归档，只读" : "修改前需确认提案");
}
else {
    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
}
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "copilot-head-actions" },
});
/** @type {__VLS_StyleScopedClasses['copilot-head-actions']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
    ...{ onClick: (...[$event]) => {
            return (__VLS_ctx.historyOpen = !__VLS_ctx.historyOpen);
            // @ts-ignore
            [compact, thread, thread, thread, artifactLabel, historyOpen, historyOpen,];
        } },
    type: "button",
    ...{ class: ({ active: __VLS_ctx.historyOpen }) },
});
/** @type {__VLS_StyleScopedClasses['active']} */ ;
if (__VLS_ctx.threads.length) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (__VLS_ctx.threads.length);
}
__VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
    ...{ onClick: (__VLS_ctx.startNewConversation) },
    ...{ class: "new-chat" },
    type: "button",
});
/** @type {__VLS_StyleScopedClasses['new-chat']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "conversation-workspace" },
});
/** @type {__VLS_StyleScopedClasses['conversation-workspace']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ref: "conversationStream",
    ...{ class: "conversation-stream" },
});
/** @type {__VLS_StyleScopedClasses['conversation-stream']} */ ;
if (__VLS_ctx.conversationItems.length) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "messages" },
    });
    /** @type {__VLS_StyleScopedClasses['messages']} */ ;
    for (const [item] of __VLS_vFor((__VLS_ctx.conversationItems))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.template)({
            key: (`${item.itemType}:${item.id}`),
        });
        if (item.itemType === 'message') {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "message" },
                ...{ class: (item.role) },
            });
            /** @type {__VLS_StyleScopedClasses['message']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
            (item.role === "user" ? "你" : "助手");
            __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
            (item.content);
        }
        else {
            __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({
                ...{ class: "proposal conversation-proposal" },
            });
            /** @type {__VLS_StyleScopedClasses['proposal']} */ ;
            /** @type {__VLS_StyleScopedClasses['conversation-proposal']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "proposal-head" },
            });
            /** @type {__VLS_StyleScopedClasses['proposal-head']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
            (item.status === "proposed" ? `建议修改${__VLS_ctx.artifactLabel(item)}` : "提案处理结果");
            __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
            (item.rationale);
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                ...{ class: "badge" },
                ...{ class: (item.risk_level === 'high' ? 'danger' : item.risk_level === 'medium' ? 'warning' : '') },
            });
            /** @type {__VLS_StyleScopedClasses['badge']} */ ;
            (item.risk_level);
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "changed-fields" },
            });
            /** @type {__VLS_StyleScopedClasses['changed-fields']} */ ;
            for (const [row] of __VLS_vFor((__VLS_ctx.proposalDiffRows(item)))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                    key: (row.key),
                });
                (row.label);
                // @ts-ignore
                [artifactLabel, historyOpen, threads, threads, startNewConversation, conversationItems, conversationItems, proposalDiffRows,];
            }
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.conversationItems.length))
                            throw 0;
                        if (!!(item.itemType === 'message'))
                            throw 0;
                        return (__VLS_ctx.toggleProposal(item));
                        // @ts-ignore
                        [toggleProposal,];
                    } },
                ...{ class: "proposal-toggle" },
                type: "button",
            });
            /** @type {__VLS_StyleScopedClasses['proposal-toggle']} */ ;
            (__VLS_ctx.proposalExpanded(item) ? "收起修改详情" : "查看修改前后");
            if (__VLS_ctx.proposalExpanded(item)) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    ...{ class: "diff-preview" },
                });
                /** @type {__VLS_StyleScopedClasses['diff-preview']} */ ;
                for (const [row] of __VLS_vFor((__VLS_ctx.proposalDiffRows(item)))) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
                        key: (row.key),
                    });
                    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
                    (row.label);
                    __VLS_asFunctionalElement1(__VLS_intrinsics.del, __VLS_intrinsics.del)({});
                    (row.before);
                    __VLS_asFunctionalElement1(__VLS_intrinsics.ins, __VLS_intrinsics.ins)({});
                    (row.after);
                    // @ts-ignore
                    [proposalDiffRows, proposalExpanded, proposalExpanded,];
                }
                __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
                (item.impact?.invalidates?.join("、") || "局部检查");
            }
            if (item.status === 'proposed') {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    ...{ class: "button-row" },
                });
                /** @type {__VLS_StyleScopedClasses['button-row']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                    ...{ onClick: (...[$event]) => {
                            if (!(__VLS_ctx.conversationItems.length))
                                throw 0;
                            if (!!(item.itemType === 'message'))
                                throw 0;
                            if (!(item.status === 'proposed'))
                                throw 0;
                            return (__VLS_ctx.decide(item, 'accepted'));
                            // @ts-ignore
                            [decide,];
                        } },
                    ...{ class: "btn primary" },
                });
                /** @type {__VLS_StyleScopedClasses['btn']} */ ;
                /** @type {__VLS_StyleScopedClasses['primary']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                    ...{ onClick: (...[$event]) => {
                            if (!(__VLS_ctx.conversationItems.length))
                                throw 0;
                            if (!!(item.itemType === 'message'))
                                throw 0;
                            if (!(item.status === 'proposed'))
                                throw 0;
                            return (__VLS_ctx.decide(item, 'rejected'));
                            // @ts-ignore
                            [decide,];
                        } },
                    ...{ class: "btn" },
                });
                /** @type {__VLS_StyleScopedClasses['btn']} */ ;
            }
            else {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    ...{ class: "proposal-state" },
                });
                /** @type {__VLS_StyleScopedClasses['proposal-state']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                    ...{ class: "badge" },
                    ...{ class: (item.execution_status === 'failed' ? 'danger' : 'success') },
                });
                /** @type {__VLS_StyleScopedClasses['badge']} */ ;
                (item.status === "rejected" ? "已拒绝" : __VLS_ctx.executionLabel(item));
                if (item.candidate_version_id) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
                    (item.candidate_version_id);
                }
                if (item.execution_error) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
                    (item.execution_error);
                }
            }
        }
        // @ts-ignore
        [executionLabel,];
    }
}
else {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "copilot-empty" },
    });
    /** @type {__VLS_StyleScopedClasses['copilot-empty']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (__VLS_ctx.threads.length ? "开始一段新讨论" : "与助手讨论当前产物");
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
    if (__VLS_ctx.threads.length) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.conversationItems.length))
                        throw 0;
                    if (!(__VLS_ctx.threads.length))
                        throw 0;
                    return (__VLS_ctx.historyOpen = true);
                    // @ts-ignore
                    [historyOpen, threads, threads,];
                } },
            type: "button",
        });
        (__VLS_ctx.threads.length);
    }
}
if (__VLS_ctx.suggestedPrompts?.length && !__VLS_ctx.thread?.messages?.length) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "prompt-chips" },
    });
    /** @type {__VLS_StyleScopedClasses['prompt-chips']} */ ;
    for (const [prompt] of __VLS_vFor((__VLS_ctx.suggestedPrompts))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.suggestedPrompts?.length && !__VLS_ctx.thread?.messages?.length))
                        throw 0;
                    return (__VLS_ctx.message = prompt);
                    // @ts-ignore
                    [thread, threads, suggestedPrompts, suggestedPrompts, message,];
                } },
            key: (prompt),
            type: "button",
        });
        (prompt);
        // @ts-ignore
        [];
    }
}
if (__VLS_ctx.agentPending) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "agent-working" },
    });
    /** @type {__VLS_StyleScopedClasses['agent-working']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "working-dot" },
    });
    /** @type {__VLS_StyleScopedClasses['working-dot']} */ ;
}
if (__VLS_ctx.historyOpen) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.historyOpen))
                    throw 0;
                return (__VLS_ctx.historyOpen = false);
                // @ts-ignore
                [historyOpen, historyOpen, agentPending,];
            } },
        ...{ class: "drawer-backdrop" },
    });
    /** @type {__VLS_StyleScopedClasses['drawer-backdrop']} */ ;
}
if (__VLS_ctx.historyOpen) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
        ...{ class: "side-drawer history-drawer" },
    });
    /** @type {__VLS_StyleScopedClasses['side-drawer']} */ ;
    /** @type {__VLS_StyleScopedClasses['history-drawer']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "drawer-head" },
    });
    /** @type {__VLS_StyleScopedClasses['drawer-head']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "drawer-actions" },
    });
    /** @type {__VLS_StyleScopedClasses['drawer-actions']} */ ;
    if (__VLS_ctx.thread?.status === 'open') {
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (__VLS_ctx.archiveCurrent) },
        });
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.historyOpen))
                    throw 0;
                return (__VLS_ctx.historyOpen = false);
                // @ts-ignore
                [thread, historyOpen, historyOpen, archiveCurrent,];
            } },
    });
    if (__VLS_ctx.loadingHistory) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "drawer-empty" },
        });
        /** @type {__VLS_StyleScopedClasses['drawer-empty']} */ ;
    }
    else if (__VLS_ctx.threads.length) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "thread-list" },
        });
        /** @type {__VLS_StyleScopedClasses['thread-list']} */ ;
        for (const [item] of __VLS_vFor((__VLS_ctx.threads))) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.historyOpen))
                            throw 0;
                        if (!!(__VLS_ctx.loadingHistory))
                            throw 0;
                        if (!(__VLS_ctx.threads.length))
                            throw 0;
                        return (__VLS_ctx.selectThread(item));
                        // @ts-ignore
                        [threads, threads, loadingHistory, selectThread,];
                    } },
                key: (item.id),
                type: "button",
                ...{ class: ({ active: item.id === __VLS_ctx.thread?.id }) },
            });
            /** @type {__VLS_StyleScopedClasses['active']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                ...{ class: "thread-line" },
            });
            /** @type {__VLS_StyleScopedClasses['thread-line']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
            (__VLS_ctx.artifactLabel(item));
            __VLS_asFunctionalElement1(__VLS_intrinsics.time, __VLS_intrinsics.time)({});
            (__VLS_ctx.formatTime(item.updated_at));
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                ...{ class: "thread-preview" },
            });
            /** @type {__VLS_StyleScopedClasses['thread-preview']} */ ;
            (item.last_message?.content || "尚无消息");
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                ...{ class: "thread-meta" },
            });
            /** @type {__VLS_StyleScopedClasses['thread-meta']} */ ;
            (item.message_count);
            if (item.pending_proposal_count) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.i, __VLS_intrinsics.i)({});
                (item.pending_proposal_count);
            }
            else if (item.proposal_count) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.i, __VLS_intrinsics.i)({});
                (item.proposal_count);
            }
            if (item.status === 'closed') {
                __VLS_asFunctionalElement1(__VLS_intrinsics.em, __VLS_intrinsics.em)({});
            }
            // @ts-ignore
            [thread, artifactLabel, formatTime,];
        }
    }
    else {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "drawer-empty" },
        });
        /** @type {__VLS_StyleScopedClasses['drawer-empty']} */ ;
    }
}
if (__VLS_ctx.readOnly) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "readonly-notice" },
    });
    /** @type {__VLS_StyleScopedClasses['readonly-notice']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (__VLS_ctx.startNewConversation) },
    });
}
else {
    __VLS_asFunctionalElement1(__VLS_intrinsics.form, __VLS_intrinsics.form)({
        ...{ onSubmit: (__VLS_ctx.send) },
        ...{ class: "composer" },
    });
    /** @type {__VLS_StyleScopedClasses['composer']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.textarea, __VLS_intrinsics.textarea)({
        value: (__VLS_ctx.message),
        rows: "3",
        placeholder: "输入问题或修改要求…",
    });
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ class: "btn primary" },
        disabled: (__VLS_ctx.pending || __VLS_ctx.agentPending || !__VLS_ctx.message.trim()),
    });
    /** @type {__VLS_StyleScopedClasses['btn']} */ ;
    /** @type {__VLS_StyleScopedClasses['primary']} */ ;
    (__VLS_ctx.pending ? "正在提交…" : __VLS_ctx.agentPending ? "助手分析中" : "发送");
}
if (__VLS_ctx.error) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
        ...{ class: "error-text" },
    });
    /** @type {__VLS_StyleScopedClasses['error-text']} */ ;
    (__VLS_ctx.error);
}
// @ts-ignore
[startNewConversation, message, message, agentPending, agentPending, readOnly, send, pending, pending, error, error,];
const __VLS_export = (await import('vue')).defineComponent({
    __typeEmits: {},
    __typeProps: {},
});
export default {};
