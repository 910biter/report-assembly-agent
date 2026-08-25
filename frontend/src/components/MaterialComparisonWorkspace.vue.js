import { computed, ref, watch } from "vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { useRouter } from "vue-router";
import { api, jsonInit } from "@/api/http";
const props = withDefaults(defineProps(), { versions: () => [], comparisonId: null, embedded: false });
const emit = defineEmits();
const router = useRouter();
const qc = useQueryClient();
const files = ref();
const focus = ref("");
const baseVersionId = ref(props.versions?.[0]?.id || null);
const selectedId = ref(props.comparisonId || null);
const selectedItemId = ref(null);
const relationFilter = ref("all");
const pending = ref(false);
const error = ref("");
watch(() => props.comparisonId, value => { if (value)
    selectedId.value = value; }, { immediate: true });
const runs = useQuery({
    queryKey: ["material-comparisons", props.reportId],
    queryFn: () => api(`/api/reports/${props.reportId}/material-comparisons`),
    enabled: computed(() => !props.embedded), refetchInterval: 5000,
});
const detail = useQuery({
    queryKey: ["material-comparison", selectedId],
    queryFn: () => api(`/api/material-comparisons/${selectedId.value}`),
    enabled: computed(() => Boolean(selectedId.value)),
    refetchInterval: q => ["ready", "failed"].includes(String(q.state.data?.status)) ? false : 4000,
});
const labels = {
    addition: "新增事实", corroboration: "新增佐证", refinement: "补充细节",
    update: "后续发展", conflict: "事实冲突", weakening: "证据削弱",
    related: "相关信息", irrelevant: "暂不相关", uncertain: "待核验",
};
const filterOrder = ["conflict", "update", "corroboration", "refinement", "addition", "weakening", "related", "uncertain"];
const items = computed(() => detail.data.value?.items || []);
const visibleItems = computed(() => relationFilter.value === "all"
    ? items.value.filter(item => item.change_type !== "irrelevant")
    : items.value.filter(item => item.change_type === relationFilter.value));
const selectedItem = computed(() => items.value.find(item => item.id === selectedItemId.value) || null);
const relationCounts = computed(() => {
    const counts = {};
    for (const item of items.value)
        counts[item.change_type] = (counts[item.change_type] || 0) + 1;
    return counts;
});
const documentSections = computed(() => {
    const sections = [];
    const sectionMap = new Map();
    for (const sentence of detail.data.value?.document?.sentences || []) {
        const section = String(sentence.section || "正文");
        const paragraph = String(sentence.paragraph ?? 0);
        if (!sectionMap.has(section))
            sectionMap.set(section, new Map());
        const paragraphs = sectionMap.get(section);
        if (!paragraphs.has(paragraph))
            paragraphs.set(paragraph, []);
        paragraphs.get(paragraph).push(sentence);
    }
    for (const [title, paragraphs] of sectionMap) {
        sections.push({ title, paragraphs: [...paragraphs].map(([id, sentences]) => ({ id, sentences })) });
    }
    return sections;
});
watch(items, value => {
    if (value.length && !value.some(item => item.id === selectedItemId.value)) {
        selectedItemId.value = value.find(item => item.change_type !== "irrelevant")?.id || value[0].id;
    }
}, { immediate: true });
function changesFor(sentence) {
    const changes = sentence.changes || [];
    return relationFilter.value === "all" ? changes : changes.filter((item) => item.change_type === relationFilter.value);
}
function selectSentence(sentence) { const changes = changesFor(sentence); if (changes.length)
    selectedItemId.value = changes[0].item_id; }
