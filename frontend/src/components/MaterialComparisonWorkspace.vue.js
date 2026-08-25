import { computed, ref } from "vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { api, jsonInit } from "@/api/http";
const props = defineProps();
const emit = defineEmits();
const qc = useQueryClient();
const files = ref();
const focus = ref("");
const baseVersionId = ref(props.versions?.[0]?.id || null);
const selectedId = ref(null);
const pending = ref(false);
const error = ref("");
const runs = useQuery({ queryKey: ["material-comparisons", props.reportId], queryFn: () => api(`/api/reports/${props.reportId}/material-comparisons`), refetchInterval: 5000 });
const detail = useQuery({ queryKey: ["material-comparison", selectedId], queryFn: () => api(`/api/material-comparisons/${selectedId.value}`), enabled: computed(() => Boolean(selectedId.value)), refetchInterval: q => q.state.data?.status === "ready" || q.state.data?.status === "failed" ? false : 4000 });
const labels = { addition: "新增", corroboration: "补强", refinement: "细化", update: "更新", conflict: "冲突", weakening: "削弱", irrelevant: "无关", uncertain: "待确认" };
async function create() {
    if (!files.value?.files?.length) {
        error.value = "请选择新增材料";
        return;
    }
    pending.value = true;
    error.value = "";
    try {
        const form = new FormData();
        form.set("focus", focus.value);
        if (baseVersionId.value)
            form.set("base_version_id", String(baseVersionId.value));
        for (const file of files.value.files)
            form.append("files", file);
        const result = await api(`/api/reports/${props.reportId}/material-comparisons`, { method: "POST", body: form });
        selectedId.value = result.comparison_id;
        await qc.invalidateQueries({ queryKey: ["material-comparisons", props.reportId] });
    }
    catch (e) {
        error.value = e.message || "创建失败";
    }
    finally {
        pending.value = false;
    }
}
async function review(item, status) {
    await api(`/api/material-comparisons/${selectedId.value}/items/${item.id}`, jsonInit("PATCH", { status }));
    await qc.invalidateQueries({ queryKey: ["material-comparison", selectedId] });
}
async function handoff() {
    try {
        emit("handoff", await api(`/api/material-comparisons/${selectedId.value}/update-handoff`, { method: "POST" }));
    }
    catch (e) {
        error.value = e.message || "请先接受需要进入报告更新的变化";
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
/** @type {__VLS_StyleScopedClasses['comparison-workspace']} */ ;
/** @type {__VLS_StyleScopedClasses['comparison-workspace']} */ ;
/** @type {__VLS_StyleScopedClasses['comparison-workspace']} */ ;
/** @type {__VLS_StyleScopedClasses['comparison-workspace']} */ ;
/** @type {__VLS_StyleScopedClasses['comparison-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['comparison-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['comparison-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['comparison-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['create-box']} */ ;
/** @type {__VLS_StyleScopedClasses['run-item']} */ ;
/** @type {__VLS_StyleScopedClasses['run-item']} */ ;
/** @type {__VLS_StyleScopedClasses['summary-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['summary-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['summary-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['summary-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['summary-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['summary-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['change-item']} */ ;
/** @type {__VLS_StyleScopedClasses['change-item']} */ ;
/** @type {__VLS_StyleScopedClasses['change-item']} */ ;
/** @type {__VLS_StyleScopedClasses['change-item']} */ ;
/** @type {__VLS_StyleScopedClasses['change-head']} */ ;
/** @type {__VLS_StyleScopedClasses['change-head']} */ ;
/** @type {__VLS_StyleScopedClasses['evidence-compare']} */ ;
/** @type {__VLS_StyleScopedClasses['evidence-compare']} */ ;
/** @type {__VLS_StyleScopedClasses['evidence-compare']} */ ;
/** @type {__VLS_StyleScopedClasses['comparison-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['comparison-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['comparison-workspace']} */ ;
/** @type {__VLS_StyleScopedClasses['summary-strip']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
    ...{ class: "comparison-workspace" },
});
/** @type {__VLS_StyleScopedClasses['comparison-workspace']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
    ...{ onClick: (...[$event]) => {
            return (__VLS_ctx.$emit('close'));
            // @ts-ignore
            [$emit,];
        } },
    ...{ class: "btn tertiary" },
});
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "comparison-grid" },
});
/** @type {__VLS_StyleScopedClasses['comparison-grid']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "create-box" },
});
/** @type {__VLS_StyleScopedClasses['create-box']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.label, __VLS_intrinsics.label)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.select, __VLS_intrinsics.select)({
    value: (__VLS_ctx.baseVersionId),
});
for (const [version] of __VLS_vFor((__VLS_ctx.versions))) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
        key: (version.id),
        value: (version.id),
    });
    (version.version_label || version.version_no);
    // @ts-ignore
    [baseVersionId, versions,];
}
__VLS_asFunctionalElement1(__VLS_intrinsics.label, __VLS_intrinsics.label)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.input)({
    ref: "files",
    type: "file",
    multiple: true,
});
__VLS_asFunctionalElement1(__VLS_intrinsics.label, __VLS_intrinsics.label)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.textarea, __VLS_intrinsics.textarea)({
    value: (__VLS_ctx.focus),
    rows: "2",
    placeholder: "可选；不填写则自动判断全部变化",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
    ...{ onClick: (__VLS_ctx.create) },
    ...{ class: "btn primary" },
    disabled: (__VLS_ctx.pending),
});
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['primary']} */ ;
(__VLS_ctx.pending ? '正在创建…' : '开始异步对比');
for (const [run] of __VLS_vFor((__VLS_ctx.runs.data.value || []))) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                return (__VLS_ctx.selectedId = run.id);
                // @ts-ignore
                [focus, create, pending, pending, runs, selectedId,];
            } },
        key: (run.id),
        ...{ class: "run-item" },
        ...{ class: ({ active: __VLS_ctx.selectedId === run.id }) },
    });
    /** @type {__VLS_StyleScopedClasses['run-item']} */ ;
    /** @type {__VLS_StyleScopedClasses['active']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (run.created_at);
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (run.status === 'ready' ? '等待审阅' : run.status === 'failed' ? '失败' : '运行中');
    (run.summary?.new_fact_count || 0);
    // @ts-ignore
    [selectedId,];
}
if (__VLS_ctx.detail.data.value) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.main, __VLS_intrinsics.main)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "summary-strip" },
    });
    /** @type {__VLS_StyleScopedClasses['summary-strip']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
    (__VLS_ctx.detail.data.value.summary?.new_fact_count || 0);
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
    (__VLS_ctx.detail.data.value.summary?.change_counts?.conflict || 0);
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
    (__VLS_ctx.detail.data.value.summary?.affected_sections?.length || 0);
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    if (__VLS_ctx.detail.data.value.status !== 'ready') {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "notice" },
        });
        /** @type {__VLS_StyleScopedClasses['notice']} */ ;
        (__VLS_ctx.detail.data.value.status === 'failed' ? __VLS_ctx.detail.data.value.error : '系统正在解析和比较新增材料，完成后可集中审阅。');
    }
    for (const [item] of __VLS_vFor((__VLS_ctx.detail.data.value.items || []))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({
            key: (item.id),
            ...{ class: "change-item" },
            ...{ class: (item.change_type) },
        });
        /** @type {__VLS_StyleScopedClasses['change-item']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "change-head" },
        });
        /** @type {__VLS_StyleScopedClasses['change-head']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
            ...{ class: "badge" },
        });
        /** @type {__VLS_StyleScopedClasses['badge']} */ ;
        (__VLS_ctx.labels[item.change_type] || item.change_type);
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (item.title);
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        (item.confidence === 'high' ? '高' : item.confidence === 'low' ? '低' : '中');
        __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
        (item.rationale);
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "evidence-compare" },
        });
        /** @type {__VLS_StyleScopedClasses['evidence-compare']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
        (item.evidence?.baseline_fact?.content || '无对应事实');
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
        (item.evidence?.new_fact?.content);
        __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
            ...{ class: "muted" },
        });
        /** @type {__VLS_StyleScopedClasses['muted']} */ ;
        (item.impact?.report_locations?.map((x) => x.section).filter(Boolean).join('、') || '尚未映射到既有章节');
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "button-row" },
        });
        /** @type {__VLS_StyleScopedClasses['button-row']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.detail.data.value))
                        throw 0;
                    return (__VLS_ctx.review(item, 'accepted'));
                    // @ts-ignore
                    [detail, detail, detail, detail, detail, detail, detail, detail, labels, review,];
                } },
            ...{ class: "btn" },
            ...{ class: ({ primary: item.status === 'accepted' }) },
        });
        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
        /** @type {__VLS_StyleScopedClasses['primary']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.detail.data.value))
                        throw 0;
                    return (__VLS_ctx.review(item, 'needs_verification'));
                    // @ts-ignore
                    [review,];
                } },
            ...{ class: "btn tertiary" },
        });
        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
        /** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.detail.data.value))
                        throw 0;
                    return (__VLS_ctx.review(item, 'ignored'));
                    // @ts-ignore
                    [review,];
                } },
            ...{ class: "btn tertiary" },
        });
        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
        /** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
        // @ts-ignore
        [];
    }
    if (__VLS_ctx.detail.data.value.status === 'ready') {
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (__VLS_ctx.handoff) },
            ...{ class: "btn primary handoff" },
        });
        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
        /** @type {__VLS_StyleScopedClasses['primary']} */ ;
        /** @type {__VLS_StyleScopedClasses['handoff']} */ ;
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
if (__VLS_ctx.error) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
        ...{ class: "error-text" },
    });
    /** @type {__VLS_StyleScopedClasses['error-text']} */ ;
    (__VLS_ctx.error);
}
// @ts-ignore
[detail, handoff, error, error,];
const __VLS_export = (await import('vue')).defineComponent({
    __typeEmits: {},
    __typeProps: {},
});
export default {};
