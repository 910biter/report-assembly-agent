import { computed, defineAsyncComponent, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useRoute, RouterLink } from "vue-router";
import { api } from "@/api/http";
import StatusBadge from "@/components/StatusBadge.vue";
import ArtifactReviewWorkspace from "@/components/ArtifactReviewWorkspace.vue";
import MaterialComparisonWorkspace from "@/components/MaterialComparisonWorkspace.vue";
const GraphNetwork = defineAsyncComponent(() => import("@/components/GraphNetwork.vue"));
const route = useRoute();
const qc = useQueryClient();
const taskId = String(route.params.taskId);
const active = ref(String(route.query.tab || "overview"));
const analysisType = ref("facts");
const detailsOpen = ref(false);
const selectedGraphEdge = ref(null);
const task = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => api(`/api/tasks/${taskId}`),
    refetchInterval: (q) => ["review", "done", "failed", "paused"].includes(String(q.state.data?.stage))
        ? 15000
        : 4000,
});
const isComparison = computed(() => task.data.value?.run_mode === "material_comparison");
watch(() => task.data.value?.run_mode, mode => {
    if (mode === "material_comparison" && !route.query.tab)
        active.value = "comparison";
}, { immediate: true });
const materials = useQuery({
    queryKey: ["task-materials", taskId],
    queryFn: () => api(`/api/tasks/${taskId}/materials`),
    enabled: computed(() => ["materials", "overview"].includes(active.value)),
    staleTime: 30000,
});
const analysis = useQuery({
    queryKey: ["task-analysis", taskId],
    queryFn: () => api(`/api/tasks/${taskId}/analysis`),
    enabled: computed(() => ["analysis", "overview"].includes(active.value)),
    staleTime: 30000,
});
const graph = useQuery({
    queryKey: ["task-graph", taskId],
    queryFn: () => api(`/api/tasks/${taskId}/graph`),
    enabled: computed(() => active.value === "analysis" && analysisType.value === "graph"),
    staleTime: 30000,
});
const graphChanges = useQuery({
    queryKey: ["task-graph-changes", taskId],
    queryFn: () => api(`/api/tasks/${taskId}/graph/changesets`),
    enabled: computed(() => active.value === "analysis" && analysisType.value === "graph"),
    staleTime: 30000,
});
const versions = useQuery({
    queryKey: ["report-versions", computed(() => task.data.value?.report_id)],
    queryFn: () => api(`/api/reports/${task.data.value?.report_id}/versions`),
    enabled: computed(() => active.value === "versions" && Boolean(task.data.value?.report_id)),
});
const command = useMutation({
    mutationFn: ({ path }) => api(path, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["task", taskId] }),
});
const stages = computed(() => task.data.value?.run_mode === "material_comparison"
    ? [
        {
            name: "新增材料准备",
            keys: [
                "created",
                "parsing",
                "dedup",
                "material_analysis",
                "planning",
            ],
        },
        { name: "证据与变化分析", keys: ["evidence", "conflict", "analysis"] },
        { name: "变化审阅", keys: ["review", "done"] },
    ]
    : [
        { name: "材料准备", keys: ["created", "parsing", "dedup"] },
        {
            name: "分析规划",
            keys: [
                "material_analysis",
                "planning",
                "evidence",
                "conflict",
                "analysis",
            ],
        },
        { name: "报告生成", keys: ["writing", "knowledge"] },
        { name: "审核完成", keys: ["review", "done"] },
    ]);
const taskTabs = computed(() => isComparison.value
    ? [["comparison", "对比结果"], ["materials", "新增材料"], ["runtime", "运行详情"]]
    : [["overview", "概览"], ["materials", "材料"], ["analysis", "分析"], ["report", "报告"], ["versions", "版本"], ["collaboration", "协作审阅"]]);