function selectItem(item) {
    selectedItemId.value = item.id;
    requestAnimationFrame(() => document.querySelector(`[data-change-item="${item.id}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" }));
}
function evidenceLabel(item) { return [item.source_file, item.page ? `第 ${item.page} 页` : "", item.paragraph ? `第 ${item.paragraph} 段` : ""].filter(Boolean).join(" · "); }
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
        await qc.invalidateQueries({ queryKey: ["material-comparisons", props.reportId] });
        await router.push(`/tasks/${result.task_id}?tab=comparison`);
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
        error.value = e.message || "请先确认需要进入报告更新的变化";
    }
}
const __VLS_defaults = { versions: () => [], comparisonId: null, embedded: false };
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
/** @type {__VLS_StyleScopedClasses['comparison-workspace']} */ ;
/** @type {__VLS_StyleScopedClasses['comparison-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['create-box']} */ ;
/** @type {__VLS_StyleScopedClasses['run-item']} */ ;
/** @type {__VLS_StyleScopedClasses['run-item']} */ ;
/** @type {__VLS_StyleScopedClasses['result-head']} */ ;
/** @type {__VLS_StyleScopedClasses['result-head']} */ ;
/** @type {__VLS_StyleScopedClasses['summary-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['summary-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['summary-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['summary-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['summary-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['relation-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['relation-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['relation-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['relation-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['relation-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['change-index']} */ ;
/** @type {__VLS_StyleScopedClasses['change-index']} */ ;
/** @type {__VLS_StyleScopedClasses['change-index']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['change-index']} */ ;
/** @type {__VLS_StyleScopedClasses['change-index']} */ ;
/** @type {__VLS_StyleScopedClasses['baseline-document']} */ ;
/** @type {__VLS_StyleScopedClasses['baseline-document']} */ ;
/** @type {__VLS_StyleScopedClasses['baseline-document']} */ ;
/** @type {__VLS_StyleScopedClasses['baseline-document']} */ ;
/** @type {__VLS_StyleScopedClasses['baseline-document']} */ ;
/** @type {__VLS_StyleScopedClasses['baseline-document']} */ ;
/** @type {__VLS_StyleScopedClasses['document-note']} */ ;
/** @type {__VLS_StyleScopedClasses['report-sentence']} */ ;
/** @type {__VLS_StyleScopedClasses['report-sentence']} */ ;
/** @type {__VLS_StyleScopedClasses['report-sentence']} */ ;
/** @type {__VLS_StyleScopedClasses['report-sentence']} */ ;
/** @type {__VLS_StyleScopedClasses['report-sentence']} */ ;
/** @type {__VLS_StyleScopedClasses['report-sentence']} */ ;
/** @type {__VLS_StyleScopedClasses['report-sentence']} */ ;
/** @type {__VLS_StyleScopedClasses['evidence-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['evidence-head']} */ ;
/** @type {__VLS_StyleScopedClasses['relation-badge']} */ ;
/** @type {__VLS_StyleScopedClasses['conflict']} */ ;
/** @type {__VLS_StyleScopedClasses['relation-badge']} */ ;
/** @type {__VLS_StyleScopedClasses['weakening']} */ ;
/** @type {__VLS_StyleScopedClasses['relation-badge']} */ ;
/** @type {__VLS_StyleScopedClasses['update']} */ ;
/** @type {__VLS_StyleScopedClasses['relation-badge']} */ ;
/** @type {__VLS_StyleScopedClasses['corroboration']} */ ;
/** @type {__VLS_StyleScopedClasses['evidence-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['fact-block']} */ ;
/** @type {__VLS_StyleScopedClasses['fact-block']} */ ;
/** @type {__VLS_StyleScopedClasses['fact-block']} */ ;
/** @type {__VLS_StyleScopedClasses['source-list']} */ ;
/** @type {__VLS_StyleScopedClasses['source-list']} */ ;
/** @type {__VLS_StyleScopedClasses['source-list']} */ ;
/** @type {__VLS_StyleScopedClasses['source-list']} */ ;
/** @type {__VLS_StyleScopedClasses['source-list']} */ ;
/** @type {__VLS_StyleScopedClasses['review-layout']} */ ;
/** @type {__VLS_StyleScopedClasses['baseline-document']} */ ;
/** @type {__VLS_StyleScopedClasses['baseline-document']} */ ;
/** @type {__VLS_StyleScopedClasses['comparison-workspace']} */ ;
/** @type {__VLS_StyleScopedClasses['comparison-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['with-runs']} */ ;
/** @type {__VLS_StyleScopedClasses['run-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['review-layout']} */ ;
/** @type {__VLS_StyleScopedClasses['relation-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['evidence-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['summary-strip']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
    ...{ class: "comparison-workspace" },
    ...{ class: ({ embedded: __VLS_ctx.embedded }) },
});
/** @type {__VLS_StyleScopedClasses['comparison-workspace']} */ ;
/** @type {__VLS_StyleScopedClasses['embedded']} */ ;
if (!__VLS_ctx.embedded) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!(!__VLS_ctx.embedded))
                    throw 0;
                return (__VLS_ctx.$emit('close'));
                // @ts-ignore
                [embedded, embedded, $emit,];
            } },
        ...{ class: "btn tertiary" },
    });
    /** @type {__VLS_StyleScopedClasses['btn']} */ ;
    /** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
}
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "comparison-shell" },
    ...{ class: ({ 'with-runs': !__VLS_ctx.embedded }) },
});
/** @type {__VLS_StyleScopedClasses['comparison-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['with-runs']} */ ;
if (!__VLS_ctx.embedded) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
        ...{ class: "run-panel" },
    });
    /** @type {__VLS_StyleScopedClasses['run-panel']} */ ;
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
        [embedded, embedded, baseVersionId, versions,];
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
        placeholder: "可选；默认检查全部事实变化",
    });
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (__VLS_ctx.create) },
        ...{ class: "btn primary" },
        disabled: (__VLS_ctx.pending),
    });
    /** @type {__VLS_StyleScopedClasses['btn']} */ ;
    /** @type {__VLS_StyleScopedClasses['primary']} */ ;
    (__VLS_ctx.pending ? "正在创建…" : "创建材料对比任务");
    for (const [run] of __VLS_vFor((__VLS_ctx.runs.data.value || []))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(!__VLS_ctx.embedded))
                        throw 0;
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
        (run.status === "ready" ? "等待审阅" : run.status === "failed" ? "失败" : "运行中");
        (run.summary?.new_fact_count || 0);
        // @ts-ignore
        [selectedId,];
    }
}
if (__VLS_ctx.detail.data.value) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.main, __VLS_intrinsics.main)({
        ...{ class: "result-workspace" },
    });
    /** @type {__VLS_StyleScopedClasses['result-workspace']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "result-head" },
    });
    /** @type {__VLS_StyleScopedClasses['result-head']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
    (__VLS_ctx.detail.data.value.baseline?.version_label || __VLS_ctx.detail.data.value.base_version_id);
    __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
    (__VLS_ctx.detail.data.value.baseline?.title || "报告材料变化");
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
    if (__VLS_ctx.detail.data.value.status !== 'ready') {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "notice" },
            ...{ class: ({ warning: __VLS_ctx.detail.data.value.status === 'failed' }) },
        });
        /** @type {__VLS_StyleScopedClasses['notice']} */ ;
        /** @type {__VLS_StyleScopedClasses['warning']} */ ;
        (__VLS_ctx.detail.data.value.status === "failed" ? __VLS_ctx.detail.data.value.error : "正在解析新增材料并建立事实关联，已有结果会保留。");
    }
    else {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "review-layout" },
        });
        /** @type {__VLS_StyleScopedClasses['review-layout']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
            ...{ class: "relation-panel" },
        });
        /** @type {__VLS_StyleScopedClasses['relation-panel']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.h3, __VLS_intrinsics.h3)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.detail.data.value))
                        throw 0;
                    if (!!(__VLS_ctx.detail.data.value.status !== 'ready'))
                        throw 0;
                    return (__VLS_ctx.relationFilter = 'all');
                    // @ts-ignore
                    [detail, detail, detail, detail, detail, detail, detail, detail, detail, detail, detail, relationFilter,];
                } },
            ...{ class: ({ active: __VLS_ctx.relationFilter === 'all' }) },
        });
        /** @type {__VLS_StyleScopedClasses['active']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (__VLS_ctx.items.filter(x => x.change_type !== 'irrelevant').length);
        for (const [type] of __VLS_vFor((__VLS_ctx.filterOrder.filter(x => __VLS_ctx.relationCounts[x])))) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.detail.data.value))
                            throw 0;
                        if (!!(__VLS_ctx.detail.data.value.status !== 'ready'))
                            throw 0;
                        return (__VLS_ctx.relationFilter = type);
                        // @ts-ignore
                        [relationFilter, relationFilter, items, filterOrder, relationCounts,];
                    } },
                key: (type),
                ...{ class: ([type, { active: __VLS_ctx.relationFilter === type }]) },
            });
            /** @type {__VLS_StyleScopedClasses['active']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
            (__VLS_ctx.labels[type]);
            __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
            (__VLS_ctx.relationCounts[type]);
            // @ts-ignore
            [relationFilter, relationCounts, labels,];
        }
        if (__VLS_ctx.relationCounts.irrelevant) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.details, __VLS_intrinsics.details)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.summary, __VLS_intrinsics.summary)({});
            (__VLS_ctx.relationCounts.irrelevant);
            for (const [item] of __VLS_vFor((__VLS_ctx.items.filter(x => x.change_type === 'irrelevant')))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                    ...{ onClick: (...[$event]) => {
                            if (!(__VLS_ctx.detail.data.value))
                                throw 0;
                            if (!!(__VLS_ctx.detail.data.value.status !== 'ready'))
                                throw 0;
                            if (!(__VLS_ctx.relationCounts.irrelevant))
                                throw 0;
                            return (__VLS_ctx.selectItem(item));
                            // @ts-ignore
                            [items, relationCounts, relationCounts, selectItem,];
                        } },
                    key: (item.id),
                    ...{ class: "minor-item" },
                });
                /** @type {__VLS_StyleScopedClasses['minor-item']} */ ;
                (item.title);
                // @ts-ignore
                [];
            }
        }
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "change-index" },
        });
        /** @type {__VLS_StyleScopedClasses['change-index']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
        for (const [item] of __VLS_vFor((__VLS_ctx.visibleItems))) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.detail.data.value))
                            throw 0;
                        if (!!(__VLS_ctx.detail.data.value.status !== 'ready'))
                            throw 0;
                        return (__VLS_ctx.selectItem(item));
                        // @ts-ignore
                        [selectItem, visibleItems,];
                    } },
                key: (item.id),
                ...{ class: ({ active: __VLS_ctx.selectedItemId === item.id }) },
            });
            /** @type {__VLS_StyleScopedClasses['active']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.i, __VLS_intrinsics.i)({
                ...{ class: (item.change_type) },
            });
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
            (item.title);
            // @ts-ignore
            [selectedItemId,];
        }
        __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({
            ...{ class: "baseline-document" },
        });
        /** @type {__VLS_StyleScopedClasses['baseline-document']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "document-note" },
        });
        /** @type {__VLS_StyleScopedClasses['document-note']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
        for (const [section] of __VLS_vFor((__VLS_ctx.documentSections))) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
                key: (section.title),
            });
            __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
            (section.title);
            for (const [paragraph] of __VLS_vFor((section.paragraphs))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
                    key: (paragraph.id),
                });
                for (const [sentence] of __VLS_vFor((paragraph.sentences))) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                        ...{ onClick: (...[$event]) => {
                                if (!(__VLS_ctx.detail.data.value))
                                    throw 0;
                                if (!!(__VLS_ctx.detail.data.value.status !== 'ready'))
                                    throw 0;
                                return (__VLS_ctx.selectSentence(sentence));
                                // @ts-ignore
                                [documentSections, selectSentence,];
                            } },
                        key: (sentence.id),
                        ...{ class: "report-sentence" },
                        ...{ class: ([__VLS_ctx.changesFor(sentence)[0]?.change_type, { affected: __VLS_ctx.changesFor(sentence).length, selected: __VLS_ctx.changesFor(sentence).some((x) => x.item_id === __VLS_ctx.selectedItemId) }]) },
                        'data-change-item': (__VLS_ctx.changesFor(sentence)[0]?.item_id),
                    });
                    /** @type {__VLS_StyleScopedClasses['report-sentence']} */ ;
                    /** @type {__VLS_StyleScopedClasses['affected']} */ ;
                    /** @type {__VLS_StyleScopedClasses['selected']} */ ;
                    (sentence.text);
                    if (__VLS_ctx.changesFor(sentence).length) {
                        __VLS_asFunctionalElement1(__VLS_intrinsics.sup, __VLS_intrinsics.sup)({});
                        (__VLS_ctx.changesFor(sentence).length);
                    }
                    // @ts-ignore
                    [selectedItemId, changesFor, changesFor, changesFor, changesFor, changesFor, changesFor,];
                }
                // @ts-ignore
                [];
            }
            // @ts-ignore
            [];
        }
        if (!__VLS_ctx.documentSections.length) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "empty" },
            });
            /** @type {__VLS_StyleScopedClasses['empty']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
        }
        __VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
            ...{ class: "evidence-panel" },
        });
        /** @type {__VLS_StyleScopedClasses['evidence-panel']} */ ;
        if (__VLS_ctx.selectedItem) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "evidence-head" },
            });
            /** @type {__VLS_StyleScopedClasses['evidence-head']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                ...{ class: "relation-badge" },
                ...{ class: (__VLS_ctx.selectedItem.change_type) },
            });
            /** @type {__VLS_StyleScopedClasses['relation-badge']} */ ;
            (__VLS_ctx.labels[__VLS_ctx.selectedItem.change_type] || __VLS_ctx.selectedItem.change_type);
            __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
            (__VLS_ctx.selectedItem.confidence === "high" ? "高" : __VLS_ctx.selectedItem.confidence === "low" ? "低" : "中");
            __VLS_asFunctionalElement1(__VLS_intrinsics.h3, __VLS_intrinsics.h3)({});
            (__VLS_ctx.selectedItem.title);
            __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
                ...{ class: "rationale" },
            });
            /** @type {__VLS_StyleScopedClasses['rationale']} */ ;
            (__VLS_ctx.selectedItem.rationale);
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "fact-block baseline" },
            });
            /** @type {__VLS_StyleScopedClasses['fact-block']} */ ;
            /** @type {__VLS_StyleScopedClasses['baseline']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
            (__VLS_ctx.selectedItem.evidence?.baseline_fact?.content || "报告中没有对应事实，这是独立新增信息。");
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "fact-block current" },
            });
            /** @type {__VLS_StyleScopedClasses['fact-block']} */ ;
            /** @type {__VLS_StyleScopedClasses['current']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
            (__VLS_ctx.selectedItem.evidence?.new_fact?.content);
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "source-list" },
            });
            /** @type {__VLS_StyleScopedClasses['source-list']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.h4, __VLS_intrinsics.h4)({});
            for (const [source] of __VLS_vFor((__VLS_ctx.selectedItem.evidence?.new_fact?.evidence || []))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.blockquote, __VLS_intrinsics.blockquote)({
                    key: (source.evidence_id || source.unit_id),
                });
                __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
                (__VLS_ctx.evidenceLabel(source));
                __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
                (source.quote);
                __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
                (source.unit_id);
                // @ts-ignore
                [labels, documentSections, selectedItem, selectedItem, selectedItem, selectedItem, selectedItem, selectedItem, selectedItem, selectedItem, selectedItem, selectedItem, selectedItem, evidenceLabel,];
            }
            if (!__VLS_ctx.selectedItem.evidence?.new_fact?.evidence?.length) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
                    ...{ class: "muted" },
                });
                /** @type {__VLS_StyleScopedClasses['muted']} */ ;
            }
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "review-actions" },
            });
            /** @type {__VLS_StyleScopedClasses['review-actions']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.detail.data.value))
                            throw 0;
                        if (!!(__VLS_ctx.detail.data.value.status !== 'ready'))
                            throw 0;
                        if (!(__VLS_ctx.selectedItem))
                            throw 0;
                        return (__VLS_ctx.review(__VLS_ctx.selectedItem, 'accepted'));
                        // @ts-ignore
                        [selectedItem, selectedItem, review,];
                    } },
                ...{ class: "btn" },
                ...{ class: ({ primary: __VLS_ctx.selectedItem.status === 'accepted' }) },
            });
            /** @type {__VLS_StyleScopedClasses['btn']} */ ;
            /** @type {__VLS_StyleScopedClasses['primary']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.detail.data.value))
                            throw 0;
                        if (!!(__VLS_ctx.detail.data.value.status !== 'ready'))
                            throw 0;
                        if (!(__VLS_ctx.selectedItem))
                            throw 0;
                        return (__VLS_ctx.review(__VLS_ctx.selectedItem, 'needs_verification'));
                        // @ts-ignore
                        [selectedItem, selectedItem, review,];
                    } },
                ...{ class: "btn" },
                ...{ class: ({ primary: __VLS_ctx.selectedItem.status === 'needs_verification' }) },
            });
            /** @type {__VLS_StyleScopedClasses['btn']} */ ;
            /** @type {__VLS_StyleScopedClasses['primary']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.detail.data.value))
                            throw 0;
                        if (!!(__VLS_ctx.detail.data.value.status !== 'ready'))
                            throw 0;
                        if (!(__VLS_ctx.selectedItem))
                            throw 0;
                        return (__VLS_ctx.review(__VLS_ctx.selectedItem, 'ignored'));
                        // @ts-ignore
                        [selectedItem, selectedItem, review,];
                    } },
                ...{ class: "btn tertiary" },
            });
            /** @type {__VLS_StyleScopedClasses['btn']} */ ;
            /** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
        }
        else {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "empty" },
            });
            /** @type {__VLS_StyleScopedClasses['empty']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
        }
    }
    if (!__VLS_ctx.embedded && __VLS_ctx.detail.data.value.status === 'ready') {
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (__VLS_ctx.handoff) },
            ...{ class: "btn handoff" },
        });
        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
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
    (__VLS_ctx.embedded ? "正在读取对比结果" : "选择一次对比");
    (__VLS_ctx.embedded ? "完成分析后将在这里逐句展示材料变化。" : "查看新材料相对于报告基线带来的变化。");
}
if (__VLS_ctx.error) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
        ...{ class: "error-text" },
    });
    /** @type {__VLS_StyleScopedClasses['error-text']} */ ;
    (__VLS_ctx.error);
}
// @ts-ignore
[embedded, embedded, embedded, detail, handoff, error, error,];
const __VLS_export = (await import('vue')).defineComponent({
    __typeEmits: {},
    __typeProps: {},
    props: {},
});
export default {};
