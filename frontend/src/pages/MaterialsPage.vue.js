import { computed, ref } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { RouterLink } from "vue-router";
import { api } from "@/api/http";
import AppIcon from "@/components/AppIcon.vue";
const search = ref("");
const type = ref("");
const status = ref("");
const usage = ref("");
const dateRange = ref("all");
const sortBy = ref("recent");
const selected = ref(null);
const detailTab = ref("overview");
const list = useQuery({ queryKey: ["materials"], queryFn: () => api("/api/materials") });
const detail = useQuery({ queryKey: computed(() => ["material", selected.value]), queryFn: () => api(`/api/materials/${selected.value}`), enabled: computed(() => selected.value !== null) });
const types = computed(() => [...new Set((list.data.value || []).map(item => item.file_type).filter(Boolean))].sort());
function taskCount(item) { return item.tasks?.length || 0; }
function withinDate(item) {
    if (dateRange.value === "all")
        return true;
    const value = Date.parse(item.parsed_at || "");
    return Number.isFinite(value) && value >= Date.now() - Number(dateRange.value) * 86_400_000;
}
function matchesStatus(item) {
    if (!status.value)
        return true;
    if (status.value === "duplicate")
        return Boolean(item.is_duplicate);
    if (status.value === "ready")
        return item.parse_status === "ready" && !item.is_duplicate;
    return item.parse_status === status.value;
}
function matchesUsage(item) {
    if (!usage.value)
        return true;
    const count = taskCount(item);
    return usage.value === "unused" ? count === 0 : usage.value === "reused" ? count > 1 : count === 1;
}
const filtered = computed(() => {
    const keyword = search.value.trim().toLowerCase();
    return (list.data.value || [])
        .filter(item => !keyword || `${item.filename} ${(item.tasks || []).map(task => typeof task === "string" ? task : `${task.theme || ""} ${task.task_id}`).join(" ")}`.toLowerCase().includes(keyword))
        .filter(item => !type.value || item.file_type === type.value).filter(matchesStatus).filter(matchesUsage).filter(withinDate)
        .sort((a, b) => {
        if (sortBy.value === "name")
            return a.filename.localeCompare(b.filename, "zh-CN");
        if (sortBy.value === "units")
            return b.unit_count - a.unit_count;
        if (sortBy.value === "usage")
            return taskCount(b) - taskCount(a);
        return Date.parse(b.parsed_at || "") - Date.parse(a.parsed_at || "") || b.id - a.id;
    });
});
const hasFilters = computed(() => Boolean(search.value || type.value || status.value || usage.value || dateRange.value !== "all" || sortBy.value !== "recent"));
function resetFilters() { search.value = ""; type.value = ""; status.value = ""; usage.value = ""; dateRange.value = "all"; sortBy.value = "recent"; }
function selectMaterial(id) { selected.value = id; detailTab.value = "overview"; }
function formatDate(value) { if (!value)
    return "尚未解析"; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }
