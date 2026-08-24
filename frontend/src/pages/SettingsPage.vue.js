import { computed, ref } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { api } from "@/api/http";
const health = useQuery({ queryKey: ["health"], queryFn: () => api("/api/health"), refetchInterval: 30_000 });
const queues = useQuery({ queryKey: ["queues"], queryFn: () => api("/api/system/queues"), refetchInterval: 10_000 });
const resources = useQuery({ queryKey: ["resources"], queryFn: () => api("/api/system/resources"), refetchInterval: 15_000 });
const performance = useQuery({ queryKey: ["performance-summary"], queryFn: () => api("/api/system/performance-summary"), refetchInterval: 30_000 });
const restarting = ref(false);
const restartMessage = ref("");
const totals = computed(() => performance.data.value?.profile?.totals || {});
const stages = computed(() => [...(performance.data.value?.profile?.by_stage_workload || [])]
    .filter((item) => Number(item.calls || 0) > 0)
    .sort((a, b) => Number(b.latency_seconds || 0) - Number(a.latency_seconds || 0))
    .slice(0, 7));
const maxStageLatency = computed(() => Math.max(...stages.value.map((item) => Number(item.latency_seconds || 0)), 1));
const stageNames = {
    material_analysis: "材料理解", planning: "分析规划", evidence: "证据提取", conflict: "冲突核验",
    analysis: "综合分析", final_planning: "报告规划", writing: "报告成文", knowledge: "知识沉淀", qa: "质量检查",
};
function stageName(stage) { return stageNames[stage] || stage || "其他"; }
function compact(value) {
    const number = Number(value || 0);
    if (number >= 1_000_000)
        return `${(number / 1_000_000).toFixed(2)}M`;
    if (number >= 1_000)
        return `${(number / 1_000).toFixed(1)}K`;
    return String(Math.round(number));
}
function duration(seconds) {
    const value = Number(seconds || 0);
    if (value >= 3600)
        return `${(value / 3600).toFixed(1)} 小时`;
    if (value >= 60)
        return `${Math.round(value / 60)} 分钟`;
    return `${value.toFixed(1)} 秒`;
}
function percent(value, total) { return Math.min(100, Math.max(0, total ? Number(value || 0) / total * 100 : 0)); }
const runningTaskId = computed(() => String(queues.data.value?.tasks?.running_task_id || ""));
async function restartServices() {
    if (runningTaskId.value || restarting.value)
        return;
    if (!confirm("确认重启报告整编服务？页面会短暂断开，并在服务恢复后自动刷新。"))
        return;
    restarting.value = true;
    restartMessage.value = "正在提交重启请求…";
    try {
        await api("/api/system/restart", { method: "POST" });
        restartMessage.value = "服务正在重启，等待恢复…";
        await new Promise(resolve => setTimeout(resolve, 2500));
        for (let attempt = 0; attempt < 30; attempt += 1) {
            try {
                const response = await fetch("/api/health", { cache: "no-store" });
                if (response.ok) {
                    location.reload();
                    return;
                }
            }
            catch { /* The expected downtime while the process is replaced. */ }
            await new Promise(resolve => setTimeout(resolve, 2000));
        }
        restartMessage.value = "服务恢复超时，请检查重启日志。";
    }
    catch (error) {
        restartMessage.value = error?.payload?.error === "TASK_RUNNING"
            ? `任务 ${error.payload.running_task_id} 正在运行，暂不能重启。`
            : (error.message || "重启请求失败");
    }
    finally {
        restarting.value = false;
    }
}
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['restart-button']} */ ;
/** @type {__VLS_StyleScopedClasses['task-context']} */ ;
/** @type {__VLS_StyleScopedClasses['task-context']} */ ;
/** @type {__VLS_StyleScopedClasses['task-context']} */ ;
/** @type {__VLS_StyleScopedClasses['task-context']} */ ;
/** @type {__VLS_StyleScopedClasses['task-context']} */ ;
/** @type {__VLS_StyleScopedClasses['task-context']} */ ;
/** @type {__VLS_StyleScopedClasses['resource-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['resource-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['resource-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['resource-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['resource-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['resource-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['run-totals']} */ ;
/** @type {__VLS_StyleScopedClasses['run-totals']} */ ;
/** @type {__VLS_StyleScopedClasses['run-totals']} */ ;
/** @type {__VLS_StyleScopedClasses['run-totals']} */ ;
/** @type {__VLS_StyleScopedClasses['run-totals']} */ ;
/** @type {__VLS_StyleScopedClasses['run-totals']} */ ;
/** @type {__VLS_StyleScopedClasses['run-totals']} */ ;
/** @type {__VLS_StyleScopedClasses['stage-title']} */ ;
/** @type {__VLS_StyleScopedClasses['stage-row']} */ ;
/** @type {__VLS_StyleScopedClasses['stage-row']} */ ;
/** @type {__VLS_StyleScopedClasses['stage-row']} */ ;
/** @type {__VLS_StyleScopedClasses['stage-row']} */ ;
/** @type {__VLS_StyleScopedClasses['stage-row']} */ ;
/** @type {__VLS_StyleScopedClasses['bar']} */ ;
/** @type {__VLS_StyleScopedClasses['performance-empty']} */ ;
/** @type {__VLS_StyleScopedClasses['resource-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['workload-layout']} */ ;
/** @type {__VLS_StyleScopedClasses['settings-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['resource-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['task-context']} */ ;
/** @type {__VLS_StyleScopedClasses['stage-row']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "page-stack settings-page" },
});
/** @type {__VLS_StyleScopedClasses['page-stack']} */ ;
/** @type {__VLS_StyleScopedClasses['settings-page']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({
    ...{ class: "page-header" },
});
/** @type {__VLS_StyleScopedClasses['page-header']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.h1, __VLS_intrinsics.h1)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
    ...{ class: "updated-at" },
});
/** @type {__VLS_StyleScopedClasses['updated-at']} */ ;
(__VLS_ctx.resources.data.value?.collected_at || '等待中');
__VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
    ...{ class: "settings-grid" },
});
/** @type {__VLS_StyleScopedClasses['settings-grid']} */ ;
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
    ...{ class: "service-actions" },
});
/** @type {__VLS_StyleScopedClasses['service-actions']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
    ...{ class: "badge" },
    ...{ class: (__VLS_ctx.health.data.value?.error ? 'danger' : 'success') },
});
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
(__VLS_ctx.health.data.value?.error ? '连接异常' : '服务可用');
__VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
    ...{ onClick: (__VLS_ctx.restartServices) },
    ...{ class: "btn restart-button" },
    disabled: (Boolean(__VLS_ctx.runningTaskId) || __VLS_ctx.restarting),
});
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['restart-button']} */ ;
(__VLS_ctx.restarting ? '正在重启…' : '重启服务');
__VLS_asFunctionalElement1(__VLS_intrinsics.dl, __VLS_intrinsics.dl)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({
    ...{ class: "mono" },
});
/** @type {__VLS_StyleScopedClasses['mono']} */ ;
(__VLS_ctx.health.data.value?.gateway_url || '—');
__VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
(__VLS_ctx.health.data.value?.version || '—');
__VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({
    ...{ class: "mono" },
});
/** @type {__VLS_StyleScopedClasses['mono']} */ ;
(__VLS_ctx.health.data.value?.runtime_root || '—');
if (__VLS_ctx.runningTaskId) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "restart-note" },
    });
    /** @type {__VLS_StyleScopedClasses['restart-note']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "mono" },
    });
    /** @type {__VLS_StyleScopedClasses['mono']} */ ;
    (__VLS_ctx.runningTaskId);
}
else if (__VLS_ctx.restartMessage) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "restart-note" },
    });
    /** @type {__VLS_StyleScopedClasses['restart-note']} */ ;
    (__VLS_ctx.restartMessage);
}
if (__VLS_ctx.health.data.value?.error) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "notice warning" },
    });
    /** @type {__VLS_StyleScopedClasses['notice']} */ ;
    /** @type {__VLS_StyleScopedClasses['warning']} */ ;
    (__VLS_ctx.health.data.value.error);
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
__VLS_asFunctionalElement1(__VLS_intrinsics.dl, __VLS_intrinsics.dl)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({
    ...{ class: "mono" },
});
/** @type {__VLS_StyleScopedClasses['mono']} */ ;
(__VLS_ctx.queues.data.value?.tasks?.running_task_id || '空闲');
__VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
(__VLS_ctx.queues.data.value?.tasks?.queued_task_ids?.length || 0);
__VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
(__VLS_ctx.queues.data.value?.llm?.queued || __VLS_ctx.queues.data.value?.llm?.queue_size || 0);
__VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
    ...{ class: "surface performance-panel" },
});
/** @type {__VLS_StyleScopedClasses['surface']} */ ;
/** @type {__VLS_StyleScopedClasses['performance-panel']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "section-head performance-head" },
});
/** @type {__VLS_StyleScopedClasses['section-head']} */ ;
/** @type {__VLS_StyleScopedClasses['performance-head']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
    ...{ class: "muted" },
});
/** @type {__VLS_StyleScopedClasses['muted']} */ ;
if (__VLS_ctx.performance.data.value?.task) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "task-context" },
    });
    /** @type {__VLS_StyleScopedClasses['task-context']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (__VLS_ctx.performance.data.value.task.theme);
    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({
        ...{ class: "mono" },
    });
    /** @type {__VLS_StyleScopedClasses['mono']} */ ;
    (__VLS_ctx.performance.data.value.task.task_id);
}
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "resource-grid" },
});
/** @type {__VLS_StyleScopedClasses['resource-grid']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
(__VLS_ctx.resources.data.value?.gpu?.available ? `${__VLS_ctx.resources.data.value.gpu.utilization}%` : '不可用');
__VLS_asFunctionalElement1(__VLS_intrinsics.progress, __VLS_intrinsics.progress)({
    value: (__VLS_ctx.resources.data.value?.gpu?.utilization || 0),
    max: "100",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
(__VLS_ctx.compact(__VLS_ctx.resources.data.value?.gpu?.used_mb || 0));
(__VLS_ctx.compact(__VLS_ctx.resources.data.value?.gpu?.total_mb || 0));
__VLS_asFunctionalElement1(__VLS_intrinsics.progress, __VLS_intrinsics.progress)({
    value: (__VLS_ctx.resources.data.value?.gpu?.used_mb || 0),
    max: (__VLS_ctx.resources.data.value?.gpu?.total_mb || 1),
});
__VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
(__VLS_ctx.resources.data.value?.cpu?.percent || 0);
__VLS_asFunctionalElement1(__VLS_intrinsics.progress, __VLS_intrinsics.progress)({
    value: (__VLS_ctx.resources.data.value?.cpu?.percent || 0),
    max: "100",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
(__VLS_ctx.resources.data.value?.cpu?.cores || 0);
__VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
(__VLS_ctx.resources.data.value?.memory?.percent || 0);
__VLS_asFunctionalElement1(__VLS_intrinsics.progress, __VLS_intrinsics.progress)({
    value: (__VLS_ctx.resources.data.value?.memory?.used_mb || 0),
    max: (__VLS_ctx.resources.data.value?.memory?.total_mb || 1),
});
__VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
(__VLS_ctx.compact(__VLS_ctx.resources.data.value?.memory?.used_mb || 0));
(__VLS_ctx.compact(__VLS_ctx.resources.data.value?.memory?.total_mb || 0));
if (__VLS_ctx.totals.calls) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "workload-layout" },
    });
    /** @type {__VLS_StyleScopedClasses['workload-layout']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "run-totals" },
    });
    /** @type {__VLS_StyleScopedClasses['run-totals']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (__VLS_ctx.totals.calls);
    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (__VLS_ctx.compact(__VLS_ctx.totals.input_tokens));
    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (__VLS_ctx.compact(__VLS_ctx.totals.output_tokens));
    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (__VLS_ctx.duration(__VLS_ctx.totals.latency_seconds));
    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "stage-load" },
    });
    /** @type {__VLS_StyleScopedClasses['stage-load']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "stage-title" },
    });
    /** @type {__VLS_StyleScopedClasses['stage-title']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    for (const [item] of __VLS_vFor((__VLS_ctx.stages))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            key: (`${item.stage}:${item.workload}`),
            ...{ class: "stage-row" },
        });
        /** @type {__VLS_StyleScopedClasses['stage-row']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (__VLS_ctx.stageName(item.stage));
        __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
        (item.calls);
        (Number(item.compute_profile?.input_output_ratio || 0).toFixed(1));
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "bar" },
        });
        /** @type {__VLS_StyleScopedClasses['bar']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.i, __VLS_intrinsics.i)({
            ...{ style: ({ width: `${__VLS_ctx.percent(item.latency_seconds, __VLS_ctx.maxStageLatency)}%` }) },
        });
        __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
        (__VLS_ctx.duration(item.latency_seconds));
        // @ts-ignore
        [resources, resources, resources, resources, resources, resources, resources, resources, resources, resources, resources, resources, resources, resources, resources, resources, health, health, health, health, health, health, health, restartServices, runningTaskId, runningTaskId, runningTaskId, restarting, restarting, restartMessage, restartMessage, queues, queues, queues, queues, performance, performance, performance, compact, compact, compact, compact, compact, compact, totals, totals, totals, totals, totals, duration, duration, stages, stageName, percent, maxStageLatency,];
    }
}
else {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "performance-empty" },
    });
    /** @type {__VLS_StyleScopedClasses['performance-empty']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
}
// @ts-ignore
[];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