const stageIndex = computed(() => Math.max(0, stages.value.findIndex((x) => x.keys.includes(task.data.value?.stage || ""))));
const running = computed(() => !["created", "review", "done", "failed", "paused"].includes(task.data.value?.stage || "created"));
const facts = computed(() => analysis.data.value?.facts || []);
const inferences = computed(() => (analysis.data.value?.inferences || []).filter((x) => x.source_level === "MATERIAL_INFERENCE"));
const conflicts = computed(() => analysis.data.value?.conflicts || []);
function run() {
    command.mutate({ path: `/api/tasks/${taskId}/run` });
}
function control(op) {
    command.mutate({ path: `/api/tasks/${taskId}/control/${op}` });
}
function confidence(x) {
    return ({ high: "高", medium: "中", low: "低" }[String(x.confidence_level || "").toLowerCase()] || "需人工复核");
}
function versionsList() {
    const data = versions.data.value;
    return Array.isArray(data) ? data : data?.versions || [];
}
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['task-head']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-rail']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-step']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-step']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-step']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-step']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-step']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-step']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-step']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-step']} */ ;
/** @type {__VLS_StyleScopedClasses['run-details']} */ ;
/** @type {__VLS_StyleScopedClasses['run-details']} */ ;
/** @type {__VLS_StyleScopedClasses['run-details']} */ ;
/** @type {__VLS_StyleScopedClasses['status-summary']} */ ;
/** @type {__VLS_StyleScopedClasses['status-summary']} */ ;
/** @type {__VLS_StyleScopedClasses['status-summary']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-link']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-link']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-link']} */ ;
/** @type {__VLS_StyleScopedClasses['artifact-link']} */ ;
/** @type {__VLS_StyleScopedClasses['material-row']} */ ;
/** @type {__VLS_StyleScopedClasses['material-row']} */ ;
/** @type {__VLS_StyleScopedClasses['material-row']} */ ;
/** @type {__VLS_StyleScopedClasses['analysis-nav']} */ ;
/** @type {__VLS_StyleScopedClasses['analysis-nav']} */ ;
/** @type {__VLS_StyleScopedClasses['knowledge-item']} */ ;
/** @type {__VLS_StyleScopedClasses['knowledge-item']} */ ;
/** @type {__VLS_StyleScopedClasses['knowledge-item']} */ ;
/** @type {__VLS_StyleScopedClasses['knowledge-item']} */ ;
/** @type {__VLS_StyleScopedClasses['knowledge-item']} */ ;
/** @type {__VLS_StyleScopedClasses['knowledge-item']} */ ;
/** @type {__VLS_StyleScopedClasses['inference']} */ ;
/** @type {__VLS_StyleScopedClasses['knowledge-item']} */ ;
/** @type {__VLS_StyleScopedClasses['inference']} */ ;
/** @type {__VLS_StyleScopedClasses['knowledge-item']} */ ;
/** @type {__VLS_StyleScopedClasses['graph-summary']} */ ;
/** @type {__VLS_StyleScopedClasses['graph-summary']} */ ;
/** @type {__VLS_StyleScopedClasses['graph-edge']} */ ;
/** @type {__VLS_StyleScopedClasses['graph-changes']} */ ;
/** @type {__VLS_StyleScopedClasses['graph-changes']} */ ;
/** @type {__VLS_StyleScopedClasses['graph-changes']} */ ;
/** @type {__VLS_StyleScopedClasses['report-entry']} */ ;
/** @type {__VLS_StyleScopedClasses['report-entry']} */ ;
/** @type {__VLS_StyleScopedClasses['version-row']} */ ;
/** @type {__VLS_StyleScopedClasses['version-row']} */ ;
/** @type {__VLS_StyleScopedClasses['version-row']} */ ;
/** @type {__VLS_StyleScopedClasses['task-head']} */ ;
/** @type {__VLS_StyleScopedClasses['report-entry']} */ ;
/** @type {__VLS_StyleScopedClasses['workspace-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-rail']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-step']} */ ;
/** @type {__VLS_StyleScopedClasses['run-details']} */ ;
/** @type {__VLS_StyleScopedClasses['status-summary']} */ ;
/** @type {__VLS_StyleScopedClasses['analysis-layout']} */ ;
/** @type {__VLS_StyleScopedClasses['analysis-nav']} */ ;
/** @type {__VLS_StyleScopedClasses['analysis-nav']} */ ;
/** @type {__VLS_StyleScopedClasses['run-details']} */ ;
/** @type {__VLS_StyleScopedClasses['status-summary']} */ ;
/** @type {__VLS_StyleScopedClasses['version-row']} */ ;
/** @type {__VLS_StyleScopedClasses['version-row']} */ ;
if (__VLS_ctx.task.data.value) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "page-stack task-workspace" },
    });
    /** @type {__VLS_StyleScopedClasses['page-stack']} */ ;
    /** @type {__VLS_StyleScopedClasses['task-workspace']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({
        ...{ class: "task-head" },
    });
    /** @type {__VLS_StyleScopedClasses['task-head']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    let __VLS_0;
    /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
    RouterLink;
    // @ts-ignore
    const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({
        to: "/tasks",
        ...{ class: "back-link" },
    }));
    const __VLS_2 = __VLS_1({
        to: "/tasks",
        ...{ class: "back-link" },
    }, ...__VLS_functionalComponentArgsRest(__VLS_1));
    /** @type {__VLS_StyleScopedClasses['back-link']} */ ;
    const { default: __VLS_5 } = __VLS_3.slots;
    // @ts-ignore
    [task,];
    var __VLS_3;
    __VLS_asFunctionalElement1(__VLS_intrinsics.h1, __VLS_intrinsics.h1)({});
    (__VLS_ctx.task.data.value.theme);
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
        ...{ class: "muted" },
    });
    /** @type {__VLS_StyleScopedClasses['muted']} */ ;
    (__VLS_ctx.task.data.value.created_at || "—");
    (__VLS_ctx.task.data.value.material_count ||
        __VLS_ctx.task.data.value.material_ids?.length ||
        0);
    (__VLS_ctx.task.data.value.run_revision || 1);
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "button-row" },
    });
    /** @type {__VLS_StyleScopedClasses['button-row']} */ ;
    const __VLS_6 = StatusBadge;
    // @ts-ignore
    const __VLS_7 = __VLS_asFunctionalComponent1(__VLS_6, new __VLS_6({
        stage: (__VLS_ctx.task.data.value.stage),
    }));
    const __VLS_8 = __VLS_7({
        stage: (__VLS_ctx.task.data.value.stage),
    }, ...__VLS_functionalComponentArgsRest(__VLS_7));
    if (['created', 'failed'].includes(__VLS_ctx.task.data.value.stage)) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (__VLS_ctx.run) },
            ...{ class: "btn primary" },
            disabled: (__VLS_ctx.command.isPending.value),
        });
        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
        /** @type {__VLS_StyleScopedClasses['primary']} */ ;
        (__VLS_ctx.task.data.value.stage === "failed" ? "重新运行" : "开始运行");
    }
    if (__VLS_ctx.running) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.task.data.value))
                        throw 0;
                    if (!(__VLS_ctx.running))
                        throw 0;
                    return (__VLS_ctx.control('pause'));
                    // @ts-ignore
                    [task, task, task, task, task, task, task, task, run, command, running, control,];
                } },
            ...{ class: "btn" },
        });
        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
    }
    if (__VLS_ctx.task.data.value.stage === 'paused') {
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.task.data.value))
                        throw 0;
                    if (!(__VLS_ctx.task.data.value.stage === 'paused'))
                        throw 0;
                    return (__VLS_ctx.control('resume'));
                    // @ts-ignore
                    [task, control,];
                } },
            ...{ class: "btn primary" },
        });
        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
        /** @type {__VLS_StyleScopedClasses['primary']} */ ;
    }
    if (__VLS_ctx.task.data.value.report_id) {
        let __VLS_11;
        /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
        RouterLink;
        // @ts-ignore
        const __VLS_12 = __VLS_asFunctionalComponent1(__VLS_11, new __VLS_11({
            ...{ class: "btn" },
            to: (`/reports/${__VLS_ctx.task.data.value.report_id}`),
        }));
        const __VLS_13 = __VLS_12({
            ...{ class: "btn" },
            to: (`/reports/${__VLS_ctx.task.data.value.report_id}`),
        }, ...__VLS_functionalComponentArgsRest(__VLS_12));
        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
        const { default: __VLS_16 } = __VLS_14.slots;
        // @ts-ignore
        [task, task,];
        var __VLS_14;
    }
    else if (__VLS_ctx.isComparison && __VLS_ctx.task.data.value.comparison_report_id) {
        let __VLS_17;
        /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
        RouterLink;
        // @ts-ignore
        const __VLS_18 = __VLS_asFunctionalComponent1(__VLS_17, new __VLS_17({
            ...{ class: "btn" },
            to: (`/reports/${__VLS_ctx.task.data.value.comparison_report_id}`),
        }));
        const __VLS_19 = __VLS_18({
            ...{ class: "btn" },
            to: (`/reports/${__VLS_ctx.task.data.value.comparison_report_id}`),
        }, ...__VLS_functionalComponentArgsRest(__VLS_18));
        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
        const { default: __VLS_22 } = __VLS_20.slots;
        // @ts-ignore
        [task, task, isComparison,];
        var __VLS_20;
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
        ...{ class: "surface progress-block" },
    });
    /** @type {__VLS_StyleScopedClasses['surface']} */ ;
    /** @type {__VLS_StyleScopedClasses['progress-block']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "progress-rail" },
        ...{ class: ({ compact: __VLS_ctx.isComparison }) },
    });
    /** @type {__VLS_StyleScopedClasses['progress-rail']} */ ;
    /** @type {__VLS_StyleScopedClasses['compact']} */ ;
    for (const [item, index] of __VLS_vFor((__VLS_ctx.stages))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            key: (item.name),
            ...{ class: "progress-step" },
            ...{ class: ({ done: index < __VLS_ctx.stageIndex, current: index === __VLS_ctx.stageIndex }) },
        });
        /** @type {__VLS_StyleScopedClasses['progress-step']} */ ;
        /** @type {__VLS_StyleScopedClasses['done']} */ ;
        /** @type {__VLS_StyleScopedClasses['current']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        (index + 1);
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (item.name);
        __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
        (index < __VLS_ctx.stageIndex
            ? "已完成"
            : index === __VLS_ctx.stageIndex
                ? __VLS_ctx.task.data.value.stage === "review"
                    ? "待审核"
                    : "正在进行"
                : "等待中");
        // @ts-ignore
        [task, isComparison, stages, stageIndex, stageIndex, stageIndex, stageIndex,];
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.task.data.value))
                    throw 0;
                return (__VLS_ctx.detailsOpen = !__VLS_ctx.detailsOpen);
                // @ts-ignore
                [detailsOpen, detailsOpen,];
            } },
        ...{ class: "details-toggle" },
    });
    /** @type {__VLS_StyleScopedClasses['details-toggle']} */ ;
    (__VLS_ctx.detailsOpen ? "收起运行详情" : "查看运行详情");
    if (__VLS_ctx.detailsOpen) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "run-details" },
        });
        /** @type {__VLS_StyleScopedClasses['run-details']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (__VLS_ctx.task.data.value.stage);
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (__VLS_ctx.task.data.value.queue_status?.status || "—");
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (__VLS_ctx.task.data.value.parse_progress?.done || 0);
        (__VLS_ctx.task.data.value.parse_progress?.total || "—");
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (__VLS_ctx.task.data.value.write_progress?.done || 0);
        (__VLS_ctx.task.data.value.write_progress?.total || "—");
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.nav, __VLS_intrinsics.nav)({
        ...{ class: "tabs workspace-tabs" },
    });
    /** @type {__VLS_StyleScopedClasses['tabs']} */ ;
    /** @type {__VLS_StyleScopedClasses['workspace-tabs']} */ ;
    for (const [tab] of __VLS_vFor((__VLS_ctx.taskTabs))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.task.data.value))
                        throw 0;
                    return (__VLS_ctx.active = tab[0]);
                    // @ts-ignore
                    [task, task, task, task, task, task, detailsOpen, detailsOpen, taskTabs, active,];
                } },
            key: (tab[0]),
            ...{ class: "tab" },
            ...{ class: ({ active: __VLS_ctx.active === tab[0] }) },
        });
        /** @type {__VLS_StyleScopedClasses['tab']} */ ;
        /** @type {__VLS_StyleScopedClasses['active']} */ ;
        (tab[1]);
        // @ts-ignore
        [active,];
    }
    if (__VLS_ctx.active === 'comparison' && __VLS_ctx.isComparison) {
        const __VLS_23 = MaterialComparisonWorkspace;
        // @ts-ignore
        const __VLS_24 = __VLS_asFunctionalComponent1(__VLS_23, new __VLS_23({
            embedded: true,
            reportId: (Number(__VLS_ctx.task.data.value.comparison_report_id)),
            comparisonId: (Number(__VLS_ctx.task.data.value.comparison_id)),
        }));
        const __VLS_25 = __VLS_24({
            embedded: true,
            reportId: (Number(__VLS_ctx.task.data.value.comparison_report_id)),
            comparisonId: (Number(__VLS_ctx.task.data.value.comparison_id)),
        }, ...__VLS_functionalComponentArgsRest(__VLS_24));
    }
    else if (__VLS_ctx.active === 'runtime' && __VLS_ctx.isComparison) {
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
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "status-summary" },
        });
        /** @type {__VLS_StyleScopedClasses['status-summary']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (__VLS_ctx.task.data.value.stage);
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (__VLS_ctx.task.data.value.queue_status?.status || "—");
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (__VLS_ctx.task.data.value.parse_progress?.done || 0);
        (__VLS_ctx.task.data.value.parse_progress?.total || "—");
    }
    else if (__VLS_ctx.active === 'overview') {
        __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
            ...{ class: "workspace-grid" },
        });
        /** @type {__VLS_StyleScopedClasses['workspace-grid']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "main-column" },
        });
        /** @type {__VLS_StyleScopedClasses['main-column']} */ ;
        if (__VLS_ctx.task.data.value.stage === 'failed') {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "notice warning" },
            });
            /** @type {__VLS_StyleScopedClasses['notice']} */ ;
            /** @type {__VLS_StyleScopedClasses['warning']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.br)({});
            (__VLS_ctx.task.data.value.error ||
                __VLS_ctx.task.data.value.failure_reason ||
                "请查看运行详情后重新运行。");
        }
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
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
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "status-summary" },
        });
        /** @type {__VLS_StyleScopedClasses['status-summary']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (__VLS_ctx.stages[__VLS_ctx.stageIndex]?.name);
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (__VLS_ctx.task.data.value.report_id ? "报告草稿" : "分析产物");
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (__VLS_ctx.task.data.value.stage === "review"
            ? "完成审核"
            : __VLS_ctx.task.data.value.stage === "done"
                ? "导出或增量更新"
                : __VLS_ctx.running
                    ? "等待当前阶段完成"
                    : "开始运行");
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
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
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "metric-strip flat" },
        });
        /** @type {__VLS_StyleScopedClasses['metric-strip']} */ ;
        /** @type {__VLS_StyleScopedClasses['flat']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "metric" },
        });
        /** @type {__VLS_StyleScopedClasses['metric']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
        (__VLS_ctx.materials.data.value?.length ||
            __VLS_ctx.task.data.value.material_ids?.length ||
            0);
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "metric" },
        });
        /** @type {__VLS_StyleScopedClasses['metric']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
        (__VLS_ctx.facts.length);
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "metric" },
        });
        /** @type {__VLS_StyleScopedClasses['metric']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
        (__VLS_ctx.inferences.length);
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "metric" },
        });
        /** @type {__VLS_StyleScopedClasses['metric']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
        (__VLS_ctx.conflicts.length + (__VLS_ctx.task.data.value.qa_notes?.length || 0));
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
            ...{ class: "side-column" },
        });
        /** @type {__VLS_StyleScopedClasses['side-column']} */ ;
        if (__VLS_ctx.task.data.value.run_mode === 'material_comparison') {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "surface section-block" },
            });
            /** @type {__VLS_StyleScopedClasses['surface']} */ ;
            /** @type {__VLS_StyleScopedClasses['section-block']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
            if (__VLS_ctx.task.data.value.material_comparison?.summary) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
                (__VLS_ctx.task.data.value.material_comparison.summary.new_fact_count || 0);
                (__VLS_ctx.task.data.value.material_comparison.summary.affected_sections
                    ?.length || 0);
                let __VLS_28;
                /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
                RouterLink;
                // @ts-ignore
                const __VLS_29 = __VLS_asFunctionalComponent1(__VLS_28, new __VLS_28({
                    ...{ class: "btn primary" },
                    to: (`/reports/${__VLS_ctx.task.data.value.comparison_report_id}`),
                }));
                const __VLS_30 = __VLS_29({
                    ...{ class: "btn primary" },
                    to: (`/reports/${__VLS_ctx.task.data.value.comparison_report_id}`),
                }, ...__VLS_functionalComponentArgsRest(__VLS_29));
                /** @type {__VLS_StyleScopedClasses['btn']} */ ;
                /** @type {__VLS_StyleScopedClasses['primary']} */ ;
                const { default: __VLS_33 } = __VLS_31.slots;
                // @ts-ignore
                [task, task, task, task, task, task, task, task, task, task, task, task, task, task, task, task, task, task, task, running, isComparison, isComparison, stages, stageIndex, active, active, active, materials, facts, inferences, conflicts,];
                var __VLS_31;
            }
            else {
                __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
                    ...{ class: "muted" },
                });
                /** @type {__VLS_StyleScopedClasses['muted']} */ ;
            }
        }
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "surface section-block" },
        });
        /** @type {__VLS_StyleScopedClasses['surface']} */ ;
        /** @type {__VLS_StyleScopedClasses['section-block']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
        if (__VLS_ctx.task.data.value.report_id) {
            let __VLS_34;
            /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
            RouterLink;
            // @ts-ignore
            const __VLS_35 = __VLS_asFunctionalComponent1(__VLS_34, new __VLS_34({
                to: (`/reports/${__VLS_ctx.task.data.value.report_id}`),
                ...{ class: "artifact-link" },
            }));
            const __VLS_36 = __VLS_35({
                to: (`/reports/${__VLS_ctx.task.data.value.report_id}`),
                ...{ class: "artifact-link" },
            }, ...__VLS_functionalComponentArgsRest(__VLS_35));
            /** @type {__VLS_StyleScopedClasses['artifact-link']} */ ;
            const { default: __VLS_39 } = __VLS_37.slots;
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
            (__VLS_ctx.task.data.value.stage === "review"
                ? "可以进入审核"
                : "持续生成中");
            __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
            // @ts-ignore
            [task, task, task,];
            var __VLS_37;
        }
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.task.data.value))
                        throw 0;
                    if (!!(__VLS_ctx.active === 'comparison' && __VLS_ctx.isComparison))
                        throw 0;
                    if (!!(__VLS_ctx.active === 'runtime' && __VLS_ctx.isComparison))
                        throw 0;
                    if (!(__VLS_ctx.active === 'overview'))
                        throw 0;
                    return (__VLS_ctx.active = 'materials');
                    // @ts-ignore
                    [active,];
                } },
            ...{ class: "artifact-link" },
        });
        /** @type {__VLS_StyleScopedClasses['artifact-link']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        (__VLS_ctx.materials.data.value?.filter((x) => x.units_count > 0)
            .length || 0);
        __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.task.data.value))
                        throw 0;
                    if (!!(__VLS_ctx.active === 'comparison' && __VLS_ctx.isComparison))
                        throw 0;
                    if (!!(__VLS_ctx.active === 'runtime' && __VLS_ctx.isComparison))
                        throw 0;
                    if (!(__VLS_ctx.active === 'overview'))
                        throw 0;
                    return (__VLS_ctx.active = 'analysis');
                    // @ts-ignore
                    [active, materials,];
                } },
            ...{ class: "artifact-link" },
        });
        /** @type {__VLS_StyleScopedClasses['artifact-link']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        (__VLS_ctx.facts.length);
        (__VLS_ctx.inferences.length);
        __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
    }
    else if (__VLS_ctx.active === 'materials') {
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
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "data-list" },
        });
        /** @type {__VLS_StyleScopedClasses['data-list']} */ ;
        for (const [item] of __VLS_vFor((__VLS_ctx.materials.data.value || []))) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                key: (item.filename),
                ...{ class: "data-row material-row" },
            });
            /** @type {__VLS_StyleScopedClasses['data-row']} */ ;
            /** @type {__VLS_StyleScopedClasses['material-row']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
            (item.filename);
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
            (item.file_type);
            (item.units_count);
            if (item.pages?.length) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
                (item.pages.length);
            }
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                ...{ class: "badge" },
                ...{ class: (item.parse_status === 'error'
                        ? 'danger'
                        : item.parse_status === 'ok'
                            ? 'success'
                            : 'warning') },
            });
            /** @type {__VLS_StyleScopedClasses['badge']} */ ;
            (item.parse_status === "ok"
                ? "已解析"
                : item.parse_status === "partial"
                    ? "正文可用"
                    : item.parse_status === "error"
                        ? "解析失败"
                        : "处理中");
            // @ts-ignore
            [active, materials, facts, inferences,];
        }
    }
    else if (__VLS_ctx.active === 'analysis') {
        __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
            ...{ class: "analysis-layout" },
        });
        /** @type {__VLS_StyleScopedClasses['analysis-layout']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
            ...{ class: "analysis-nav surface" },
        });
        /** @type {__VLS_StyleScopedClasses['analysis-nav']} */ ;
        /** @type {__VLS_StyleScopedClasses['surface']} */ ;
        for (const [item] of __VLS_vFor(([
            ['facts', `事实 ${__VLS_ctx.facts.length}`],
            ['inferences', `分析判断 ${__VLS_ctx.inferences.length}`],
            [
                'graph',
                `关系网络 ${__VLS_ctx.graph.data.value?.stats?.assertion_count || 0}`,
            ],
            ['conflicts', `冲突与待核验 ${__VLS_ctx.conflicts.length}`],
            ['qa', `质量检查 ${__VLS_ctx.task.data.value.qa_notes?.length || 0}`],
        ]))) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.task.data.value))
                            throw 0;
                        if (!!(__VLS_ctx.active === 'comparison' && __VLS_ctx.isComparison))
                            throw 0;
                        if (!!(__VLS_ctx.active === 'runtime' && __VLS_ctx.isComparison))
                            throw 0;
                        if (!!(__VLS_ctx.active === 'overview'))
                            throw 0;
                        if (!!(__VLS_ctx.active === 'materials'))
                            throw 0;
                        if (!(__VLS_ctx.active === 'analysis'))
                            throw 0;
                        return (__VLS_ctx.analysisType = item[0]);
                        // @ts-ignore
                        [task, active, facts, inferences, conflicts, graph, analysisType,];
                    } },
                key: (item[0]),
                ...{ class: ({ active: __VLS_ctx.analysisType === item[0] }) },
            });
            /** @type {__VLS_StyleScopedClasses['active']} */ ;
            (item[1]);
            // @ts-ignore
            [analysisType,];
        }
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "surface analysis-content" },
        });
        /** @type {__VLS_StyleScopedClasses['surface']} */ ;
        /** @type {__VLS_StyleScopedClasses['analysis-content']} */ ;
        if (__VLS_ctx.analysisType === 'facts') {
            for (const [fact] of __VLS_vFor((__VLS_ctx.facts))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.details, __VLS_intrinsics.details)({
                    key: (fact.id),
                    ...{ class: "knowledge-item" },
                });
                /** @type {__VLS_StyleScopedClasses['knowledge-item']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.summary, __VLS_intrinsics.summary)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
                (fact.content);
                __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
                (fact.evidence?.length || 0);
                for (const [ev] of __VLS_vFor((fact.evidence || []))) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.blockquote, __VLS_intrinsics.blockquote)({
                        key: (ev.quote),
                    });
                    (ev.source_file);
                    (ev.page ? ` 第 ${ev.page} 页` : "");
                    (ev.quote);
                    // @ts-ignore
                    [facts, analysisType,];
                }
                // @ts-ignore
                [];
            }
        }
        else if (__VLS_ctx.analysisType === 'inferences') {
            for (const [item] of __VLS_vFor((__VLS_ctx.inferences))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({
                    key: (item.id),
                    ...{ class: "knowledge-item inference" },
                });
                /** @type {__VLS_StyleScopedClasses['knowledge-item']} */ ;
                /** @type {__VLS_StyleScopedClasses['inference']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                    ...{ class: "badge success" },
                });
                /** @type {__VLS_StyleScopedClasses['badge']} */ ;
                /** @type {__VLS_StyleScopedClasses['success']} */ ;
                (__VLS_ctx.confidence(item));
                __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
                (item.based_fact_ids?.join("、") || "待核验");
                __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
                (item.content);
                if (item.reasoning_chain) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.blockquote, __VLS_intrinsics.blockquote)({});
                    (item.reasoning_chain);
                }
                // @ts-ignore
                [inferences, analysisType, confidence,];
            }
        }
        else if (__VLS_ctx.analysisType === 'graph') {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "graph-summary" },
            });
            /** @type {__VLS_StyleScopedClasses['graph-summary']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                ...{ class: "badge" },
                ...{ class: (__VLS_ctx.graph.data.value?.mode === 'active' ? 'success' : 'warning') },
            });
            /** @type {__VLS_StyleScopedClasses['badge']} */ ;
            (__VLS_ctx.graph.data.value?.mode === "active"
                ? "Graph RAG 已启用"
                : "图谱观测模式");
            __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
            if (__VLS_ctx.graph.data.value?.edges?.length) {
                let __VLS_40;
                /** @ts-ignore @type { | typeof __VLS_components.GraphNetwork} */
                GraphNetwork;
                // @ts-ignore
                const __VLS_41 = __VLS_asFunctionalComponent1(__VLS_40, new __VLS_40({
                    ...{ 'onSelect': {} },
                    nodes: (__VLS_ctx.graph.data.value.nodes),
                    edges: (__VLS_ctx.graph.data.value.edges),
                }));
                const __VLS_42 = __VLS_41({
                    ...{ 'onSelect': {} },
                    nodes: (__VLS_ctx.graph.data.value.nodes),
                    edges: (__VLS_ctx.graph.data.value.edges),
                }, ...__VLS_functionalComponentArgsRest(__VLS_41));
                let __VLS_45;
                const __VLS_46 = {
                    /** @type {typeof __VLS_45.select} */
                    onSelect: (...[$event]) => {
                        if (!(__VLS_ctx.task.data.value))
                            throw 0;
                        if (!!(__VLS_ctx.active === 'comparison' && __VLS_ctx.isComparison))
                            throw 0;
                        if (!!(__VLS_ctx.active === 'runtime' && __VLS_ctx.isComparison))
                            throw 0;
                        if (!!(__VLS_ctx.active === 'overview'))
                            throw 0;
                        if (!!(__VLS_ctx.active === 'materials'))
                            throw 0;
                        if (!(__VLS_ctx.active === 'analysis'))
                            throw 0;
                        if (!!(__VLS_ctx.analysisType === 'facts'))
                            throw 0;
                        if (!!(__VLS_ctx.analysisType === 'inferences'))
                            throw 0;
                        if (!(__VLS_ctx.analysisType === 'graph'))
                            throw 0;
                        if (!(__VLS_ctx.graph.data.value?.edges?.length))
                            throw 0;
                        return (__VLS_ctx.selectedGraphEdge = $event);
                        // @ts-ignore
                        [graph, graph, graph, graph, graph, analysisType, selectedGraphEdge,];
                    },
                };
                var __VLS_43;
                var __VLS_44;
            }
            if (__VLS_ctx.selectedGraphEdge) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({
                    ...{ class: "knowledge-item graph-edge" },
                });
                /** @type {__VLS_StyleScopedClasses['knowledge-item']} */ ;
                /** @type {__VLS_StyleScopedClasses['graph-edge']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
                (__VLS_ctx.selectedGraphEdge.subject_name);
                (__VLS_ctx.selectedGraphEdge.predicate);
                (__VLS_ctx.selectedGraphEdge.object_name ||
                    __VLS_ctx.selectedGraphEdge.object_value);
                __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                    ...{ class: "badge" },
                });
                /** @type {__VLS_StyleScopedClasses['badge']} */ ;
                (__VLS_ctx.selectedGraphEdge.status === "confirmed" ? "已确认" : "已校验");
                __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
                (__VLS_ctx.selectedGraphEdge.fact_ids?.join("、") || "—");
                (__VLS_ctx.confidence({ confidence_level: __VLS_ctx.selectedGraphEdge.confidence }));
            }
            if (__VLS_ctx.graphChanges.data.value?.changesets?.length) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    ...{ class: "graph-changes" },
                });
                /** @type {__VLS_StyleScopedClasses['graph-changes']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
                for (const [change] of __VLS_vFor((__VLS_ctx.graphChanges.data.value.changesets.slice(0, 5)))) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                        key: (change.id),
                    });
                    (change.change_type);
                    (change.status);
                    // @ts-ignore
                    [confidence, selectedGraphEdge, selectedGraphEdge, selectedGraphEdge, selectedGraphEdge, selectedGraphEdge, selectedGraphEdge, selectedGraphEdge, selectedGraphEdge, graphChanges, graphChanges,];
                }
            }
            if (!__VLS_ctx.graph.data.value?.edges?.length) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    ...{ class: "empty" },
                });
                /** @type {__VLS_StyleScopedClasses['empty']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
            }
        }
        else if (__VLS_ctx.analysisType === 'conflicts') {
            for (const [item] of __VLS_vFor((__VLS_ctx.conflicts))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({
                    key: (item.id),
                    ...{ class: "knowledge-item conflict" },
                });
                /** @type {__VLS_StyleScopedClasses['knowledge-item']} */ ;
                /** @type {__VLS_StyleScopedClasses['conflict']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
                (item.fact_key);
                for (const [entry] of __VLS_vFor((item.entries || []))) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
                        key: (entry.statement),
                    });
                    (entry.file);
                    (entry.statement);
                    // @ts-ignore
                    [conflicts, graph, analysisType,];
                }
                // @ts-ignore
                [];
            }
            if (!__VLS_ctx.conflicts.length) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    ...{ class: "empty" },
                });
                /** @type {__VLS_StyleScopedClasses['empty']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
            }
        }
        else {
            for (const [item, index] of __VLS_vFor((__VLS_ctx.task.data.value.qa_notes || []))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({
                    key: (index),
                    ...{ class: "knowledge-item conflict" },
                });
                /** @type {__VLS_StyleScopedClasses['knowledge-item']} */ ;
                /** @type {__VLS_StyleScopedClasses['conflict']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
                (item.type || "质量问题");
                __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
                (item.note || item.quote);
                // @ts-ignore
                [task, conflicts,];
            }
            if (!__VLS_ctx.task.data.value.qa_notes?.length) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    ...{ class: "empty" },
                });
                /** @type {__VLS_StyleScopedClasses['empty']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
            }
        }
    }
    else if (__VLS_ctx.active === 'collaboration') {
        const __VLS_47 = ArtifactReviewWorkspace;
        // @ts-ignore
        const __VLS_48 = __VLS_asFunctionalComponent1(__VLS_47, new __VLS_47({
            taskId: (__VLS_ctx.taskId),
            reportId: (__VLS_ctx.task.data.value.report_id),
            runRevision: (__VLS_ctx.task.data.value.run_revision || 1),
        }));
        const __VLS_49 = __VLS_48({
            taskId: (__VLS_ctx.taskId),
            reportId: (__VLS_ctx.task.data.value.report_id),
            runRevision: (__VLS_ctx.task.data.value.run_revision || 1),
        }, ...__VLS_functionalComponentArgsRest(__VLS_48));
    }
    else if (__VLS_ctx.active === 'report') {
        __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
            ...{ class: "surface section-block report-entry" },
        });
        /** @type {__VLS_StyleScopedClasses['surface']} */ ;
        /** @type {__VLS_StyleScopedClasses['section-block']} */ ;
        /** @type {__VLS_StyleScopedClasses['report-entry']} */ ;
        if (__VLS_ctx.task.data.value.report_id) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
            (__VLS_ctx.task.data.value.stage === "review"
                ? "报告已形成，可以进入审核"
                : "报告草稿正在生成");
            __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
                ...{ class: "muted" },
            });
            /** @type {__VLS_StyleScopedClasses['muted']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "button-row" },
            });
            /** @type {__VLS_StyleScopedClasses['button-row']} */ ;
            let __VLS_52;
            /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
            RouterLink;
            // @ts-ignore
            const __VLS_53 = __VLS_asFunctionalComponent1(__VLS_52, new __VLS_52({
                ...{ class: "btn primary" },
                to: (`/reports/${__VLS_ctx.task.data.value.report_id}`),
            }));
            const __VLS_54 = __VLS_53({
                ...{ class: "btn primary" },
                to: (`/reports/${__VLS_ctx.task.data.value.report_id}`),
            }, ...__VLS_functionalComponentArgsRest(__VLS_53));
            /** @type {__VLS_StyleScopedClasses['btn']} */ ;
            /** @type {__VLS_StyleScopedClasses['primary']} */ ;
            const { default: __VLS_57 } = __VLS_55.slots;
            // @ts-ignore
            [task, task, task, task, task, task, active, active, taskId,];
            var __VLS_55;
            if (['review', 'done'].includes(__VLS_ctx.task.data.value.stage)) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.a, __VLS_intrinsics.a)({
                    ...{ class: "btn" },
                    href: (`/api/reports/${__VLS_ctx.task.data.value.report_id}/export`),
                });
                /** @type {__VLS_StyleScopedClasses['btn']} */ ;
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
    }
    else {
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
        if (__VLS_ctx.versionsList().length) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "data-list" },
            });
            /** @type {__VLS_StyleScopedClasses['data-list']} */ ;
            for (const [item] of __VLS_vFor((__VLS_ctx.versionsList()))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    key: (item.id),
                    ...{ class: "data-row version-row" },
                });
                /** @type {__VLS_StyleScopedClasses['data-row']} */ ;
                /** @type {__VLS_StyleScopedClasses['version-row']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
                (item.version_label || item.version_no);
                __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
                (item.change_summary || "报告版本快照");
                __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
                (item.created_at || "—");
                let __VLS_58;
                /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
                RouterLink;
                // @ts-ignore
                const __VLS_59 = __VLS_asFunctionalComponent1(__VLS_58, new __VLS_58({
                    ...{ class: "btn tertiary" },
                    to: (`/reports/${__VLS_ctx.task.data.value.report_id}?version=${item.id}`),
                }));
                const __VLS_60 = __VLS_59({
                    ...{ class: "btn tertiary" },
                    to: (`/reports/${__VLS_ctx.task.data.value.report_id}?version=${item.id}`),
                }, ...__VLS_functionalComponentArgsRest(__VLS_59));
                /** @type {__VLS_StyleScopedClasses['btn']} */ ;
                /** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
                const { default: __VLS_63 } = __VLS_61.slots;
                // @ts-ignore
                [task, task, task, versionsList, versionsList,];
                var __VLS_61;
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
    }
}
else if (__VLS_ctx.task.isLoading.value) {
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
// @ts-ignore
[task,];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
