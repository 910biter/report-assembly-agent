import { computed, ref } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { RouterLink } from "vue-router";
import { api } from "@/api/http";
import StatusBadge from "@/components/StatusBadge.vue";
import AppIcon from "@/components/AppIcon.vue";
const queryClient = useQueryClient();
const search = ref("");
const view = ref("all");
const dateRange = ref("all");
const variant = ref("");
const sortBy = ref("updated_desc");
const selectedId = ref(null);
const tasks = useQuery({ queryKey: ["tasks"], queryFn: () => api("/api/tasks"), refetchInterval: 8_000 });
const templates = useQuery({ queryKey: ["templates"], queryFn: () => api("/api/style/variants") });
const templateNames = computed(() => new Map((templates.data.value || []).map(item => [Number(item.id), item.name || item.label || `模板 ${item.id}`])));
function inView(task, target) {
    if (target === "all")
        return true;
    if (target === "pending")
        return task.stage === "created";
    if (target === "paused")
        return task.stage === "paused";
    if (target === "review")
        return task.stage === "review";
    if (target === "completed")
        return task.stage === "done";
    if (target === "failed")
        return task.stage === "failed";
    return !["created", "review", "done", "failed", "paused"].includes(task.stage);
}
function parseTimestamp(value) {
    if (!value)
        return 0;
    const numeric = Number(value);
    if (Number.isFinite(numeric) && String(value).trim() !== "")
        return numeric < 1e12 ? numeric * 1000 : numeric;
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : 0;
}
function withinDate(task) {
    if (dateRange.value === "all")
        return true;
    const value = parseTimestamp(task.updated_at || task.created_at);
    return value > 0 && value >= Date.now() - Number(dateRange.value) * 86_400_000;
}
function timestamp(task) {
    return parseTimestamp(task.updated_at || task.created_at);
}
const viewItems = computed(() => [
    { key: "all", label: "全部", count: (tasks.data.value || []).length },
    { key: "pending", label: "待运行", count: (tasks.data.value || []).filter(item => inView(item, "pending")).length },
    { key: "active", label: "进行中", count: (tasks.data.value || []).filter(item => inView(item, "active")).length },
    { key: "paused", label: "已暂停", count: (tasks.data.value || []).filter(item => inView(item, "paused")).length },
    { key: "review", label: "待审核", count: (tasks.data.value || []).filter(item => inView(item, "review")).length },
    { key: "completed", label: "已完成", count: (tasks.data.value || []).filter(item => inView(item, "completed")).length },
    { key: "failed", label: "异常", count: (tasks.data.value || []).filter(item => inView(item, "failed")).length },
]);
const filtered = computed(() => {
    const keyword = search.value.trim().toLowerCase();
    return (tasks.data.value || [])
        .filter(task => !keyword || `${task.theme} ${task.task_id} ${task.update_reason || ""}`.toLowerCase().includes(keyword))
        .filter(task => inView(task, view.value)).filter(withinDate)
        .filter(task => !variant.value || String(task.variant_id || "") === variant.value)
        .sort((a, b) => {
        if (sortBy.value === "created_desc")
            return parseTimestamp(b.created_at) - parseTimestamp(a.created_at);
        if (sortBy.value === "name")
            return a.theme.localeCompare(b.theme, "zh-CN");
        if (sortBy.value === "materials_desc")
            return (b.material_count || 0) - (a.material_count || 0);
        return timestamp(b) - timestamp(a);
    });
});
const selected = computed(() => (tasks.data.value || []).find(item => item.task_id === selectedId.value) || null);
const hasFilters = computed(() => Boolean(search.value || dateRange.value !== "all" || variant.value || sortBy.value !== "updated_desc"));
function resetFilters() { search.value = ""; dateRange.value = "all"; variant.value = ""; sortBy.value = "updated_desc"; }
function formatDate(value) { const timestamp = parseTimestamp(value); if (!timestamp)
    return "—"; return new Date(timestamp).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }
const remove = useMutation({ mutationFn: (id) => api(`/api/tasks/${id}`, { method: "DELETE" }), onSuccess: () => { selectedId.value = null; queryClient.invalidateQueries({ queryKey: ["tasks"] }); } });
function deleteTask(task) { if (confirm(`删除任务“${task.theme}”？该操作不可恢复。`))
    remove.mutate(task.task_id); }
