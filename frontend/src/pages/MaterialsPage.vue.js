import { computed, ref } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { api } from "@/api/http";
const search = ref("");
const type = ref("");
const selected = ref(null);
const detailTab = ref("overview");
const list = useQuery({ queryKey: ["materials"], queryFn: () => api("/api/materials") });
const detail = useQuery({ queryKey: computed(() => ["material", selected.value]), queryFn: () => api(`/api/materials/${selected.value}`), enabled: computed(() => selected.value !== null) });
const types = computed(() => [...new Set((list.data.value || []).map(x => x.file_type).filter(Boolean))]);
const filtered = computed(() => (list.data.value || []).filter(x => (!search.value || x.filename.toLowerCase().includes(search.value.toLowerCase())) && (!type.value || x.file_type === type.value)));
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-head']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-head']} */ ;
/** @type {__VLS_StyleScopedClasses['material-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-body']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-body']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-body']} */ ;
/** @type {__VLS_StyleScopedClasses['unit-list']} */ ;
/** @type {__VLS_StyleScopedClasses['unit-list']} */ ;
/** @type {__VLS_StyleScopedClasses['unit-list']} */ ;
/** @type {__VLS_StyleScopedClasses['unit-list']} */ ;
/** @type {__VLS_StyleScopedClasses['unit-list']} */ ;
/** @type {__VLS_StyleScopedClasses['unit-list']} */ ;
/** @type {__VLS_StyleScopedClasses['metadata']} */ ;
/** @type {__VLS_StyleScopedClasses['material-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['material-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['material-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['filters']} */ ;
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
});
/** @type {__VLS_StyleScopedClasses['surface']} */ ;
/** @type {__VLS_StyleScopedClasses['material-shell']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "material-list" },
});
/** @type {__VLS_StyleScopedClasses['material-list']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "filters" },
});
/** @type {__VLS_StyleScopedClasses['filters']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.input)({
    placeholder: "搜索文件名",
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
    });
    (item);
    // @ts-ignore
    [search, type, types,];
}
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "table-head" },
});
/** @type {__VLS_StyleScopedClasses['table-head']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
for (const [item] of __VLS_vFor((__VLS_ctx.filtered))) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                return (__VLS_ctx.selected = item.id);
                // @ts-ignore
                [filtered, selected,];
            } },
        key: (item.id),
        ...{ class: "file-row" },
        ...{ class: ({ selected: __VLS_ctx.selected === item.id }) },
    });
    /** @type {__VLS_StyleScopedClasses['file-row']} */ ;
    /** @type {__VLS_StyleScopedClasses['selected']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (item.filename);
    if (item.is_duplicate) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
        (item.duplicate_of);
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (item.file_type || '—');
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (item.unit_count);
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (item.tasks?.length || 0);
    // @ts-ignore
    [selected,];
}
if (!__VLS_ctx.filtered.length) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "empty" },
    });
    /** @type {__VLS_StyleScopedClasses['empty']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
}
__VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
    ...{ class: "material-detail" },
    ...{ class: ({ open: __VLS_ctx.selected !== null }) },
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
                if (!(__VLS_ctx.detail.data.value))
                    throw 0;
                return (__VLS_ctx.selected = null);
                // @ts-ignore
                [filtered, selected, selected, detail, detail,];
            } },
        ...{ class: "btn tertiary" },
    });
    /** @type {__VLS_StyleScopedClasses['btn']} */ ;
    /** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "tabs" },
    });
    /** @type {__VLS_StyleScopedClasses['tabs']} */ ;
    for (const [tab] of __VLS_vFor(([['overview', '概览'], ['content', '解析内容'], ['meta', '元数据']]))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
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
        (__VLS_ctx.detail.data.value.file_type);
        __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
        (__VLS_ctx.detail.data.value.units?.length || 0);
        __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
        (__VLS_ctx.detail.data.value.tasks?.length || 0);
        __VLS_asFunctionalElement1(__VLS_intrinsics.h3, __VLS_intrinsics.h3)({});
        for (const [task] of __VLS_vFor((__VLS_ctx.detail.data.value.tasks || []))) {
            let __VLS_0;
            /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
            RouterLink;
            // @ts-ignore
            const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({
                key: (task.task_id || task),
                to: (`/tasks/${task.task_id || task}`),
                ...{ class: "task-use" },
            }));
            const __VLS_2 = __VLS_1({
                key: (task.task_id || task),
                to: (`/tasks/${task.task_id || task}`),
                ...{ class: "task-use" },
            }, ...__VLS_functionalComponentArgsRest(__VLS_1));
            /** @type {__VLS_StyleScopedClasses['task-use']} */ ;
            const { default: __VLS_5 } = __VLS_3.slots;
            (task.theme || task.task_id || task);
            // @ts-ignore
            [detail, detail, detail, detail, detailTab,];
            var __VLS_3;
            // @ts-ignore
            [];
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
            (unit.kind);
            __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
            (unit.page ? `第 ${unit.page} 页` : `单元 ${unit.id}`);
            __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
            (unit.content || unit.image_desc || '无文本内容');
            // @ts-ignore
            [detail, detailTab,];
        }
    }
    else {
        __VLS_asFunctionalElement1(__VLS_intrinsics.pre, __VLS_intrinsics.pre)({
            ...{ class: "metadata" },
        });
        /** @type {__VLS_StyleScopedClasses['metadata']} */ ;
        (JSON.stringify(__VLS_ctx.detail.data.value.units?.[0]?.metadata || {}, null, 2));
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
// @ts-ignore
[detail,];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
