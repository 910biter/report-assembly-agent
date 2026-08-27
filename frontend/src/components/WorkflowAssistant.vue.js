import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { api } from "@/api/http";
import AppIcon from "@/components/AppIcon.vue";
import ReviewCopilot from "@/components/ReviewCopilot.vue";
import { useUiStore } from "@/stores/ui";
const route = useRoute();
const ui = useUiStore();
ui.ensureDraftId();
const open = ref(false);
const tab = ref("progress");
const task = ref(null);
const report = ref(null);
const workspace = ref(null);
const artifactType = ref("task_brief");
const selected = ref(null);
const references = ref([]);
const draft = computed(() => ui.taskDraft);
const draftId = computed(() => ui.draftId);
const loading = ref(false);
const expanded = ref(false);
const savedPanelSize = (() => {
    try {
        return JSON.parse(localStorage.getItem("ira-assistant-panel-size") || "{}");
    }
    catch {
        return {};
    }
})();
const panelSize = ref({
    width: Number(savedPanelSize.width) || 560,
    height: Number(savedPanelSize.height) || 720,
});
const panelStyle = computed(() => {
    if (expanded.value) {
        return {
            width: `${Math.min(860, window.innerWidth - 48)}px`,
            height: `${Math.max(480, window.innerHeight - 48)}px`,
        };
    }
    return {
        width: `${Math.min(panelSize.value.width, window.innerWidth - 32)}px`,
        height: `${Math.min(panelSize.value.height, window.innerHeight - 100)}px`,
    };
});
let timer;
let stopResize;
function startPanelResize(event) {
    if (window.innerWidth <= 600)
        return;
    event.preventDefault();
    expanded.value = false;
    const origin = {
        x: event.clientX,
        y: event.clientY,
        width: panelSize.value.width,
        height: panelSize.value.height,
    };
    const move = (next) => {
        panelSize.value = {
            width: Math.max(380, Math.min(window.innerWidth - 32, origin.width + origin.x - next.clientX)),
            height: Math.max(480, Math.min(window.innerHeight - 80, origin.height + origin.y - next.clientY)),
        };
    };
    const stop = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", stop);
        localStorage.setItem("ira-assistant-panel-size", JSON.stringify(panelSize.value));
        stopResize = undefined;
    };
    stopResize?.();
    stopResize = stop;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
}
const visible = computed(() => route.path === "/" || route.path.startsWith("/tasks/") || route.path.startsWith("/reports/"));
const taskId = computed(() => String(route.params.taskId || task.value?.task_id || report.value?.task_id || ""));
const reportId = computed(() => Number(route.params.reportId || task.value?.report_id || report.value?.id || 0) || undefined);
const isDraft = computed(() => route.path === "/");
const interactionScopeReady = computed(() => isDraft.value ? Boolean(draftId.value) : Boolean(taskId.value || reportId.value));
const activeArtifact = computed(() => {
    if (isDraft.value) {
        return {
            artifact_type: "task_draft",
            artifact_version: "draft",
            object_id: draftId.value,
            current: draft.value,
            title: "任务需求草案",
        };
    }
    return selected.value || {
        artifact_type: "task_brief",
        artifact_version: String(task.value?.run_revision || 1),
        object_id: "",
        current: {
            theme: task.value?.theme || "",
            requirements: task.value?.user_requirements || "",
            stage: task.value?.stage || "",
            queue_status: task.value?.queue_status || {},
            parse_progress: task.value?.parse_progress || {},
            evidence_progress: task.value?.evidence_progress || {},
            write_progress: task.value?.write_progress || {},
        },
        title: "当前任务",
    };
});
const assistantFocus = computed(() => ({
    ...activeArtifact.value,
    references: references.value,
}));
const prompts = computed(() => {
    if (["sentence", "paragraph", "qa_issue"].includes(activeArtifact.value.artifact_type)) {
        return [
            "解释这段内容存在的问题及其依据。",
            "在不改变事实含义的前提下改写这段内容。",
            "检查这段内容的事实和引用是否匹配。",
        ];
    }
    return isDraft.value
        ? [
            "帮我判断当前需求是否清楚，还缺少哪些业务信息？",
            "根据这个目标，建议报告重点回答哪些问题？",
            "我应该准备哪些类型的材料？",
        ]
        : [
            "当前运行到哪一步，已经完成什么，下一步是什么？",
            "请解释当前阶段的输入、产出和必要性。",
            "如果修改当前产物，后续哪些环节需要重新计算？",
        ];
});
const stageMeta = {
    created: { label: "等待开始", description: "任务目标和材料已登记，尚未进入处理。" },
    parsing: { label: "材料解析", description: "把文件转换为带来源位置的内容单元。" },
    dedup: { label: "去重归并", description: "识别重复材料和重复内容，保留来源关系。" },
    material_analysis: { label: "材料理解", description: "判断材料角色、可证明范围和信息缺口。" },
    planning: { label: "分析规划", description: "确定需要回答的问题和证据提取范围。" },
    evidence: { label: "事实与证据", description: "提取事实并绑定原始材料位置。" },
    conflict: { label: "冲突核验", description: "检查多来源对同一事项是否存在矛盾。" },
    analysis: { label: "综合分析", description: "基于事实形成带依据和置信度的分析判断。" },
    writing: { label: "报告生成", description: "先组织叙事计划，再按章节生成并绑定来源。" },
    knowledge: { label: "深度检查", description: "报告可审核后继续执行知识整理和深度质检。" },
    review: { label: "等待审核", description: "报告草稿已形成，可以审阅、讨论和修改。" },
    done: { label: "已完成", description: "报告已审核，可导出或进行增量更新。" },
    paused: { label: "已暂停", description: "任务停在安全边界，可继续运行。" },
    failed: { label: "运行异常", description: "当前阶段未完成，请查看错误并决定是否重试。" },
};
const currentStage = computed(() => stageMeta[String(task.value?.stage || "created")] || {
    label: String(task.value?.stage || "处理中"), description: "系统正在处理当前任务。",
});
async function loadContext() {
    if (!visible.value || isDraft.value) {
        task.value = null;
        report.value = null;
        workspace.value = null;
        selected.value = null;
        return;
    }
    loading.value = true;
    try {
        if (route.path.startsWith("/tasks/")) {
            task.value = await api(`/api/tasks/${String(route.params.taskId)}/assistant-context`);
            report.value = null;
        }
        else {
            report.value = await api(`/api/reports/${String(route.params.reportId)}/assistant-context`);
            task.value = report.value;
        }
        if (tab.value === "artifacts")
            await loadArtifacts(artifactType.value);
    }
    catch {
        task.value = null;
    }
    finally {
        loading.value = false;
    }
}
async function loadArtifacts(type = artifactType.value) {
    if (!taskId.value)
        return;
    artifactType.value = type;
    workspace.value = await api(`/api/tasks/${taskId.value}/review-workspace?artifact_type=${encodeURIComponent(type)}&offset=0&limit=30`);
    const items = workspace.value?.items || [];
    if (!selected.value || selected.value.artifact_type !== type)
        selected.value = items[0] || null;
}
async function switchTab(value) {
    tab.value = value;
    if (value === "artifacts" && taskId.value)
        await loadArtifacts();
}
function discussArtifact(item) {
    selected.value = selected.value?.object_id === item.object_id ? null : item;
}
function beginArtifactDiscussion(item) {
    selected.value = item;
    addReference(item);
    tab.value = "discuss";
}
function referenceKey(item) {
    return `${item?.artifact_type || "reference"}:${item?.object_id || ""}:${item?.current?.quote || item?.current?.content || ""}`;
}
function referenceSummary(item) {
    return String(item?.current?.quote || item?.current?.content || item?.current?.note || item?.title || "所选内容")
        .replace(/\s+/g, " ").slice(0, 72);
}
function artifactPreview(item) {
    const current = item?.current || {};
    const value = current.requirements || current.content || current.summary || current.objective ||
        current.core_message || current.narrative_logic || current.note || current.quote || item?.summary || "";
    return String(value).replace(/\s+/g, " ").slice(0, 360);
}
function addReference(item) {
    if (!item)
        return;
    const key = referenceKey(item);
    const next = references.value.filter((entry) => referenceKey(entry) !== key);
    references.value = [...next, item].slice(-8);
}
function removeReference(index) {
    references.value = references.value.filter((_item, current) => current !== index);
}
function acceptExternalFocus(event) {
    const detail = event.detail;
    if (!detail || (detail.taskId && taskId.value && String(detail.taskId) !== taskId.value))
        return;
    if (detail.reference)
        addReference(detail.reference);
    if (detail.artifact)
        selected.value = detail.artifact;
    else if (!detail.reference)
        selected.value = detail;
    if (!detail.append && !detail.reference)
        references.value = [];
    open.value = true;
    tab.value = "discuss";
}
function proposalApplied(proposal) {
    if (isDraft.value) {
        ui.applyDraftProposal(proposal?.after || {});
    }
    else {
        loadContext();
    }
}
watch(() => route.fullPath, () => {
    open.value = false;
    tab.value = "progress";
    selected.value = null;
    references.value = [];
    loadContext();
});
onMounted(() => {
    loadContext();
    window.addEventListener("ira:assistant-focus", acceptExternalFocus);
    timer = window.setInterval(() => {
        if (open.value && taskId.value)
            loadContext();
    }, 5000);
});
onBeforeUnmount(() => {
    if (timer)
        window.clearInterval(timer);
    stopResize?.();
    window.removeEventListener("ira:assistant-focus", acceptExternalFocus);
});
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['assistant-orb']} */ ;
/** @type {__VLS_StyleScopedClasses['orb-mark']} */ ;
/** @type {__VLS_StyleScopedClasses['orb-mark']} */ ;
/** @type {__VLS_StyleScopedClasses['orb-mark']} */ ;
/** @type {__VLS_StyleScopedClasses['orb-mark']} */ ;
/** @type {__VLS_StyleScopedClasses['assistant-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['assistant-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['assistant-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['assistant-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['assistant-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['assistant-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['assistant-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['assistant-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['assistant-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-view']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-view']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-view']} */ ;
/** @type {__VLS_StyleScopedClasses['stage-state']} */ ;
/** @type {__VLS_StyleScopedClasses['stage-state']} */ ;
/** @type {__VLS_StyleScopedClasses['stage-state']} */ ;
/** @type {__VLS_StyleScopedClasses['stage-state']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-view']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-view']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-view']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-view']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-types']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-types']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-types']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-types']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-items']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-items']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-items']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-items']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-items']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-items']} */ ;
/** @type {__VLS_StyleScopedClasses['assistant-artifact-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['assistant-artifact-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['discussion-scope']} */ ;
/** @type {__VLS_StyleScopedClasses['discussion-scope']} */ ;
/** @type {__VLS_StyleScopedClasses['discussion-scope']} */ ;
/** @type {__VLS_StyleScopedClasses['discussion-scope']} */ ;
/** @type {__VLS_StyleScopedClasses['assistant-body']} */ ;
/** @type {__VLS_StyleScopedClasses['discussion-view']} */ ;
/** @type {__VLS_StyleScopedClasses['panel-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['workflow-assistant']} */ ;
/** @type {__VLS_StyleScopedClasses['assistant-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['orb-label']} */ ;
/** @type {__VLS_StyleScopedClasses['assistant-orb']} */ ;
/** @type {__VLS_StyleScopedClasses['assistant-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['panel-resize-handle']} */ ;
/** @type {__VLS_StyleScopedClasses['reference-list']} */ ;
/** @type {__VLS_StyleScopedClasses['reference-list']} */ ;
/** @type {__VLS_StyleScopedClasses['reference-list']} */ ;
/** @type {__VLS_StyleScopedClasses['reference-list']} */ ;
/** @type {__VLS_StyleScopedClasses['clear-references']} */ ;
/** @type {__VLS_StyleScopedClasses['focus-context']} */ ;
/** @type {__VLS_StyleScopedClasses['focus-context']} */ ;
if (__VLS_ctx.visible) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "workflow-assistant" },
        ...{ class: ({ open: __VLS_ctx.open }) },
    });
    /** @type {__VLS_StyleScopedClasses['workflow-assistant']} */ ;
    /** @type {__VLS_StyleScopedClasses['open']} */ ;
    if (__VLS_ctx.open) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
            ...{ class: "assistant-panel" },
            ...{ style: (__VLS_ctx.panelStyle) },
        });
        /** @type {__VLS_StyleScopedClasses['assistant-panel']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onPointerdown: (__VLS_ctx.startPanelResize) },
            ...{ class: "panel-resize-handle" },
            type: "button",
            'aria-label': "调整助手窗口大小",
            title: "拖动调整窗口大小",
        });
        /** @type {__VLS_StyleScopedClasses['panel-resize-handle']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
        (__VLS_ctx.isDraft ? "任务创建前" : __VLS_ctx.currentStage.label);
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "panel-actions" },
        });
        /** @type {__VLS_StyleScopedClasses['panel-actions']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.visible))
                        throw 0;
                    if (!(__VLS_ctx.open))
                        throw 0;
                    return (__VLS_ctx.expanded = !__VLS_ctx.expanded);
                    // @ts-ignore
                    [visible, open, open, panelStyle, startPanelResize, isDraft, currentStage, expanded, expanded,];
                } },
            type: "button",
        });
        (__VLS_ctx.expanded ? "还原" : "扩展");
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.visible))
                        throw 0;
                    if (!(__VLS_ctx.open))
                        throw 0;
                    return (__VLS_ctx.open = false);
                    // @ts-ignore
                    [open, expanded,];
                } },
            'aria-label': "关闭助手",
        });
        const __VLS_0 = AppIcon;
        // @ts-ignore
        const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({
            name: "close",
            size: (17),
        }));
        const __VLS_2 = __VLS_1({
            name: "close",
            size: (17),
        }, ...__VLS_functionalComponentArgsRest(__VLS_1));
        __VLS_asFunctionalElement1(__VLS_intrinsics.nav, __VLS_intrinsics.nav)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.visible))
                        throw 0;
                    if (!(__VLS_ctx.open))
                        throw 0;
                    return (__VLS_ctx.switchTab('progress'));
                    // @ts-ignore
                    [switchTab,];
                } },
            ...{ class: ({ active: __VLS_ctx.tab === 'progress' }) },
        });
        /** @type {__VLS_StyleScopedClasses['active']} */ ;
        (__VLS_ctx.isDraft ? "需求" : "任务状态");
        if (!__VLS_ctx.isDraft) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.visible))
                            throw 0;
                        if (!(__VLS_ctx.open))
                            throw 0;
                        if (!(!__VLS_ctx.isDraft))
                            throw 0;
                        return (__VLS_ctx.switchTab('artifacts'));
                        // @ts-ignore
                        [isDraft, isDraft, switchTab, tab,];
                    } },
                ...{ class: ({ active: __VLS_ctx.tab === 'artifacts' }) },
            });
            /** @type {__VLS_StyleScopedClasses['active']} */ ;
        }
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.visible))
                        throw 0;
                    if (!(__VLS_ctx.open))
                        throw 0;
                    return (__VLS_ctx.switchTab('discuss'));
                    // @ts-ignore
                    [switchTab, tab,];
                } },
            ...{ class: ({ active: __VLS_ctx.tab === 'discuss' }) },
        });
        /** @type {__VLS_StyleScopedClasses['active']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "assistant-body" },
            ...{ class: ({ 'discussion-active': __VLS_ctx.tab === 'discuss' }) },
        });
        /** @type {__VLS_StyleScopedClasses['assistant-body']} */ ;
        /** @type {__VLS_StyleScopedClasses['discussion-active']} */ ;
        if (__VLS_ctx.tab === 'progress') {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "progress-view" },
            });
            /** @type {__VLS_StyleScopedClasses['progress-view']} */ ;
            if (__VLS_ctx.isDraft) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.h3, __VLS_intrinsics.h3)({});
                (__VLS_ctx.draft.theme || "尚未填写报告主题");
                __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
                (__VLS_ctx.draft.requirements || "填写业务目标后，可以先和助手讨论报告重点、材料范围和预期结构。");
                __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                    ...{ onClick: (...[$event]) => {
                            if (!(__VLS_ctx.visible))
                                throw 0;
                            if (!(__VLS_ctx.open))
                                throw 0;
                            if (!(__VLS_ctx.tab === 'progress'))
                                throw 0;
                            if (!(__VLS_ctx.isDraft))
                                throw 0;
                            return (__VLS_ctx.tab = 'discuss');
                            // @ts-ignore
                            [isDraft, tab, tab, tab, tab, draft, draft,];
                        } },
                    ...{ class: "ask-link" },
                });
                /** @type {__VLS_StyleScopedClasses['ask-link']} */ ;
            }
            else {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    ...{ class: "stage-state" },
                });
                /** @type {__VLS_StyleScopedClasses['stage-state']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                    ...{ class: (__VLS_ctx.task?.stage) },
                });
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.h3, __VLS_intrinsics.h3)({});
                (__VLS_ctx.currentStage.label);
                __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
                (__VLS_ctx.currentStage.description);
                __VLS_asFunctionalElement1(__VLS_intrinsics.dl, __VLS_intrinsics.dl)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
                (__VLS_ctx.task?.parse_progress?.done || 0);
                (__VLS_ctx.task?.parse_progress?.total || "—");
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
                (__VLS_ctx.task?.evidence_progress?.done || 0);
                (__VLS_ctx.task?.evidence_progress?.total || "—");
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
                (__VLS_ctx.task?.write_progress?.done || 0);
                (__VLS_ctx.task?.write_progress?.total || "—");
                __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                    ...{ onClick: (...[$event]) => {
                            if (!(__VLS_ctx.visible))
                                throw 0;
                            if (!(__VLS_ctx.open))
                                throw 0;
                            if (!(__VLS_ctx.tab === 'progress'))
                                throw 0;
                            if (!!(__VLS_ctx.isDraft))
                                throw 0;
                            return (__VLS_ctx.tab = 'discuss');
                            // @ts-ignore
                            [currentStage, currentStage, tab, task, task, task, task, task, task, task,];
                        } },
                    ...{ class: "ask-link" },
                });
                /** @type {__VLS_StyleScopedClasses['ask-link']} */ ;
            }
        }
        else if (__VLS_ctx.tab === 'artifacts') {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "artifact-view" },
            });
            /** @type {__VLS_StyleScopedClasses['artifact-view']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "artifact-types" },
            });
            /** @type {__VLS_StyleScopedClasses['artifact-types']} */ ;
            for (const [group] of __VLS_vFor((__VLS_ctx.workspace?.groups || []))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                    ...{ onClick: (...[$event]) => {
                            if (!(__VLS_ctx.visible))
                                throw 0;
                            if (!(__VLS_ctx.open))
                                throw 0;
                            if (!!(__VLS_ctx.tab === 'progress'))
                                throw 0;
                            if (!(__VLS_ctx.tab === 'artifacts'))
                                throw 0;
                            return (__VLS_ctx.loadArtifacts(group.artifact_type));
                            // @ts-ignore
                            [tab, workspace, loadArtifacts,];
                        } },
                    key: (group.artifact_type),
                    disabled: (!group.available),
                    ...{ class: ({ active: __VLS_ctx.artifactType === group.artifact_type }) },
                });
                /** @type {__VLS_StyleScopedClasses['active']} */ ;
                (group.label);
                __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
                (group.count);
                // @ts-ignore
                [artifactType,];
            }
            if (__VLS_ctx.workspace?.items?.length) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    ...{ class: "artifact-items" },
                });
                /** @type {__VLS_StyleScopedClasses['artifact-items']} */ ;
                for (const [item] of __VLS_vFor((__VLS_ctx.workspace.items))) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({
                        key: (`${item.artifact_type}:${item.object_id}`),
                        ...{ class: ({ active: __VLS_ctx.selected?.object_id === item.object_id }) },
                    });
                    /** @type {__VLS_StyleScopedClasses['active']} */ ;
                    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                        ...{ onClick: (...[$event]) => {
                                if (!(__VLS_ctx.visible))
                                    throw 0;
                                if (!(__VLS_ctx.open))
                                    throw 0;
                                if (!!(__VLS_ctx.tab === 'progress'))
                                    throw 0;
                                if (!(__VLS_ctx.tab === 'artifacts'))
                                    throw 0;
                                if (!(__VLS_ctx.workspace?.items?.length))
                                    throw 0;
                                return (__VLS_ctx.discussArtifact(item));
                                // @ts-ignore
                                [workspace, workspace, selected, discussArtifact,];
                            } },
                    });
                    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
                    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
                    (item.title);
                    __VLS_asFunctionalElement1(__VLS_intrinsics.i, __VLS_intrinsics.i)({});
                    (__VLS_ctx.selected?.object_id === item.object_id ? "收起" : "查看");
                    if (__VLS_ctx.selected?.object_id !== item.object_id) {
                        __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
                        (item.summary || "查看内容");
                    }
                    if (__VLS_ctx.selected?.object_id === item.object_id) {
                        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                            ...{ class: "assistant-artifact-detail" },
                        });
                        /** @type {__VLS_StyleScopedClasses['assistant-artifact-detail']} */ ;
                        __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
                        (__VLS_ctx.artifactPreview(item) || "该产物已形成，可交给助手结合任务上下文解释。");
                        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                            ...{ onClick: (...[$event]) => {
                                    if (!(__VLS_ctx.visible))
                                        throw 0;
                                    if (!(__VLS_ctx.open))
                                        throw 0;
                                    if (!!(__VLS_ctx.tab === 'progress'))
                                        throw 0;
                                    if (!(__VLS_ctx.tab === 'artifacts'))
                                        throw 0;
                                    if (!(__VLS_ctx.workspace?.items?.length))
                                        throw 0;
                                    if (!(__VLS_ctx.selected?.object_id === item.object_id))
                                        throw 0;
                                    return (__VLS_ctx.beginArtifactDiscussion(item));
                                    // @ts-ignore
                                    [selected, selected, selected, artifactPreview, beginArtifactDiscussion,];
                                } },
                            ...{ class: "btn primary" },
                        });
                        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
                        /** @type {__VLS_StyleScopedClasses['primary']} */ ;
                    }
                    // @ts-ignore
                    [];
                }
            }
            else {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    ...{ class: "assistant-empty" },
                });
                /** @type {__VLS_StyleScopedClasses['assistant-empty']} */ ;
            }
        }
        else {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "discussion-view" },
            });
            /** @type {__VLS_StyleScopedClasses['discussion-view']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "discussion-scope" },
            });
            /** @type {__VLS_StyleScopedClasses['discussion-scope']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
            (__VLS_ctx.activeArtifact.title);
            if (__VLS_ctx.selected || __VLS_ctx.isDraft) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    ...{ class: "focus-context" },
                });
                /** @type {__VLS_StyleScopedClasses['focus-context']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
                (__VLS_ctx.artifactPreview(__VLS_ctx.activeArtifact) || "当前产物已作为对话上下文。");
                if (__VLS_ctx.selected && !__VLS_ctx.isDraft) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                        ...{ onClick: (...[$event]) => {
                                if (!(__VLS_ctx.visible))
                                    throw 0;
                                if (!(__VLS_ctx.open))
                                    throw 0;
                                if (!!(__VLS_ctx.tab === 'progress'))
                                    throw 0;
                                if (!!(__VLS_ctx.tab === 'artifacts'))
                                    throw 0;
                                if (!(__VLS_ctx.selected || __VLS_ctx.isDraft))
                                    throw 0;
                                if (!(__VLS_ctx.selected && !__VLS_ctx.isDraft))
                                    throw 0;
                                return (__VLS_ctx.tab = 'artifacts');
                                // @ts-ignore
                                [isDraft, isDraft, tab, selected, selected, artifactPreview, activeArtifact, activeArtifact,];
                            } },
                        type: "button",
                    });
                }
            }
            if (__VLS_ctx.references.length) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    ...{ class: "reference-list" },
                });
                /** @type {__VLS_StyleScopedClasses['reference-list']} */ ;
                for (const [item, index] of __VLS_vFor((__VLS_ctx.references))) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                        key: (__VLS_ctx.referenceKey(item)),
                    });
                    __VLS_asFunctionalElement1(__VLS_intrinsics.i, __VLS_intrinsics.i)({});
                    (__VLS_ctx.referenceSummary(item));
                    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                        ...{ onClick: (...[$event]) => {
                                if (!(__VLS_ctx.visible))
                                    throw 0;
                                if (!(__VLS_ctx.open))
                                    throw 0;
                                if (!!(__VLS_ctx.tab === 'progress'))
                                    throw 0;
                                if (!!(__VLS_ctx.tab === 'artifacts'))
                                    throw 0;
                                if (!(__VLS_ctx.references.length))
                                    throw 0;
                                return (__VLS_ctx.removeReference(index));
                                // @ts-ignore
                                [references, references, referenceKey, referenceSummary, removeReference,];
                            } },
                        type: "button",
                        'aria-label': "移除引用",
                    });
                    // @ts-ignore
                    [];
                }
                __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                    ...{ onClick: (...[$event]) => {
                            if (!(__VLS_ctx.visible))
                                throw 0;
                            if (!(__VLS_ctx.open))
                                throw 0;
                            if (!!(__VLS_ctx.tab === 'progress'))
                                throw 0;
                            if (!!(__VLS_ctx.tab === 'artifacts'))
                                throw 0;
                            if (!(__VLS_ctx.references.length))
                                throw 0;
                            return (__VLS_ctx.references = []);
                            // @ts-ignore
                            [references,];
                        } },
                    type: "button",
                    ...{ class: "clear-references" },
                });
                /** @type {__VLS_StyleScopedClasses['clear-references']} */ ;
            }
            if (__VLS_ctx.interactionScopeReady) {
                const __VLS_5 = ReviewCopilot;
                // @ts-ignore
                const __VLS_6 = __VLS_asFunctionalComponent1(__VLS_5, new __VLS_5({
                    ...{ 'onApplied': {} },
                    key: (__VLS_ctx.isDraft ? `draft:${__VLS_ctx.draftId}` : `task:${__VLS_ctx.taskId}`),
                    compact: true,
                    taskId: (__VLS_ctx.taskId),
                    reportId: (__VLS_ctx.isDraft ? __VLS_ctx.reportId : undefined),
                    artifactType: (__VLS_ctx.isDraft ? 'task_draft' : 'task_control'),
                    artifactVersion: (__VLS_ctx.isDraft ? 'draft' : ''),
                    objectId: (__VLS_ctx.isDraft ? __VLS_ctx.draftId : ''),
                    current: (__VLS_ctx.isDraft ? __VLS_ctx.activeArtifact.current : { task_id: __VLS_ctx.taskId, stage: __VLS_ctx.task?.stage || 'created' }),
                    focus: (__VLS_ctx.assistantFocus),
                    suggestedPrompts: (__VLS_ctx.prompts),
                }));
                const __VLS_7 = __VLS_6({
                    ...{ 'onApplied': {} },
                    key: (__VLS_ctx.isDraft ? `draft:${__VLS_ctx.draftId}` : `task:${__VLS_ctx.taskId}`),
                    compact: true,
                    taskId: (__VLS_ctx.taskId),
                    reportId: (__VLS_ctx.isDraft ? __VLS_ctx.reportId : undefined),
                    artifactType: (__VLS_ctx.isDraft ? 'task_draft' : 'task_control'),
                    artifactVersion: (__VLS_ctx.isDraft ? 'draft' : ''),
                    objectId: (__VLS_ctx.isDraft ? __VLS_ctx.draftId : ''),
                    current: (__VLS_ctx.isDraft ? __VLS_ctx.activeArtifact.current : { task_id: __VLS_ctx.taskId, stage: __VLS_ctx.task?.stage || 'created' }),
                    focus: (__VLS_ctx.assistantFocus),
                    suggestedPrompts: (__VLS_ctx.prompts),
                }, ...__VLS_functionalComponentArgsRest(__VLS_6));
                let __VLS_10;
                const __VLS_11 = {
                    /** @type {typeof __VLS_10.applied} */
                    onApplied: (__VLS_ctx.proposalApplied),
                };
                var __VLS_8;
                var __VLS_9;
            }
            else {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    ...{ class: "assistant-empty" },
                });
                /** @type {__VLS_StyleScopedClasses['assistant-empty']} */ ;
            }
        }
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.visible))
                    throw 0;
                return (__VLS_ctx.open = !__VLS_ctx.open);
                // @ts-ignore
                [open, open, isDraft, isDraft, isDraft, isDraft, isDraft, isDraft, task, activeArtifact, interactionScopeReady, draftId, draftId, taskId, taskId, taskId, reportId, assistantFocus, prompts, proposalApplied,];
            } },
        ...{ class: "assistant-orb" },
        'aria-label': (__VLS_ctx.open ? '关闭报告协作助手' : '打开报告协作助手'),
    });
    /** @type {__VLS_StyleScopedClasses['assistant-orb']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "orb-mark" },
    });
    /** @type {__VLS_StyleScopedClasses['orb-mark']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.i, __VLS_intrinsics.i)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.i, __VLS_intrinsics.i)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.i, __VLS_intrinsics.i)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "orb-label" },
    });
    /** @type {__VLS_StyleScopedClasses['orb-label']} */ ;
    (__VLS_ctx.isDraft ? "讨论需求" : "任务助手");
}
// @ts-ignore
[open, isDraft,];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