function taskType(task) { return task.run_mode === "material_comparison" ? "新增材料对比" : task.incremental_update ? "增量更新" : "首次生成"; }
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['task-browser']} */ ;
/** @type {__VLS_StyleScopedClasses['view-tabs']} */ ;
/** @type {__VLS_StyleScopedClasses['view-tabs']} */ ;
/** @type {__VLS_StyleScopedClasses['view-tabs']} */ ;
/** @type {__VLS_StyleScopedClasses['view-tabs']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['search-box']} */ ;
/** @type {__VLS_StyleScopedClasses['search-box']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['task-row']} */ ;
/** @type {__VLS_StyleScopedClasses['task-row']} */ ;
/** @type {__VLS_StyleScopedClasses['task-row']} */ ;
/** @type {__VLS_StyleScopedClasses['task-name']} */ ;
/** @type {__VLS_StyleScopedClasses['task-name']} */ ;
/** @type {__VLS_StyleScopedClasses['task-row']} */ ;
/** @type {__VLS_StyleScopedClasses['task-row']} */ ;
/** @type {__VLS_StyleScopedClasses['icon-button']} */ ;
/** @type {__VLS_StyleScopedClasses['task-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['task-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['task-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['task-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['update-reason']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['task-browser']} */ ;
/** @type {__VLS_StyleScopedClasses['has-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['task-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['filter-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['search-box']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['task-row']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['task-row']} */ ;
/** @type {__VLS_StyleScopedClasses['task-row']} */ ;
/** @type {__VLS_StyleScopedClasses['filter-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['search-box']} */ ;
/** @type {__VLS_StyleScopedClasses['view-tabs']} */ ;
/** @type {__VLS_StyleScopedClasses['view-tabs']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['task-row']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['task-row']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "page-stack tasks-page" },
});
/** @type {__VLS_StyleScopedClasses['page-stack']} */ ;
/** @type {__VLS_StyleScopedClasses['tasks-page']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({
    ...{ class: "page-header" },
});
/** @type {__VLS_StyleScopedClasses['page-header']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.h1, __VLS_intrinsics.h1)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
let __VLS_0;
/** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
RouterLink;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({
    ...{ class: "btn primary page-action" },
    to: "/?create=1",
}));
const __VLS_2 = __VLS_1({
    ...{ class: "btn primary page-action" },
    to: "/?create=1",
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['primary']} */ ;
/** @type {__VLS_StyleScopedClasses['page-action']} */ ;
const { default: __VLS_5 } = __VLS_3.slots;
const __VLS_6 = AppIcon;
// @ts-ignore
const __VLS_7 = __VLS_asFunctionalComponent1(__VLS_6, new __VLS_6({
    name: "plus",
    size: (16),
}));
const __VLS_8 = __VLS_7({
    name: "plus",
    size: (16),
}, ...__VLS_functionalComponentArgsRest(__VLS_7));
var __VLS_3;
__VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
    ...{ class: "surface task-browser" },
    ...{ class: ({ 'has-detail': __VLS_ctx.selected }) },
});
/** @type {__VLS_StyleScopedClasses['surface']} */ ;
/** @type {__VLS_StyleScopedClasses['task-browser']} */ ;
/** @type {__VLS_StyleScopedClasses['has-detail']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "browser-main" },
});
/** @type {__VLS_StyleScopedClasses['browser-main']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.nav, __VLS_intrinsics.nav)({
    ...{ class: "view-tabs" },
    'aria-label': "任务视图",
});
/** @type {__VLS_StyleScopedClasses['view-tabs']} */ ;
for (const [item] of __VLS_vFor((__VLS_ctx.viewItems))) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                return (__VLS_ctx.view = item.key);
                // @ts-ignore
                [selected, viewItems, view,];
            } },
        key: (item.key),
        ...{ class: ({ active: __VLS_ctx.view === item.key }) },
    });
    /** @type {__VLS_StyleScopedClasses['active']} */ ;
    (item.label);
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (item.count);
    // @ts-ignore
    [view,];
}
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "filter-bar" },
});
/** @type {__VLS_StyleScopedClasses['filter-bar']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.label, __VLS_intrinsics.label)({
    ...{ class: "search-box" },
});
/** @type {__VLS_StyleScopedClasses['search-box']} */ ;
const __VLS_11 = AppIcon;
// @ts-ignore
const __VLS_12 = __VLS_asFunctionalComponent1(__VLS_11, new __VLS_11({
    name: "search",
    size: (16),
}));
const __VLS_13 = __VLS_12({
    name: "search",
    size: (16),
}, ...__VLS_functionalComponentArgsRest(__VLS_12));
__VLS_asFunctionalElement1(__VLS_intrinsics.input)({
    placeholder: "搜索任务名称、ID 或更新说明",
});
(__VLS_ctx.search);
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
    value: (__VLS_ctx.variant),
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "",
});
for (const [item] of __VLS_vFor((__VLS_ctx.templates.data.value || []))) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
        key: (item.id),
        value: (String(item.id)),
    });
    (item.name || item.label || `模板 ${item.id}`);
    // @ts-ignore
    [search, dateRange, variant, templates,];
}
__VLS_asFunctionalElement1(__VLS_intrinsics.select, __VLS_intrinsics.select)({
    value: (__VLS_ctx.sortBy),
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "updated_desc",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "created_desc",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "materials_desc",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "name",
});
if (__VLS_ctx.hasFilters) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (__VLS_ctx.resetFilters) },
        ...{ class: "btn tertiary reset" },
    });
    /** @type {__VLS_StyleScopedClasses['btn']} */ ;
    /** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
    /** @type {__VLS_StyleScopedClasses['reset']} */ ;
}
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "result-meta" },
});
/** @type {__VLS_StyleScopedClasses['result-meta']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
(__VLS_ctx.filtered.length);
if (__VLS_ctx.tasks.isFetching.value && !__VLS_ctx.tasks.isLoading.value) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
}
if (__VLS_ctx.tasks.isLoading.value) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "loading-line" },
    });
    /** @type {__VLS_StyleScopedClasses['loading-line']} */ ;
}
if (__VLS_ctx.filtered.length) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "task-table" },
    });
    /** @type {__VLS_StyleScopedClasses['task-table']} */ ;
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
    for (const [task] of __VLS_vFor((__VLS_ctx.filtered))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.filtered.length))
                        throw 0;
                    return (__VLS_ctx.selectedId = task.task_id);
                    // @ts-ignore
                    [sortBy, hasFilters, resetFilters, filtered, filtered, filtered, tasks, tasks, tasks, selectedId,];
                } },
            key: (task.task_id),
            ...{ class: "task-row" },
            ...{ class: ({ selected: __VLS_ctx.selectedId === task.task_id }) },
        });
        /** @type {__VLS_StyleScopedClasses['task-row']} */ ;
        /** @type {__VLS_StyleScopedClasses['selected']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "task-name" },
        });
        /** @type {__VLS_StyleScopedClasses['task-name']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
        (task.theme);
        __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({
            ...{ class: "mono" },
        });
        /** @type {__VLS_StyleScopedClasses['mono']} */ ;
        (task.task_id);
        const __VLS_16 = StatusBadge;
        // @ts-ignore
        const __VLS_17 = __VLS_asFunctionalComponent1(__VLS_16, new __VLS_16({
            stage: (task.stage),
        }));
        const __VLS_18 = __VLS_17({
            stage: (task.stage),
        }, ...__VLS_functionalComponentArgsRest(__VLS_17));
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        (task.material_count || 0);
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
            ...{ class: "truncate" },
        });
        /** @type {__VLS_StyleScopedClasses['truncate']} */ ;
        (task.variant_id ? __VLS_ctx.templateNames.get(Number(task.variant_id)) || `模板 ${task.variant_id}` : '默认模板');
        __VLS_asFunctionalElement1(__VLS_intrinsics.time, __VLS_intrinsics.time)({});
        (__VLS_ctx.formatDate(task.updated_at || task.created_at));
        let __VLS_21;
        /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
        RouterLink;
        // @ts-ignore
        const __VLS_22 = __VLS_asFunctionalComponent1(__VLS_21, new __VLS_21({
            ...{ 'onClick': {} },
            ...{ class: "open-link" },
            to: (`/tasks/${task.task_id}`),
        }));
        const __VLS_23 = __VLS_22({
            ...{ 'onClick': {} },
            ...{ class: "open-link" },
            to: (`/tasks/${task.task_id}`),
        }, ...__VLS_functionalComponentArgsRest(__VLS_22));
        let __VLS_26;
        const __VLS_27 = {
            /** @type {typeof __VLS_26.click} */
            onClick: () => { },
        };
        /** @type {__VLS_StyleScopedClasses['open-link']} */ ;
        const { default: __VLS_28 } = __VLS_24.slots;
        // @ts-ignore
        [selectedId, templateNames, formatDate,];
        var __VLS_24;
        var __VLS_25;
        // @ts-ignore
        [];
    }
}
else if (!__VLS_ctx.tasks.isLoading.value) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "empty" },
    });
    /** @type {__VLS_StyleScopedClasses['empty']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
}
if (__VLS_ctx.selected) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
        ...{ class: "task-detail" },
    });
    /** @type {__VLS_StyleScopedClasses['task-detail']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "detail-head" },
    });
    /** @type {__VLS_StyleScopedClasses['detail-head']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.selected))
                    throw 0;
                return (__VLS_ctx.selectedId = null);
                // @ts-ignore
                [selected, tasks, selectedId,];
            } },
        ...{ class: "icon-button" },
        'aria-label': "关闭详情",
    });
    /** @type {__VLS_StyleScopedClasses['icon-button']} */ ;
    const __VLS_29 = AppIcon;
    // @ts-ignore
    const __VLS_30 = __VLS_asFunctionalComponent1(__VLS_29, new __VLS_29({
        name: "close",
        size: (17),
    }));
    const __VLS_31 = __VLS_30({
        name: "close",
        size: (17),
    }, ...__VLS_functionalComponentArgsRest(__VLS_30));
    __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
    (__VLS_ctx.selected.theme);
    const __VLS_34 = StatusBadge;
    // @ts-ignore
    const __VLS_35 = __VLS_asFunctionalComponent1(__VLS_34, new __VLS_34({
        stage: (__VLS_ctx.selected.stage),
    }));
    const __VLS_36 = __VLS_35({
        stage: (__VLS_ctx.selected.stage),
    }, ...__VLS_functionalComponentArgsRest(__VLS_35));
    __VLS_asFunctionalElement1(__VLS_intrinsics.dl, __VLS_intrinsics.dl)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({
        ...{ class: "mono" },
    });
    /** @type {__VLS_StyleScopedClasses['mono']} */ ;
    (__VLS_ctx.selected.task_id);
    __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
    (__VLS_ctx.selected.material_count || 0);
    __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
    (__VLS_ctx.selected.run_revision || 1);
    __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
    (__VLS_ctx.taskType(__VLS_ctx.selected));
    __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
    (__VLS_ctx.selected.variant_id ? __VLS_ctx.templateNames.get(Number(__VLS_ctx.selected.variant_id)) || `模板 ${__VLS_ctx.selected.variant_id}` : '默认模板');
    __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
    (__VLS_ctx.formatDate(__VLS_ctx.selected.created_at));
    __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
    (__VLS_ctx.formatDate(__VLS_ctx.selected.updated_at || __VLS_ctx.selected.created_at));
    if (__VLS_ctx.selected.update_reason) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
            ...{ class: "update-reason" },
        });
        /** @type {__VLS_StyleScopedClasses['update-reason']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (__VLS_ctx.selected.update_reason);
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "detail-actions" },
    });
    /** @type {__VLS_StyleScopedClasses['detail-actions']} */ ;
    let __VLS_39;
    /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
    RouterLink;
    // @ts-ignore
    const __VLS_40 = __VLS_asFunctionalComponent1(__VLS_39, new __VLS_39({
        ...{ class: "btn primary" },
        to: (`/tasks/${__VLS_ctx.selected.task_id}`),
    }));
    const __VLS_41 = __VLS_40({
        ...{ class: "btn primary" },
        to: (`/tasks/${__VLS_ctx.selected.task_id}`),
    }, ...__VLS_functionalComponentArgsRest(__VLS_40));
    /** @type {__VLS_StyleScopedClasses['btn']} */ ;
    /** @type {__VLS_StyleScopedClasses['primary']} */ ;
    const { default: __VLS_44 } = __VLS_42.slots;
    // @ts-ignore
    [selected, selected, selected, selected, selected, selected, selected, selected, selected, selected, selected, selected, selected, selected, selected, templateNames, formatDate, formatDate, taskType,];
    var __VLS_42;
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.selected))
                    throw 0;
                return (__VLS_ctx.deleteTask(__VLS_ctx.selected));
                // @ts-ignore
                [selected, deleteTask,];
            } },
        ...{ class: "btn danger" },
    });
    /** @type {__VLS_StyleScopedClasses['btn']} */ ;
    /** @type {__VLS_StyleScopedClasses['danger']} */ ;
}
// @ts-ignore
[];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
