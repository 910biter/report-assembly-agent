<script setup lang="ts">
import { computed, ref } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { api } from "@/api/http";

const health = useQuery({ queryKey: ["health"], queryFn: () => api<any>("/api/health"), refetchInterval: 30_000 });
const queues = useQuery({ queryKey: ["queues"], queryFn: () => api<any>("/api/system/queues"), refetchInterval: 10_000 });
const resources = useQuery({ queryKey: ["resources"], queryFn: () => api<any>("/api/system/resources"), refetchInterval: 15_000 });
const performance = useQuery({ queryKey: ["performance-summary"], queryFn: () => api<any>("/api/system/performance-summary"), refetchInterval: 30_000 });
const restarting = ref(false);
const restartMessage = ref("");

const totals = computed(() => performance.data.value?.profile?.totals || {});
const stages = computed(() => [...(performance.data.value?.profile?.by_stage_workload || [])]
  .filter((item: any) => Number(item.calls || 0) > 0)
  .sort((a: any, b: any) => Number(b.latency_seconds || 0) - Number(a.latency_seconds || 0))
  .slice(0, 7));
const maxStageLatency = computed(() => Math.max(...stages.value.map((item: any) => Number(item.latency_seconds || 0)), 1));

const stageNames: Record<string, string> = {
  material_analysis: "材料理解", planning: "分析规划", evidence: "证据提取", conflict: "冲突核验",
  analysis: "综合分析", final_planning: "报告规划", writing: "报告成文", knowledge: "知识沉淀", qa: "质量检查",
};
function stageName(stage: string) { return stageNames[stage] || stage || "其他"; }
function compact(value: number) {
  const number = Number(value || 0);
  if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(2)}M`;
  if (number >= 1_000) return `${(number / 1_000).toFixed(1)}K`;
  return String(Math.round(number));
}
function duration(seconds: number) {
  const value = Number(seconds || 0);
  if (value >= 3600) return `${(value / 3600).toFixed(1)} 小时`;
  if (value >= 60) return `${Math.round(value / 60)} 分钟`;
  return `${value.toFixed(1)} 秒`;
}
function percent(value: number, total: number) { return Math.min(100, Math.max(0, total ? Number(value || 0) / total * 100 : 0)); }
const runningTaskId = computed(() => String(queues.data.value?.tasks?.running_task_id || ""));

async function restartServices() {
  if (runningTaskId.value || restarting.value) return;
  if (!confirm("确认重启报告整编服务？页面会短暂断开，并在服务恢复后自动刷新。")) return;
  restarting.value = true;
  restartMessage.value = "正在提交重启请求…";
  try {
    await api("/api/system/restart", { method: "POST" });
    restartMessage.value = "服务正在重启，等待恢复…";
    await new Promise(resolve => setTimeout(resolve, 2500));
    for (let attempt = 0; attempt < 30; attempt += 1) {
      try {
        const response = await fetch("/api/health", { cache: "no-store" });
        if (response.ok) { location.reload(); return; }
      } catch { /* The expected downtime while the process is replaced. */ }
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
    restartMessage.value = "服务恢复超时，请检查重启日志。";
  } catch (error: any) {
    restartMessage.value = error?.payload?.error === "TASK_RUNNING"
      ? `任务 ${error.payload.running_task_id} 正在运行，暂不能重启。`
      : (error.message || "重启请求失败");
  } finally {
    restarting.value = false;
  }
}
</script>

<template>
  <div class="page-stack settings-page">
    <header class="page-header"><div><h1>系统设置</h1><p>查看服务状态、任务调度与真实运行负载。</p></div><span class="updated-at">资源采样 {{ resources.data.value?.collected_at || '等待中' }}</span></header>

    <section class="settings-grid">
      <div class="surface section-block"><div class="section-head"><div><h2>模型服务</h2><p class="muted">核心推理服务连通状态</p></div><div class="service-actions"><span class="badge" :class="health.data.value?.error?'danger':'success'">{{health.data.value?.error?'连接异常':'服务可用'}}</span><button class="btn restart-button" :disabled="Boolean(runningTaskId)||restarting" @click="restartServices">{{restarting?'正在重启…':'重启服务'}}</button></div></div><dl><dt>网关地址</dt><dd class="mono">{{health.data.value?.gateway_url||'—'}}</dd><dt>服务版本</dt><dd>{{health.data.value?.version||'—'}}</dd><dt>运行目录</dt><dd class="mono">{{health.data.value?.runtime_root||'—'}}</dd></dl><div v-if="runningTaskId" class="restart-note">任务 <span class="mono">{{runningTaskId}}</span> 正在运行，完成后才可重启。</div><div v-else-if="restartMessage" class="restart-note">{{restartMessage}}</div><div v-if="health.data.value?.error" class="notice warning">{{health.data.value.error}}</div></div>
      <div class="surface section-block"><div class="section-head"><div><h2>任务队列</h2><p class="muted">前台任务优先，后台工作按队列调度</p></div></div><dl><dt>当前任务</dt><dd class="mono">{{queues.data.value?.tasks?.running_task_id||'空闲'}}</dd><dt>等待任务</dt><dd>{{queues.data.value?.tasks?.queued_task_ids?.length||0}}</dd><dt>LLM 队列</dt><dd>{{queues.data.value?.llm?.queued||queues.data.value?.llm?.queue_size||0}}</dd></dl></div>
    </section>

    <section class="surface performance-panel">
      <div class="section-head performance-head"><div><h2>性能概览</h2><p class="muted">实时资源状态与最近一次完整任务的推理负载</p></div><div v-if="performance.data.value?.task" class="task-context"><span>数据来源</span><b>{{ performance.data.value.task.theme }}</b><small class="mono">{{ performance.data.value.task.task_id }}</small></div></div>

      <div class="resource-grid">
        <article><div><span>GPU 利用率</span><b>{{resources.data.value?.gpu?.available ? `${resources.data.value.gpu.utilization}%` : '不可用'}}</b></div><progress :value="resources.data.value?.gpu?.utilization||0" max="100"></progress><small>当前加速器计算负载</small></article>
        <article><div><span>显存</span><b>{{compact(resources.data.value?.gpu?.used_mb||0)}} / {{compact(resources.data.value?.gpu?.total_mb||0)}} MB</b></div><progress :value="resources.data.value?.gpu?.used_mb||0" :max="resources.data.value?.gpu?.total_mb||1"></progress><small>模型与 KV Cache 占用</small></article>
        <article><div><span>CPU 利用率</span><b>{{resources.data.value?.cpu?.percent||0}}%</b></div><progress :value="resources.data.value?.cpu?.percent||0" max="100"></progress><small>{{resources.data.value?.cpu?.cores||0}} 个逻辑核心</small></article>
        <article><div><span>内存</span><b>{{resources.data.value?.memory?.percent||0}}%</b></div><progress :value="resources.data.value?.memory?.used_mb||0" :max="resources.data.value?.memory?.total_mb||1"></progress><small>{{compact(resources.data.value?.memory?.used_mb||0)}} / {{compact(resources.data.value?.memory?.total_mb||0)}} MB</small></article>
      </div>

      <div v-if="totals.calls" class="workload-layout">
        <div class="run-totals"><article><span>模型调用</span><b>{{totals.calls}}</b><small>次</small></article><article><span>输入 Token</span><b>{{compact(totals.input_tokens)}}</b><small>Prefill 负载</small></article><article><span>输出 Token</span><b>{{compact(totals.output_tokens)}}</b><small>Decode 负载</small></article><article><span>推理总耗时</span><b>{{duration(totals.latency_seconds)}}</b><small>模型调用累计</small></article></div>
        <div class="stage-load"><div class="stage-title"><b>阶段耗时分布</b><span>按累计模型耗时排序</span></div><div v-for="item in stages" :key="`${item.stage}:${item.workload}`" class="stage-row"><div><b>{{stageName(item.stage)}}</b><small>{{item.calls}} 次 · 输入/输出 {{Number(item.compute_profile?.input_output_ratio||0).toFixed(1)}}:1</small></div><div class="bar"><i :style="{width:`${percent(item.latency_seconds,maxStageLatency)}%`}"></i></div><strong>{{duration(item.latency_seconds)}}</strong></div></div>
      </div>
      <div v-else class="performance-empty"><b>暂无完整任务性能数据</b><span>任务完成后，这里将展示真实 Token 与阶段耗时分布。</span></div>
    </section>
  </div>
</template>

<style scoped>
.settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}dl{display:grid;grid-template-columns:110px 1fr;gap:12px;margin:0}dt{color:var(--color-muted)}dd{margin:0;overflow-wrap:anywhere}.updated-at{color:var(--color-faint);font-size:11px}.service-actions{display:flex;align-items:center;gap:8px}.restart-button{padding:5px 9px;font-size:11px}.restart-button:disabled{cursor:not-allowed;opacity:.5}.restart-note{margin-top:14px;padding:9px 11px;color:var(--color-muted);background:var(--color-surface-soft);border-left:2px solid var(--color-border-strong);font-size:11px}.performance-panel{padding:24px}.performance-head{align-items:flex-start}.task-context{max-width:360px;text-align:right}.task-context span,.task-context b,.task-context small{display:block}.task-context span,.task-context small{color:var(--color-faint);font-size:10px}.task-context b{margin:2px 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}.resource-grid{display:grid;grid-template-columns:repeat(4,1fr);margin-top:20px;border-top:1px solid var(--color-border);border-left:1px solid var(--color-border)}.resource-grid article{padding:16px 18px;background:#fafbfc;border-right:1px solid var(--color-border);border-bottom:1px solid var(--color-border)}.resource-grid article>div{display:flex;align-items:baseline;justify-content:space-between;gap:8px}.resource-grid span,.run-totals span{color:var(--color-muted);font-size:11px}.resource-grid b{font-size:15px}.resource-grid progress{width:100%;height:5px;margin:13px 0 7px;accent-color:var(--color-primary)}.resource-grid small,.run-totals small{color:var(--color-faint);font-size:10px}.workload-layout{display:grid;grid-template-columns:290px 1fr;gap:28px;margin-top:28px;padding-top:24px;border-top:1px solid var(--color-border)}.run-totals{display:grid;grid-template-columns:1fr 1fr;border-top:1px solid var(--color-border);border-left:1px solid var(--color-border)}.run-totals article{padding:16px;background:#fff;border-right:1px solid var(--color-border);border-bottom:1px solid var(--color-border)}.run-totals span,.run-totals b,.run-totals small{display:block}.run-totals b{margin:5px 0 2px;font-family:Georgia,serif;font-size:21px;font-weight:500}.stage-title{display:flex;align-items:center;justify-content:space-between;margin-bottom:9px}.stage-title span{color:var(--color-faint);font-size:10px}.stage-row{display:grid;grid-template-columns:170px minmax(100px,1fr) 76px;align-items:center;gap:14px;padding:8px 0;border-top:1px solid var(--color-border)}.stage-row>div:first-child b,.stage-row>div:first-child small{display:block}.stage-row>div:first-child b{font-size:12px}.stage-row>div:first-child small{margin-top:2px;color:var(--color-faint);font-size:9px}.stage-row>strong{text-align:right;font-size:11px}.bar{height:6px;background:#edf0f3}.bar i{display:block;height:100%;min-width:2px;background:#587fa8}.performance-empty{display:flex;flex-direction:column;align-items:center;padding:46px;color:var(--color-muted)}.performance-empty span{margin-top:5px;font-size:12px}@media(max-width:1000px){.resource-grid{grid-template-columns:1fr 1fr}.workload-layout{grid-template-columns:1fr}}@media(max-width:700px){.settings-grid,.resource-grid{grid-template-columns:1fr}.task-context{display:none}.stage-row{grid-template-columns:130px 1fr 68px}}
</style>
