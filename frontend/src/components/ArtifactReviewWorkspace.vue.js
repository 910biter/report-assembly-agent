import { computed, ref, watch } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { api } from "@/api/http";
const props = defineProps();
const artifactType = ref("task_brief");
const search = ref("");
const offset = ref(0);
const selected = ref(null);
const pageSize = 40;
const workspace = useQuery({
    queryKey: ["review-workspace", props.taskId, artifactType, search, offset],
    queryFn: () => api(`/api/tasks/${props.taskId}/review-workspace?artifact_type=${encodeURIComponent(artifactType.value)}&q=${encodeURIComponent(search.value)}&offset=${offset.value}&limit=${pageSize}`),
    staleTime: 5000,
});
const items = computed(() => workspace.data.value?.items || []);
watch(items, (value) => {
    if (!value.length || (selected.value && !value.some((item) => item.object_id === selected.value.object_id)))
        selected.value = null;
}, { immediate: true });
watch([artifactType, search], () => {
    offset.value = 0;
    selected.value = null;
});
function chooseType(value) {
    artifactType.value = value;
}
function locateQualityIssue(item) {
    if (!props.reportId)
        return;
    const params = new URLSearchParams({ panel: "qa" });
    if (item?.current?.sentence_id)
        params.set("qa_sentence", String(item.current.sentence_id));
    else if (item?.current?.section)
        params.set("qa_section", String(item.current.section));
    location.href = `/reports/${props.reportId}?${params.toString()}`;
}
function discussWithAssistant(item) {
    window.dispatchEvent(new CustomEvent("ira:assistant-focus", {
        detail: { taskId: props.taskId, artifact: item },
    }));
}
function toggleItem(item) {
    selected.value = selected.value?.object_id === item.object_id ? null : item;
}
const fieldLabels = {
    theme: "报告主题", requirements: "报告要求", task_intent: "任务目标",
    content: "内容", title: "标题", summary: "摘要", objective: "本章目的",
    core_question: "核心问题", core_message: "核心观点", narrative_logic: "组织逻辑",
    chapters: "章节安排", chapter_plans: "章节安排", target_words: "目标篇幅",
    material_role: "材料角色", claim_support: "能够证明", allowed_usage: "适合用途",
    forbidden_usage: "使用边界", missing_information: "缺失信息", dimension: "分析维度",
    fact_type: "事实类型", confidence_level: "可信程度", reasoning_chain: "判断依据",
    based_fact_ids: "依据事实", section: "所在章节", quote: "问题原文", note: "问题说明",
    message: "问题说明", severity: "影响程度", problem_type: "问题类型",
};
const detailFields = {
    task_brief: ["theme", "requirements", "task_intent"],
    material_role: ["summary", "material_role", "claim_support", "allowed_usage", "forbidden_usage", "missing_information"],
    analysis_plan: ["objective", "core_question", "dimensions", "required_dimensions", "narrative_logic"],
    fact: ["content", "dimension", "fact_type"],
    inference: ["content", "confidence_level", "reasoning_chain", "based_fact_ids"],
    final_plan: ["objective", "core_message", "narrative_logic", "chapters", "chapter_plans", "target_words"],
    narrative_plan: ["section", "core_message", "objective", "narrative_logic", "subsections", "paragraphs"],
    qa_issue: ["problem_type", "severity", "section", "quote", "note", "message"],
    comparison_item: ["content", "summary", "change_type", "relation_type", "section"],
};
function readable(value) {
    if (value == null || value === "")
        return "";
    if (Array.isArray(value)) {
        return value.map((item, index) => {
            if (item == null)
                return "";
            if (typeof item !== "object")
                return String(item);
            const title = item.display_title || item.title || item.name || item.content || item.summary;
            return title ? `${index + 1}. ${title}` : "";
        }).filter(Boolean).join("\n");
    }
    if (typeof value === "object") {
        return ["title", "content", "summary", "objective", "core_message"]
            .map((key) => value[key]).filter(Boolean).join("\n");
    }
    if (typeof value === "boolean")
        return value ? "是" : "否";
    return String(value);
}
function detailRows(item) {
    const current = item?.current || {};
    const preferred = detailFields[item?.artifact_type] || ["title", "content", "summary", "section", "note"];
    return preferred
        .map((key) => ({ key, label: fieldLabels[key] || "相关内容", value: readable(current[key]) }))
        .filter((row) => row.value);
}
const __VLS_ctx = {
    ...{},
    ...{},
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['collaboration-head']} */ ;
/** @type {__VLS_StyleScopedClasses['collaboration-head']} */ ;
/** @type {__VLS_StyleScopedClasses['collaboration-head']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-groups']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-groups']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-groups']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-groups']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-groups']} */ ;
/** @type {__VLS_StyleScopedClasses['browser-tools']} */ ;
/** @type {__VLS_StyleScopedClasses['browser-tools']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-list']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-heading']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-list']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-list']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-list']} */ ;
/** @type {__VLS_StyleScopedClasses['inline-review']} */ ;
/** @type {__VLS_StyleScopedClasses['inline-review']} */ ;
/** @type {__VLS_StyleScopedClasses['inline-review']} */ ;
/** @type {__VLS_StyleScopedClasses['inline-review']} */ ;
/** @type {__VLS_StyleScopedClasses['inline-review']} */ ;
/** @type {__VLS_StyleScopedClasses['inline-review']} */ ;
/** @type {__VLS_StyleScopedClasses['pager']} */ ;
/** @type {__VLS_StyleScopedClasses['collaboration-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['collaboration-grid']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
    ...{ class: "collaboration-shell" },
});
/** @type {__VLS_StyleScopedClasses['collaboration-shell']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({
    ...{ class: "collaboration-head" },
});
/** @type {__VLS_StyleScopedClasses['collaboration-head']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
    ...{ class: "badge" },
});
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "collaboration-grid" },
});
/** @type {__VLS_StyleScopedClasses['collaboration-grid']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
    ...{ class: "artifact-groups" },
});
/** @type {__VLS_StyleScopedClasses['artifact-groups']} */ ;
for (const [group] of __VLS_vFor((__VLS_ctx.workspace.data.value?.groups || []))) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                return (__VLS_ctx.chooseType(group.artifact_type));
                // @ts-ignore
                [workspace, chooseType,];
            } },
        key: (group.artifact_type),
        disabled: (!group.available),
        ...{ class: ({ active: __VLS_ctx.artifactType === group.artifact_type }) },
    });
    /** @type {__VLS_StyleScopedClasses['active']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (group.label);
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (group.count);
    // @ts-ignore
    [artifactType,];
}
__VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
    ...{ class: "artifact-browser" },
});
/** @type {__VLS_StyleScopedClasses['artifact-browser']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "browser-tools" },
});
/** @type {__VLS_StyleScopedClasses['browser-tools']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.input)({
    placeholder: "搜索当前阶段产物",
});
(__VLS_ctx.search);
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
(__VLS_ctx.workspace.data.value?.total || 0);
if (__VLS_ctx.items.length) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "artifact-list" },
    });
    /** @type {__VLS_StyleScopedClasses['artifact-list']} */ ;
    for (const [item] of __VLS_vFor((__VLS_ctx.items))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({
            key: (`${item.artifact_type}:${item.object_id}`),
            ...{ class: ({ active: __VLS_ctx.selected?.object_id === item.object_id }) },
        });
        /** @type {__VLS_StyleScopedClasses['active']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.items.length))
                        throw 0;
                    return (__VLS_ctx.toggleItem(item));
                    // @ts-ignore
                    [workspace, search, items, items, selected, toggleItem,];
                } },
            type: "button",
        });
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
            ...{ class: "artifact-heading" },
        });
        /** @type {__VLS_StyleScopedClasses['artifact-heading']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (item.title);
        __VLS_asFunctionalElement1(__VLS_intrinsics.i, __VLS_intrinsics.i)({});
        (__VLS_ctx.selected?.object_id === item.object_id ? "收起" : "查看");
        if (__VLS_ctx.selected?.object_id !== item.object_id) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
            (item.summary || "打开查看详情");
        }
        if (__VLS_ctx.selected?.object_id === item.object_id) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "inline-review" },
            });
            /** @type {__VLS_StyleScopedClasses['inline-review']} */ ;
            if (__VLS_ctx.detailRows(item).length) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.dl, __VLS_intrinsics.dl)({});
                for (const [row] of __VLS_vFor((__VLS_ctx.detailRows(item)))) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.template)({
                        key: (row.key),
                    });
                    __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
                    (row.label);
                    __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
                    (row.value);
                    // @ts-ignore
                    [selected, selected, selected, detailRows, detailRows,];
                }
            }
            else {
                __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
            }
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "inline-actions" },
            });
            /** @type {__VLS_StyleScopedClasses['inline-actions']} */ ;
            if (item.artifact_type === 'qa_issue' && __VLS_ctx.reportId && (item.current?.sentence_id || item.current?.section)) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                    ...{ onClick: (...[$event]) => {
                            if (!(__VLS_ctx.items.length))
                                throw 0;
                            if (!(__VLS_ctx.selected?.object_id === item.object_id))
                                throw 0;
                            if (!(item.artifact_type === 'qa_issue' && __VLS_ctx.reportId && (item.current?.sentence_id || item.current?.section)))
                                throw 0;
                            return (__VLS_ctx.locateQualityIssue(item));
                            // @ts-ignore
                            [reportId, locateQualityIssue,];
                        } },
                    ...{ class: "btn" },
                    type: "button",
                });
                /** @type {__VLS_StyleScopedClasses['btn']} */ ;
            }
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.items.length))
                            throw 0;
                        if (!(__VLS_ctx.selected?.object_id === item.object_id))
                            throw 0;
                        return (__VLS_ctx.discussWithAssistant(item));
                        // @ts-ignore
                        [discussWithAssistant,];
                    } },
                ...{ class: "btn primary" },
                type: "button",
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
        ...{ class: "empty" },
    });
    /** @type {__VLS_StyleScopedClasses['empty']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
}
if ((__VLS_ctx.workspace.data.value?.total || 0) > __VLS_ctx.pageSize) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "pager" },
    });
    /** @type {__VLS_StyleScopedClasses['pager']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!((__VLS_ctx.workspace.data.value?.total || 0) > __VLS_ctx.pageSize))
                    throw 0;
                return (__VLS_ctx.offset = Math.max(0, __VLS_ctx.offset - __VLS_ctx.pageSize));
                // @ts-ignore
                [workspace, pageSize, pageSize, offset, offset,];
            } },
        ...{ class: "btn" },
        disabled: (__VLS_ctx.offset === 0),
    });
    /** @type {__VLS_StyleScopedClasses['btn']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (__VLS_ctx.offset + 1);
    (Math.min(__VLS_ctx.offset + __VLS_ctx.pageSize, __VLS_ctx.workspace.data.value.total));
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!((__VLS_ctx.workspace.data.value?.total || 0) > __VLS_ctx.pageSize))
                    throw 0;
                return (__VLS_ctx.offset += __VLS_ctx.pageSize);
                // @ts-ignore
                [workspace, pageSize, pageSize, offset, offset, offset, offset,];
            } },
        ...{ class: "btn" },
        disabled: (__VLS_ctx.offset + __VLS_ctx.pageSize >= __VLS_ctx.workspace.data.value.total),
    });
    /** @type {__VLS_StyleScopedClasses['btn']} */ ;
}
// @ts-ignore
[workspace, pageSize, offset,];
const __VLS_export = (await import('vue')).defineComponent({
    __typeProps: {},
});
export default {};
