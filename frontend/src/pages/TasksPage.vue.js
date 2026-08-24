import { computed, ref } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { RouterLink } from "vue-router";
import { api } from "@/api/http";
import StatusBadge from "@/components/StatusBadge.vue";
const queryClient = useQueryClient();
const search = ref("");
const stage = ref("");
const tasks = useQuery({ queryKey: ["tasks"], queryFn: () => api("/api/tasks"), refetchInterval: 8000 });
const filtered = computed(() => (tasks.data.value || []).filter(t => (!search.value || t.theme.toLowerCase().includes(search.value.toLowerCase()) || t.task_id.includes(search.value)) && (!stage.value || t.stage === stage.value)));
const remove = useMutation({ mutationFn: (id) => api(`/api/tasks/${id}`, { method: "DELETE" }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tasks"] }) });
function deleteTask(task) { if (confirm(`删除任务“${task.theme}”？该操作不可恢复。`))
    remove.mutate(task.task_id); }
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['task-line']} */ ;
/** @type {__VLS_StyleScopedClasses['task-line']} */ ;
/** @type {__VLS_StyleScopedClasses['task-line']} */ ;
/** @type {__VLS_StyleScopedClasses['task-line']} */ ;
/** @type {__VLS_StyleScopedClasses['task-line']} */ ;
/** @type {__VLS_StyleScopedClasses['filters']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "page-stack" },
});
/** @type {__VLS_StyleScopedClasses['page-stack']} */ ;
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
    ...{ class: "btn primary" },
    to: "/?create=1",
}));
const __VLS_2 = __VLS_1({
    ...{ class: "btn primary" },
    to: "/?create=1",
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['primary']} */ ;
const { default: __VLS_5 } = __VLS_3.slots;
var __VLS_3;
__VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
    ...{ class: "surface" },
});
/** @type {__VLS_StyleScopedClasses['surface']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "filters" },
});
/** @type {__VLS_StyleScopedClasses['filters']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.input)({
    placeholder: "搜索任务名称或 ID",
});
(__VLS_ctx.search);
__VLS_asFunctionalElement1(__VLS_intrinsics.select, __VLS_intrinsics.select)({
    value: (__VLS_ctx.stage),
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "created",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "writing",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "review",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "done",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
    value: "failed",
});
if (__VLS_ctx.tasks.isLoading.value) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "loading-line" },
    });
    /** @type {__VLS_StyleScopedClasses['loading-line']} */ ;
}
if (__VLS_ctx.filtered.length) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "data-list" },
    });
    /** @type {__VLS_StyleScopedClasses['data-list']} */ ;
    for (const [task] of __VLS_vFor((__VLS_ctx.filtered))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            key: (task.task_id),
            ...{ class: "data-row task-line" },
        });
        /** @type {__VLS_StyleScopedClasses['data-row']} */ ;
        /** @type {__VLS_StyleScopedClasses['task-line']} */ ;
        let __VLS_6;
        /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
        RouterLink;
        // @ts-ignore
        const __VLS_7 = __VLS_asFunctionalComponent1(__VLS_6, new __VLS_6({
            to: (`/tasks/${task.task_id}`),
        }));
        const __VLS_8 = __VLS_7({
            to: (`/tasks/${task.task_id}`),
        }, ...__VLS_functionalComponentArgsRest(__VLS_7));
        const { default: __VLS_11 } = __VLS_9.slots;
        __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
        (task.theme);
        __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({
            ...{ class: "mono" },
        });
        /** @type {__VLS_StyleScopedClasses['mono']} */ ;
        (task.task_id);
        // @ts-ignore
        [search, stage, tasks, filtered, filtered,];
        var __VLS_9;
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        (task.material_count || 0);
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        (task.updated_at || task.created_at || '—');
        const __VLS_12 = StatusBadge;
        // @ts-ignore
        const __VLS_13 = __VLS_asFunctionalComponent1(__VLS_12, new __VLS_12({
            stage: (task.stage),
        }));
        const __VLS_14 = __VLS_13({
            stage: (task.stage),
        }, ...__VLS_functionalComponentArgsRest(__VLS_13));
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "button-row" },
        });
        /** @type {__VLS_StyleScopedClasses['button-row']} */ ;
        let __VLS_17;
        /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
        RouterLink;
        // @ts-ignore
        const __VLS_18 = __VLS_asFunctionalComponent1(__VLS_17, new __VLS_17({
            ...{ class: "btn tertiary" },
            to: (`/tasks/${task.task_id}`),
        }));
        const __VLS_19 = __VLS_18({
            ...{ class: "btn tertiary" },
            to: (`/tasks/${task.task_id}`),
        }, ...__VLS_functionalComponentArgsRest(__VLS_18));
        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
        /** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
        const { default: __VLS_22 } = __VLS_20.slots;
        // @ts-ignore
        [];
        var __VLS_20;
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.filtered.length))
                        throw 0;
                    return (__VLS_ctx.deleteTask(task));
                    // @ts-ignore
                    [deleteTask,];
                } },
            ...{ class: "btn tertiary danger" },
        });
        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
        /** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
        /** @type {__VLS_StyleScopedClasses['danger']} */ ;
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
// @ts-ignore
[tasks,];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
