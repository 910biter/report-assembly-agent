import { computed, ref, watch } from "vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { api } from "@/api/http";
import ReviewCopilot from "@/components/ReviewCopilot.vue";
const props = defineProps();
const qc = useQueryClient();
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
const notifications = useQuery({
    queryKey: ["interaction-notifications", props.taskId],
    queryFn: () => api(`/api/interaction-notifications?task_id=${props.taskId}`),
    refetchInterval: 5000,
});
const items = computed(() => workspace.data.value?.items || []);
watch(items, (value) => {
    if (!value.length)
        selected.value = null;
    else if (!selected.value ||
        !value.some((item) => item.object_id === selected.value.object_id))
        selected.value = value[0];
}, { immediate: true });
watch([artifactType, search], () => {
    offset.value = 0;
    selected.value = null;
});
async function readNotice(item) {
    if (item.status === "unread") {
        await api(`/api/interaction-notifications/${item.id}/read`, {
            method: "PATCH",
        });
        await qc.invalidateQueries({
            queryKey: ["interaction-notifications", props.taskId],
        });
    }
    if (item.action_url)
        location.href = item.action_url;
}
function chooseType(value) {
    artifactType.value = value;
}
function pretty(value) {
    if (value == null || value === "")
        return "—";
    if (Array.isArray(value))
        return (value
            .map((item) => typeof item === "object"
            ? item.title || item.content || JSON.stringify(item)
            : item)
            .join("、") || "—");
    if (typeof value === "object")
        return JSON.stringify(value, null, 2);
    return String(value);
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
/** @type {__VLS_StyleScopedClasses['notice-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['notice-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['notice-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['notice-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['notice-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['notice-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['notice-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['notice-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-groups']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-groups']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-groups']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-groups']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-groups']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-browser']} */ ;
/** @type {__VLS_StyleScopedClasses['browser-tools']} */ ;
/** @type {__VLS_StyleScopedClasses['browser-tools']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-list']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-list']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-list']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-list']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-list']} */ ;
/** @type {__VLS_StyleScopedClasses['inline-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['inline-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['inline-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['inline-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['review-target']} */ ;
/** @type {__VLS_StyleScopedClasses['review-target']} */ ;
/** @type {__VLS_StyleScopedClasses['review-target']} */ ;
/** @type {__VLS_StyleScopedClasses['review-target']} */ ;
/** @type {__VLS_StyleScopedClasses['pager']} */ ;
/** @type {__VLS_StyleScopedClasses['collaboration-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-review']} */ ;
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
if ((__VLS_ctx.notifications.data.value || []).length) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "notice-strip" },
    });
    /** @type {__VLS_StyleScopedClasses['notice-strip']} */ ;
    for (const [item] of __VLS_vFor(((__VLS_ctx.notifications.data.value || []).slice(0, 3)))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!((__VLS_ctx.notifications.data.value || []).length))
                        throw 0;
                    return (__VLS_ctx.readNotice(item));
                    // @ts-ignore
                    [notifications, notifications, readNotice,];
                } },
            key: (item.id),
            ...{ class: ({ unread: item.status === 'unread' }) },
        });
        /** @type {__VLS_StyleScopedClasses['unread']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (item.title);
        __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
        (item.message);
        __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
        // @ts-ignore
        [];
    }
}
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
                    return (__VLS_ctx.selected = item);
                    // @ts-ignore
                    [workspace, search, items, items, selected, selected,];
                } },
            type: "button",
        });
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (item.title);
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        (item.summary || "打开查看详情");
        if (__VLS_ctx.selected?.object_id === item.object_id) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.dl, __VLS_intrinsics.dl)({
                ...{ class: "inline-detail" },
            });
            /** @type {__VLS_StyleScopedClasses['inline-detail']} */ ;
            for (const [value, key] of __VLS_vFor((item.current))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.template)({
                    key: (key),
                });
                __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
                (key);
                __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
                (__VLS_ctx.pretty(value));
                // @ts-ignore
                [selected, pretty,];
            }
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
__VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
    ...{ class: "artifact-review" },
});
/** @type {__VLS_StyleScopedClasses['artifact-review']} */ ;
if (__VLS_ctx.selected) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "review-target" },
    });
    /** @type {__VLS_StyleScopedClasses['review-target']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (__VLS_ctx.selected.title);
    const __VLS_0 = ReviewCopilot;
    // @ts-ignore
    const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({
        ...{ 'onApplied': {} },
        taskId: (__VLS_ctx.taskId),
        reportId: (__VLS_ctx.reportId),
        artifactType: (__VLS_ctx.selected.artifact_type),
        artifactVersion: (__VLS_ctx.selected.artifact_version || String(__VLS_ctx.runRevision || 1)),
        objectId: (__VLS_ctx.selected.object_id),
        current: (__VLS_ctx.selected.current),
    }));
    const __VLS_2 = __VLS_1({
        ...{ 'onApplied': {} },
        taskId: (__VLS_ctx.taskId),
        reportId: (__VLS_ctx.reportId),
        artifactType: (__VLS_ctx.selected.artifact_type),
        artifactVersion: (__VLS_ctx.selected.artifact_version || String(__VLS_ctx.runRevision || 1)),
        objectId: (__VLS_ctx.selected.object_id),
        current: (__VLS_ctx.selected.current),
    }, ...__VLS_functionalComponentArgsRest(__VLS_1));
    let __VLS_5;
    const __VLS_6 = {
        /** @type {typeof __VLS_5.applied} */
        onApplied: (...[$event]) => {
            if (!(__VLS_ctx.selected))
                throw 0;
            return (__VLS_ctx.qc.invalidateQueries({ queryKey: ['review-workspace', __VLS_ctx.taskId] }));
            // @ts-ignore
            [workspace, selected, selected, selected, selected, selected, selected, pageSize, offset, taskId, taskId, reportId, runRevision, qc,];
        },
    };
    var __VLS_3;
    var __VLS_4;
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
[];
const __VLS_export = (await import('vue')).defineComponent({
    __typeProps: {},
});
export default {};
