import { computed, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useRoute, useRouter, RouterLink } from "vue-router";
import { api } from "@/api/http";
import StatusBadge from "@/components/StatusBadge.vue";
import AppIcon from "@/components/AppIcon.vue";
const route = useRoute();
const router = useRouter();
const queryClient = useQueryClient();
const createOpen = ref(false);
const form = ref();
const error = ref("");
const tasks = useQuery({ queryKey: ["tasks"], queryFn: () => api("/api/tasks"), refetchInterval: 8_000 });
const materials = useQuery({ queryKey: ["materials"], queryFn: () => api("/api/materials") });
const templates = useQuery({ queryKey: ["templates"], queryFn: () => api("/api/style/variants") });
const recent = computed(() => (tasks.data.value || []).slice(0, 6));
const waiting = computed(() => (tasks.data.value || []).filter(t => t.stage === "review"));
const finished = computed(() => (tasks.data.value || []).filter(t => t.stage === "done" || t.stage === "review"));
watch(() => route.query.create, value => { if (value)
    createOpen.value = true; }, { immediate: true });
watch([() => tasks.isSuccess.value, () => tasks.data.value?.length], ([ready, count]) => { if (ready && count === 0)
    createOpen.value = true; }, { immediate: true });
function closeCreate() {
    createOpen.value = false;
    const query = { ...route.query };
    delete query.create;
    router.replace({ query });
}
const createTask = useMutation({
    mutationFn: async () => {
        if (!form.value)
            throw new Error("表单尚未就绪");
        const data = new FormData(form.value);
        return api("/api/tasks", { method: "POST", body: data });
    },
    onSuccess: async (result) => { await queryClient.invalidateQueries({ queryKey: ["tasks"] }); router.push(`/tasks/${result.task_id}`); },
    onError: e => { error.value = e instanceof Error ? e.message : "创建失败"; },
});
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['intro-heading']} */ ;
/** @type {__VLS_StyleScopedClasses['intro-heading']} */ ;
/** @type {__VLS_StyleScopedClasses['create-intro']} */ ;
/** @type {__VLS_StyleScopedClasses['create-intro']} */ ;
/** @type {__VLS_StyleScopedClasses['submit-row']} */ ;
/** @type {__VLS_StyleScopedClasses['task-row']} */ ;
/** @type {__VLS_StyleScopedClasses['task-row']} */ ;
/** @type {__VLS_StyleScopedClasses['task-row']} */ ;
/** @type {__VLS_StyleScopedClasses['compact-link']} */ ;
/** @type {__VLS_StyleScopedClasses['compact-link']} */ ;
/** @type {__VLS_StyleScopedClasses['create-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['dashboard-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['create-form']} */ ;
/** @type {__VLS_StyleScopedClasses['field-wide']} */ ;
/** @type {__VLS_StyleScopedClasses['submit-row']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "page-stack dashboard" },
});
/** @type {__VLS_StyleScopedClasses['page-stack']} */ ;
/** @type {__VLS_StyleScopedClasses['dashboard']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({
    ...{ class: "page-header" },
});
/** @type {__VLS_StyleScopedClasses['page-header']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.h1, __VLS_intrinsics.h1)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
    ...{ onClick: (...[$event]) => {
            return (__VLS_ctx.createOpen ? __VLS_ctx.closeCreate() : __VLS_ctx.createOpen = true);
            // @ts-ignore
            [createOpen, createOpen, closeCreate,];
        } },
    ...{ class: "btn primary header-create" },
    type: "button",
});
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['primary']} */ ;
/** @type {__VLS_StyleScopedClasses['header-create']} */ ;
const __VLS_0 = AppIcon;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({
    name: (__VLS_ctx.createOpen ? 'close' : 'plus'),
    size: (16),
}));
const __VLS_2 = __VLS_1({
    name: (__VLS_ctx.createOpen ? 'close' : 'plus'),
    size: (16),
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
(__VLS_ctx.createOpen ? '收起创建' : '新建任务');
if (__VLS_ctx.createOpen) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
        ...{ class: "surface create-panel" },
    });
    /** @type {__VLS_StyleScopedClasses['surface']} */ ;
    /** @type {__VLS_StyleScopedClasses['create-panel']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "create-intro" },
    });
    /** @type {__VLS_StyleScopedClasses['create-intro']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "intro-heading" },
    });
    /** @type {__VLS_StyleScopedClasses['intro-heading']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.form, __VLS_intrinsics.form)({
        ...{ onSubmit: (...[$event]) => {
                if (!(__VLS_ctx.createOpen))
                    throw 0;
                return (__VLS_ctx.createTask.mutate());
                // @ts-ignore
                [createOpen, createOpen, createOpen, createTask,];
            } },
        ref: "form",
        ...{ class: "create-form" },
    });
    /** @type {__VLS_StyleScopedClasses['create-form']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.label, __VLS_intrinsics.label)({
        ...{ class: "field field-wide" },
    });
    /** @type {__VLS_StyleScopedClasses['field']} */ ;
    /** @type {__VLS_StyleScopedClasses['field-wide']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.input)({
        name: "theme",
        required: true,
        placeholder: "例如：可信执行环境远程证明机制研究综述",
    });
    __VLS_asFunctionalElement1(__VLS_intrinsics.label, __VLS_intrinsics.label)({
        ...{ class: "field field-wide" },
    });
    /** @type {__VLS_StyleScopedClasses['field']} */ ;
    /** @type {__VLS_StyleScopedClasses['field-wide']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.textarea, __VLS_intrinsics.textarea)({
        name: "requirements",
        rows: "4",
        placeholder: "描述用途、重点、篇幅或必须回答的问题。无需配置系统参数。",
    });
    __VLS_asFunctionalElement1(__VLS_intrinsics.label, __VLS_intrinsics.label)({
        ...{ class: "field" },
    });
    /** @type {__VLS_StyleScopedClasses['field']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.input)({
        name: "files",
        type: "file",
        multiple: true,
    });
    __VLS_asFunctionalElement1(__VLS_intrinsics.label, __VLS_intrinsics.label)({
        ...{ class: "field" },
    });
    /** @type {__VLS_StyleScopedClasses['field']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.select, __VLS_intrinsics.select)({
        name: "existing_material_ids",
        multiple: true,
        size: "5",
    });
    for (const [item] of __VLS_vFor((__VLS_ctx.materials.data.value || []))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
            key: (item.id),
            value: (item.id),
        });
        (item.filename);
        // @ts-ignore
        [materials,];
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.label, __VLS_intrinsics.label)({
        ...{ class: "field" },
    });
    /** @type {__VLS_StyleScopedClasses['field']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.select, __VLS_intrinsics.select)({
        name: "variant_id",
    });
    __VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
        value: "",
    });
    for (const [item] of __VLS_vFor((__VLS_ctx.templates.data.value || []))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
            key: (item.id),
            value: (item.id),
        });
        (item.name || item.label || `模板 ${item.id}`);
        // @ts-ignore
        [templates,];
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "submit-row" },
    });
    /** @type {__VLS_StyleScopedClasses['submit-row']} */ ;
    if (__VLS_ctx.error) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
            ...{ class: "error-text" },
        });
        /** @type {__VLS_StyleScopedClasses['error-text']} */ ;
        (__VLS_ctx.error);
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ class: "btn primary" },
        disabled: (__VLS_ctx.createTask.isPending.value),
    });
    /** @type {__VLS_StyleScopedClasses['btn']} */ ;
    /** @type {__VLS_StyleScopedClasses['primary']} */ ;
    (__VLS_ctx.createTask.isPending.value ? '正在创建…' : '创建并进入任务');
}
__VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
    ...{ class: "metric-strip" },
});
/** @type {__VLS_StyleScopedClasses['metric-strip']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "metric" },
});
/** @type {__VLS_StyleScopedClasses['metric']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
(__VLS_ctx.tasks.data.value?.length || 0);
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "metric" },
});
/** @type {__VLS_StyleScopedClasses['metric']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
((__VLS_ctx.tasks.data.value || []).filter(t => !['done', 'review', 'failed', 'paused', 'created'].includes(t.stage)).length);
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "metric" },
});
/** @type {__VLS_StyleScopedClasses['metric']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
(__VLS_ctx.waiting.length);
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "metric" },
});
/** @type {__VLS_StyleScopedClasses['metric']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
(__VLS_ctx.materials.data.value?.length || 0);
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "dashboard-grid" },
});
/** @type {__VLS_StyleScopedClasses['dashboard-grid']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
    ...{ class: "surface section-block recent" },
});
/** @type {__VLS_StyleScopedClasses['surface']} */ ;
/** @type {__VLS_StyleScopedClasses['section-block']} */ ;
/** @type {__VLS_StyleScopedClasses['recent']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "section-head" },
});
/** @type {__VLS_StyleScopedClasses['section-head']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
    ...{ class: "muted" },
});
/** @type {__VLS_StyleScopedClasses['muted']} */ ;
let __VLS_5;
/** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
RouterLink;
// @ts-ignore
const __VLS_6 = __VLS_asFunctionalComponent1(__VLS_5, new __VLS_5({
    ...{ class: "btn tertiary" },
    to: "/tasks",
}));
const __VLS_7 = __VLS_6({
    ...{ class: "btn tertiary" },
    to: "/tasks",
}, ...__VLS_functionalComponentArgsRest(__VLS_6));
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
const { default: __VLS_10 } = __VLS_8.slots;
// @ts-ignore
[createTask, createTask, materials, error, error, tasks, tasks, waiting,];
var __VLS_8;
if (__VLS_ctx.recent.length) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "data-list" },
    });
    /** @type {__VLS_StyleScopedClasses['data-list']} */ ;
    for (const [task] of __VLS_vFor((__VLS_ctx.recent))) {
        let __VLS_11;
        /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
        RouterLink;
        // @ts-ignore
        const __VLS_12 = __VLS_asFunctionalComponent1(__VLS_11, new __VLS_11({
            key: (task.task_id),
            to: (`/tasks/${task.task_id}`),
            ...{ class: "data-row task-row" },
        }));
        const __VLS_13 = __VLS_12({
            key: (task.task_id),
            to: (`/tasks/${task.task_id}`),
            ...{ class: "data-row task-row" },
        }, ...__VLS_functionalComponentArgsRest(__VLS_12));
        /** @type {__VLS_StyleScopedClasses['data-row']} */ ;
        /** @type {__VLS_StyleScopedClasses['task-row']} */ ;
        const { default: __VLS_16 } = __VLS_14.slots;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
        (task.theme);
        __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
        (task.created_at || task.task_id);
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        (task.material_count || 0);
        const __VLS_17 = StatusBadge;
        // @ts-ignore
        const __VLS_18 = __VLS_asFunctionalComponent1(__VLS_17, new __VLS_17({
            stage: (task.stage),
        }));
        const __VLS_19 = __VLS_18({
            stage: (task.stage),
        }, ...__VLS_functionalComponentArgsRest(__VLS_18));
        // @ts-ignore
        [recent, recent,];
        var __VLS_14;
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
__VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
    ...{ class: "side-stack" },
});
/** @type {__VLS_StyleScopedClasses['side-stack']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
    ...{ class: "surface section-block" },
});
/** @type {__VLS_StyleScopedClasses['surface']} */ ;
/** @type {__VLS_StyleScopedClasses['section-block']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "section-head" },
});
/** @type {__VLS_StyleScopedClasses['section-head']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
    ...{ class: "muted" },
});
/** @type {__VLS_StyleScopedClasses['muted']} */ ;
for (const [task] of __VLS_vFor((__VLS_ctx.waiting.slice(0, 4)))) {
    let __VLS_22;
    /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
    RouterLink;
    // @ts-ignore
    const __VLS_23 = __VLS_asFunctionalComponent1(__VLS_22, new __VLS_22({
        key: (task.task_id),
        to: (task.report_id ? `/reports/${task.report_id}` : `/tasks/${task.task_id}`),
        ...{ class: "compact-link" },
    }));
    const __VLS_24 = __VLS_23({
        key: (task.task_id),
        to: (task.report_id ? `/reports/${task.report_id}` : `/tasks/${task.task_id}`),
        ...{ class: "compact-link" },
    }, ...__VLS_functionalComponentArgsRest(__VLS_23));
    /** @type {__VLS_StyleScopedClasses['compact-link']} */ ;
    const { default: __VLS_27 } = __VLS_25.slots;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (task.theme);
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    // @ts-ignore
    [waiting,];
    var __VLS_25;
    // @ts-ignore
    [];
}
if (!__VLS_ctx.waiting.length) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "quiet-empty" },
    });
    /** @type {__VLS_StyleScopedClasses['quiet-empty']} */ ;
}
if (__VLS_ctx.finished.length) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
        ...{ class: "surface section-block" },
    });
    /** @type {__VLS_StyleScopedClasses['surface']} */ ;
    /** @type {__VLS_StyleScopedClasses['section-block']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
    for (const [task] of __VLS_vFor((__VLS_ctx.finished.slice(0, 3)))) {
        let __VLS_28;
        /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
        RouterLink;
        // @ts-ignore
        const __VLS_29 = __VLS_asFunctionalComponent1(__VLS_28, new __VLS_28({
            key: (task.task_id),
            to: (`/tasks/${task.task_id}`),
            ...{ class: "compact-link" },
        }));
        const __VLS_30 = __VLS_29({
            key: (task.task_id),
            to: (`/tasks/${task.task_id}`),
            ...{ class: "compact-link" },
        }, ...__VLS_functionalComponentArgsRest(__VLS_29));
        /** @type {__VLS_StyleScopedClasses['compact-link']} */ ;
        const { default: __VLS_33 } = __VLS_31.slots;
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        (task.theme);
        const __VLS_34 = StatusBadge;
        // @ts-ignore
        const __VLS_35 = __VLS_asFunctionalComponent1(__VLS_34, new __VLS_34({
            stage: (task.stage),
        }));
        const __VLS_36 = __VLS_35({
            stage: (task.stage),
        }, ...__VLS_functionalComponentArgsRest(__VLS_35));
        // @ts-ignore
        [waiting, finished, finished,];
        var __VLS_31;
        // @ts-ignore
        [];
    }
}
// @ts-ignore
[];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