function statusLabel(item) { return item.is_duplicate ? "重复" : item.parse_status === "ready" ? "已解析" : item.parse_status === "error" ? "异常" : "待解析"; }
function unitKindLabel(value) { return { text: "正文", paragraph: "段落", table: "表格", image: "图片", picture: "图片", heading: "标题" }[String(value || "").toLowerCase()] || "内容"; }
const metadataRows = computed(() => {
    const metadata = detail.data.value?.units?.[0]?.metadata || {};
    const labels = {
        source_type: "内容来源", language: "识别语言", page_count: "页数",
        has_ocr: "文字识别", has_tables: "表格识别", has_images: "图片识别",
        title: "文档标题", author: "作者", created_at: "创建时间",
    };
    return Object.entries(metadata).flatMap(([key, value]) => {
        if (!labels[key] || value == null || typeof value === "object")
            return [];
        const display = typeof value === "boolean" ? (value ? "已启用" : "未发现") : String(value);
        return [{ key, label: labels[key], value: display }];
    });
});
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['material-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['material-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['has-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['filter-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['material-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['has-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['material-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['has-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['material-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['has-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['material-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['has-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['material-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['has-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['material-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['has-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['search-box']} */ ;
/** @type {__VLS_StyleScopedClasses['search-box']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['file-name']} */ ;
/** @type {__VLS_StyleScopedClasses['file-name']} */ ;
/** @type {__VLS_StyleScopedClasses['file-name']} */ ;
/** @type {__VLS_StyleScopedClasses['file-name']} */ ;
/** @type {__VLS_StyleScopedClasses['parse-state']} */ ;
/** @type {__VLS_StyleScopedClasses['parse-state']} */ ;
/** @type {__VLS_StyleScopedClasses['parse-state']} */ ;
/** @type {__VLS_StyleScopedClasses['parse-state']} */ ;
/** @type {__VLS_StyleScopedClasses['parse-state']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-head']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-head']} */ ;
/** @type {__VLS_StyleScopedClasses['icon-button']} */ ;
/** @type {__VLS_StyleScopedClasses['material-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-body']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-body']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-body']} */ ;
/** @type {__VLS_StyleScopedClasses['task-use']} */ ;
/** @type {__VLS_StyleScopedClasses['task-use']} */ ;
/** @type {__VLS_StyleScopedClasses['unit-list']} */ ;
/** @type {__VLS_StyleScopedClasses['unit-list']} */ ;
/** @type {__VLS_StyleScopedClasses['unit-list']} */ ;
/** @type {__VLS_StyleScopedClasses['unit-list']} */ ;
/** @type {__VLS_StyleScopedClasses['unit-list']} */ ;
/** @type {__VLS_StyleScopedClasses['unit-list']} */ ;
/** @type {__VLS_StyleScopedClasses['metadata']} */ ;
/** @type {__VLS_StyleScopedClasses['filter-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['search-box']} */ ;
/** @type {__VLS_StyleScopedClasses['material-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['has-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['material-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['material-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['filter-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['search-box']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "page-stack materials-page" },
});
/** @type {__VLS_StyleScopedClasses['page-stack']} */ ;
/** @type {__VLS_StyleScopedClasses['materials-page']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({
    ...{ class: "page-header" },
});
/** @type {__VLS_StyleScopedClasses['page-header']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.h1, __VLS_intrinsics.h1)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
    ...{ class: "surface material-shell" },
    ...{ class: ({ 'has-detail': __VLS_ctx.selected !== null }) },
});
/** @type {__VLS_StyleScopedClasses['surface']} */ ;
/** @type {__VLS_StyleScopedClasses['material-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['has-detail']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "material-list" },
});
/** @type {__VLS_StyleScopedClasses['material-list']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "filter-bar" },
});
/** @type {__VLS_StyleScopedClasses['filter-bar']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.label, __VLS_intrinsics.label)({
    ...{ class: "search-box" },
});
/** @type {__VLS_StyleScopedClasses['search-box']} */ ;
const __VLS_0 = AppIcon;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({
    name: "search",
    size: (16),
}));
const __VLS_2 = __VLS_1({
    name: "search",
    size: (16),
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
__VLS_asFunctionalElement1(__VLS_intrinsics.input)({
    placeholder: "搜索文件名或关联任务",
});
(__VLS_ctx.search);
__VLS_asFunctionalElement1(__VLS_intrinsics.select, __VLS_intrinsics.select)({
    value: (__VLS_ctx.type),
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "",
});
for (const [item] of __VLS_vFor((__VLS_ctx.types))) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
        key: (item),
        value: (item),
    });
    (item.toUpperCase());
    // @ts-ignore
    [selected, search, type, types,];
}
__VLS_asFunctionalElement1(__VLS_intrinsics.select, __VLS_intrinsics.select)({
    value: (__VLS_ctx.status),
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "ready",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "pending",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "duplicate",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.select, __VLS_intrinsics.select)({
    value: (__VLS_ctx.usage),
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "unused",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "single",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "reused",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.select, __VLS_intrinsics.select)({
    value: (__VLS_ctx.dateRange),
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "all",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "7",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "30",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "90",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.select, __VLS_intrinsics.select)({
    value: (__VLS_ctx.sortBy),
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "recent",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "name",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "units",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "usage",
});
if (__VLS_ctx.hasFilters) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (__VLS_ctx.resetFilters) },
        ...{ class: "btn tertiary" },
    });
    /** @type {__VLS_StyleScopedClasses['btn']} */ ;
    /** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
}
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "result-meta" },
});
/** @type {__VLS_StyleScopedClasses['result-meta']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
(__VLS_ctx.filtered.length);
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
((__VLS_ctx.list.data.value || []).filter(item => __VLS_ctx.taskCount(item) > 1).length);
if (__VLS_ctx.list.isLoading.value) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "loading-line" },
    });
    /** @type {__VLS_StyleScopedClasses['loading-line']} */ ;
}
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "table-head" },
});
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
for (const [item] of __VLS_vFor((__VLS_ctx.filtered))) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                return (__VLS_ctx.selectMaterial(item.id));
                // @ts-ignore
                [status, usage, dateRange, sortBy, hasFilters, resetFilters, filtered, filtered, list, list, taskCount, selectMaterial,];
            } },
        key: (item.id),
        ...{ class: "file-row" },
        ...{ class: ({ selected: __VLS_ctx.selected === item.id }) },
    });
    /** @type {__VLS_StyleScopedClasses['file-row']} */ ;
    /** @type {__VLS_StyleScopedClasses['selected']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "file-name" },
    });
    /** @type {__VLS_StyleScopedClasses['file-name']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (item.filename);
    if (item.is_duplicate) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
        (item.duplicate_of);
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "type-label" },
    });
    /** @type {__VLS_StyleScopedClasses['type-label']} */ ;
    ((item.file_type || '—').toUpperCase());
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "parse-state" },
        ...{ class: (item.is_duplicate ? 'duplicate' : item.parse_status) },
    });
    /** @type {__VLS_StyleScopedClasses['parse-state']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.i, __VLS_intrinsics.i)({});
    (__VLS_ctx.statusLabel(item));
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (item.unit_count);
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (__VLS_ctx.taskCount(item));
    __VLS_asFunctionalElement1(__VLS_intrinsics.time, __VLS_intrinsics.time)({});
    (__VLS_ctx.formatDate(item.parsed_at));
    // @ts-ignore
    [selected, taskCount, statusLabel, formatDate,];
}
if (!__VLS_ctx.filtered.length && !__VLS_ctx.list.isLoading.value) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "empty" },
    });
    /** @type {__VLS_StyleScopedClasses['empty']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
    (__VLS_ctx.hasFilters ? '调整筛选条件后再试。' : '材料会在创建任务时进入材料库。');
}
if (__VLS_ctx.selected !== null) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
        ...{ class: "material-detail open" },
    });
    /** @type {__VLS_StyleScopedClasses['material-detail']} */ ;
    /** @type {__VLS_StyleScopedClasses['open']} */ ;
    if (__VLS_ctx.detail.data.value) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "detail-head" },
        });
        /** @type {__VLS_StyleScopedClasses['detail-head']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
        (__VLS_ctx.detail.data.value.filename);
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.selected !== null))
                        throw 0;
                    if (!(__VLS_ctx.detail.data.value))
                        throw 0;
                    return (__VLS_ctx.selected = null);
                    // @ts-ignore
                    [selected, selected, hasFilters, filtered, list, detail, detail,];
                } },
            ...{ class: "icon-button" },
            'aria-label': "关闭详情",
        });
        /** @type {__VLS_StyleScopedClasses['icon-button']} */ ;
        const __VLS_5 = AppIcon;
        // @ts-ignore
        const __VLS_6 = __VLS_asFunctionalComponent1(__VLS_5, new __VLS_5({
            name: "close",
            size: (17),
        }));
        const __VLS_7 = __VLS_6({
            name: "close",
            size: (17),
        }, ...__VLS_functionalComponentArgsRest(__VLS_6));
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "tabs" },
        });
        /** @type {__VLS_StyleScopedClasses['tabs']} */ ;
        for (const [tab] of __VLS_vFor(([['overview', '概览'], ['content', '解析内容'], ['meta', '元数据']]))) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.selected !== null))
                            throw 0;
                        if (!(__VLS_ctx.detail.data.value))
                            throw 0;
                        return (__VLS_ctx.detailTab = tab[0]);
                        // @ts-ignore
                        [detailTab,];
                    } },
                key: (tab[0]),
                ...{ class: "tab" },
                ...{ class: ({ active: __VLS_ctx.detailTab === tab[0] }) },
            });
            /** @type {__VLS_StyleScopedClasses['tab']} */ ;
            /** @type {__VLS_StyleScopedClasses['active']} */ ;
            (tab[1]);
            // @ts-ignore
            [detailTab,];
        }
        if (__VLS_ctx.detailTab === 'overview') {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "detail-body" },
            });
            /** @type {__VLS_StyleScopedClasses['detail-body']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.dl, __VLS_intrinsics.dl)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
            ((__VLS_ctx.detail.data.value.file_type || '—').toUpperCase());
            __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
            (__VLS_ctx.statusLabel(__VLS_ctx.detail.data.value));
            __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
            (__VLS_ctx.formatDate(__VLS_ctx.detail.data.value.parsed_at));
            __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
            (__VLS_ctx.detail.data.value.units?.length || 0);
            __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
            (__VLS_ctx.detail.data.value.tasks?.length || 0);
            __VLS_asFunctionalElement1(__VLS_intrinsics.h3, __VLS_intrinsics.h3)({});
            for (const [task] of __VLS_vFor((__VLS_ctx.detail.data.value.tasks || []))) {
                let __VLS_10;
                /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
                RouterLink;
                // @ts-ignore
                const __VLS_11 = __VLS_asFunctionalComponent1(__VLS_10, new __VLS_10({
                    key: (task.task_id || task),
                    to: (`/tasks/${task.task_id || task}`),
                    ...{ class: "task-use" },
                }));
                const __VLS_12 = __VLS_11({
                    key: (task.task_id || task),
                    to: (`/tasks/${task.task_id || task}`),
                    ...{ class: "task-use" },
                }, ...__VLS_functionalComponentArgsRest(__VLS_11));
                /** @type {__VLS_StyleScopedClasses['task-use']} */ ;
                const { default: __VLS_15 } = __VLS_13.slots;
                __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
                (task.theme || task.task_id || task);
                __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
                // @ts-ignore
                [statusLabel, formatDate, detail, detail, detail, detail, detail, detail, detailTab,];
                var __VLS_13;
                // @ts-ignore
                [];
            }
            if (!__VLS_ctx.detail.data.value.tasks?.length) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    ...{ class: "quiet-empty" },
                });
                /** @type {__VLS_StyleScopedClasses['quiet-empty']} */ ;
            }
        }
        else if (__VLS_ctx.detailTab === 'content') {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "unit-list" },
            });
            /** @type {__VLS_StyleScopedClasses['unit-list']} */ ;
            for (const [unit] of __VLS_vFor((__VLS_ctx.detail.data.value.units || []))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.details, __VLS_intrinsics.details)({
                    key: (unit.id),
                });
                __VLS_asFunctionalElement1(__VLS_intrinsics.summary, __VLS_intrinsics.summary)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
                (__VLS_ctx.unitKindLabel(unit.kind));
                __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
                (unit.page ? `第 ${unit.page} 页` : '文档内容');
                __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
                (unit.content || unit.image_desc || '无文本内容');
                // @ts-ignore
                [detail, detail, detailTab, unitKindLabel,];
            }
        }
        else {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "detail-body metadata" },
            });
            /** @type {__VLS_StyleScopedClasses['detail-body']} */ ;
            /** @type {__VLS_StyleScopedClasses['metadata']} */ ;
            if (__VLS_ctx.metadataRows.length) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.dl, __VLS_intrinsics.dl)({});
                for (const [row] of __VLS_vFor((__VLS_ctx.metadataRows))) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.template)({
                        key: (row.key),
                    });
                    __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
                    (row.label);
                    __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
                    (row.value);
                    // @ts-ignore
                    [metadataRows, metadataRows,];
                }
            }
            else {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    ...{ class: "quiet-empty" },
                });
                /** @type {__VLS_StyleScopedClasses['quiet-empty']} */ ;
            }
        }
    }
    else if (__VLS_ctx.detail.isLoading.value) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "detail-loading" },
        });
        /** @type {__VLS_StyleScopedClasses['detail-loading']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "loading-line" },
        });
        /** @type {__VLS_StyleScopedClasses['loading-line']} */ ;
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
// @ts-ignore
[detail,];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
