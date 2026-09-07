<script setup lang="ts">
import { computed, ref } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { api } from "@/api/http";
import UiPageHeader from "@/components/ui/UiPageHeader.vue";
import UiPanel from "@/components/ui/UiPanel.vue";
import UiSectionHeader from "@/components/ui/UiSectionHeader.vue";
import UiButton from "@/components/ui/UiButton.vue";
import { useUiStore } from "@/stores/ui";

const ui = useUiStore();

const health = useQuery({
  queryKey: ["health"],
  queryFn: () => api<any>("/api/health"),
  refetchInterval: 30_000,
});
const queues = useQuery({
  queryKey: ["queues"],
  queryFn: () => api<any>("/api/system/queues"),
  refetchInterval: 10_000,
});
const resources = useQuery({
  queryKey: ["resources"],
  queryFn: () => api<any>("/api/system/resources"),
  refetchInterval: 15_000,
});
const performance = useQuery({
  queryKey: ["performance-summary"],
  queryFn: () => api<any>("/api/system/performance-summary"),
  refetchInterval: 30_000,
});
const restarting = ref(false);
const restartMessage = ref("");

const totals = computed(() => performance.data.value?.profile?.totals || {});
const stages = computed(() =>
  [...(performance.data.value?.profile?.by_stage_workload || [])]
    .filter((item: any) => Number(item.calls || 0) > 0)
    .sort(
      (a: any, b: any) =>
        Number(b.latency_seconds || 0) - Number(a.latency_seconds || 0),
    )
    .slice(0, 7),
);
const maxStageLatency = computed(() =>
  Math.max(
    ...stages.value.map((item: any) => Number(item.latency_seconds || 0)),
    1,
  ),
);

const stageNames: Record<string, string> = {
  material_analysis: "材料理解",
  planning: "分析规划",
  evidence: "证据提取",
  conflict: "冲突核验",
  analysis: "综合分析",
  final_planning: "报告规划",
  writing: "报告成文",
  graph_build: "知识图谱",
  qa: "质量检查",
};
function stageName(stage: string) {
  return stageNames[stage] || stage || "其他";
}
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
function percent(value: number, total: number) {
  return Math.min(
    100,
    Math.max(0, total ? (Number(value || 0) / total) * 100 : 0),
  );
}
const runningTaskId = computed(() =>
  String(queues.data.value?.tasks?.running_task_id || ""),
);

async function restartServices() {
  if (runningTaskId.value || restarting.value) return;
  if (
    !confirm("确认重启报告整编服务？页面会短暂断开，并在服务恢复后自动刷新。")
  )
    return;
  restarting.value = true;
  restartMessage.value = "正在提交重启请求…";
  try {
    await api("/api/system/restart", { method: "POST" });
    restartMessage.value = "服务正在重启，等待恢复…";
    await new Promise((resolve) => setTimeout(resolve, 2500));
    for (let attempt = 0; attempt < 30; attempt += 1) {
      try {
        const response = await fetch("/api/health", { cache: "no-store" });
        if (response.ok) {
          location.reload();
          return;
        }
      } catch {
        /* The expected downtime while the process is replaced. */
      }
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
    restartMessage.value = "服务恢复超时，请检查重启日志。";
  } catch (error: any) {
    restartMessage.value =
      error?.payload?.error === "TASK_RUNNING"
        ? `任务 ${error.payload.running_task_id} 正在运行，暂不能重启。`
        : error.message || "重启请求失败";
  } finally {
    restarting.value = false;
  }
}
</script>

<template>
  <div class="page-stack settings-page">
    <UiPageHeader title="系统设置" />

    <section class="settings-grid">
      <UiPanel class="section-block">
        <UiSectionHeader title="模型服务" description="核心推理服务连通状态">
          <template #actions>
            <div class="service-actions">
              <span
                class="badge"
                :class="health.data.value?.error ? 'danger' : 'success'"
                >{{ health.data.value?.error ? "连接异常" : "服务可用" }}</span
              ><UiButton
                variant="outline"
                size="sm"
                class="restart-button"
                :disabled="Boolean(runningTaskId) || restarting"
                @click="restartServices"
              >
                {{ restarting ? "正在重启…" : "重启服务" }}
              </UiButton>
            </div>
          </template>
        </UiSectionHeader>
        <dl>
          <dt>网关地址</dt>
          <dd class="mono">{{ health.data.value?.gateway_url || "—" }}</dd>
          <dt>服务版本</dt>
          <dd>{{ health.data.value?.version || "—" }}</dd>
          <dt>运行目录</dt>
          <dd class="mono">{{ health.data.value?.runtime_root || "—" }}</dd>
        </dl>
        <div v-if="runningTaskId" class="restart-note">
          任务
          <span class="mono">{{ runningTaskId }}</span>
          正在运行，完成后才可重启。
        </div>
        <div v-else-if="restartMessage" class="restart-note">
          {{ restartMessage }}
        </div>
        <div v-if="health.data.value?.error" class="notice warning">
          {{ health.data.value.error }}
        </div>
      </UiPanel>
      <UiPanel class="section-block">
        <UiSectionHeader
          title="任务队列"
          description="前台任务优先，后台工作按队列调度"
        />
        <dl>
          <dt>当前任务</dt>
          <dd class="mono">
            {{ queues.data.value?.tasks?.running_task_id || "空闲" }}
          </dd>
          <dt>等待任务</dt>
          <dd>{{ queues.data.value?.tasks?.queued_task_ids?.length || 0 }}</dd>
          <dt>LLM 队列</dt>
          <dd>
            {{
              queues.data.value?.llm?.queued ||
              queues.data.value?.llm?.queue_size ||
              0
            }}
          </dd>
        </dl>
      </UiPanel>
    </section>

    <UiPanel class="section-block appearance-panel">
      <UiSectionHeader
        title="界面外观"
        description="明暗模式独立于强调色；切换后会在此设备上保留。"
      />
      <div class="appearance-controls">
        <div class="appearance-group">
          <span>显示模式</span>
          <div class="appearance-options" role="group" aria-label="显示模式">
            <button
              type="button"
              :class="{ active: ui.theme === 'dark' }"
              @click="ui.setTheme('dark')"
            >暗色</button>
            <button
              type="button"
              :class="{ active: ui.theme === 'light' }"
              @click="ui.setTheme('light')"
            >亮色</button>
          </div>
        </div>
        <div class="appearance-group">
          <span>强调色</span>
          <div class="palette-options" role="group" aria-label="强调色">
            <button
              type="button"
              :class="{ active: ui.palette === 'violet' }"
              @click="ui.setPalette('violet')"
            ><i class="violet-swatch"></i><b>深紫 · 智能工作区</b><small>默认</small></button>
            <button
              type="button"
              :class="{ active: ui.palette === 'steel' }"
              @click="ui.setPalette('steel')"
            ><i class="steel-swatch"></i><b>冷灰 · 钢蓝</b><small>推荐</small></button>
            <button
              type="button"
              :class="{ active: ui.palette === 'graphite' }"
              @click="ui.setPalette('graphite')"
            ><i class="graphite-swatch"></i><b>冷灰 · 石墨</b><small>低干扰</small></button>
          </div>
        </div>
      </div>
    </UiPanel>

    <UiPanel class="performance-panel">
      <UiSectionHeader
        class="performance-head"
        title="性能概览"
        description="实时资源状态与最近一次完整任务的推理负载"
      >
        <template #actions>
          <div v-if="performance.data.value?.task" class="task-context">
            <span>数据来源</span><b>{{ performance.data.value.task.theme }}</b
            ><small class="mono">{{
              performance.data.value.task.task_id
            }}</small>
          </div>
        </template>
      </UiSectionHeader>

      <div class="resource-grid">
        <article>
          <div>
            <span>GPU 利用率</span
            ><b>{{
              resources.data.value?.gpu?.available
                ? `${resources.data.value.gpu.utilization}%`
                : "不可用"
            }}</b>
          </div>
          <progress
            :value="resources.data.value?.gpu?.utilization || 0"
            max="100"
          ></progress
          ><small>当前加速器计算负载</small>
        </article>
        <article>
          <div>
            <span>显存</span
            ><b
              >{{ compact(resources.data.value?.gpu?.used_mb || 0) }} /
              {{ compact(resources.data.value?.gpu?.total_mb || 0) }} MB</b
            >
          </div>
          <progress
            :value="resources.data.value?.gpu?.used_mb || 0"
            :max="resources.data.value?.gpu?.total_mb || 1"
          ></progress
          ><small>模型与 KV Cache 占用</small>
        </article>
        <article>
          <div>
            <span>CPU 利用率</span
            ><b>{{ resources.data.value?.cpu?.percent || 0 }}%</b>
          </div>
          <progress
            :value="resources.data.value?.cpu?.percent || 0"
            max="100"
          ></progress
          ><small>{{ resources.data.value?.cpu?.cores || 0 }} 个逻辑核心</small>
        </article>
        <article>
          <div>
            <span>内存</span
            ><b>{{ resources.data.value?.memory?.percent || 0 }}%</b>
          </div>
          <progress
            :value="resources.data.value?.memory?.used_mb || 0"
            :max="resources.data.value?.memory?.total_mb || 1"
          ></progress
          ><small
            >{{ compact(resources.data.value?.memory?.used_mb || 0) }} /
            {{ compact(resources.data.value?.memory?.total_mb || 0) }} MB</small
          >
        </article>
      </div>

      <div v-if="totals.calls" class="workload-layout">
        <div class="run-totals">
          <article>
            <span>模型调用</span><b>{{ totals.calls }}</b
            ><small>次</small>
          </article>
          <article>
            <span>输入 Token</span><b>{{ compact(totals.input_tokens) }}</b
            ><small>Prefill 负载</small>
          </article>
          <article>
            <span>输出 Token</span><b>{{ compact(totals.output_tokens) }}</b
            ><small>Decode 负载</small>
          </article>
          <article>
            <span>推理总耗时</span><b>{{ duration(totals.latency_seconds) }}</b
            ><small>模型调用累计</small>
          </article>
        </div>
        <div class="stage-load">
          <div class="stage-title">
            <b>阶段耗时分布</b><span>按累计模型耗时排序</span>
          </div>
          <div
            v-for="item in stages"
            :key="`${item.stage}:${item.workload}`"
            class="stage-row"
          >
            <div>
              <b>{{ stageName(item.stage) }}</b
              ><small
                >{{ item.calls }} 次 · 输入/输出
                {{
                  Number(item.compute_profile?.input_output_ratio || 0).toFixed(
                    1,
                  )
                }}:1</small
              >
            </div>
            <div class="bar">
              <i
                :style="{
                  width: `${percent(item.latency_seconds, maxStageLatency)}%`,
                }"
              ></i>
            </div>
            <strong>{{ duration(item.latency_seconds) }}</strong>
          </div>
        </div>
      </div>
      <div v-else class="performance-empty">
        <b>暂无完整任务性能数据</b
        ><span>任务完成后，这里将展示真实 Token 与阶段耗时分布。</span>
      </div>
    </UiPanel>
  </div>
</template>

<style scoped>
.settings-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
}
dl {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 12px;
  margin: 0;
}
dt {
  color: var(--color-muted);
}
dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.updated-at {
  padding: 5px 9px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--color-faint);
  background: var(--card);
  font-size: 11px;
}
.page-kicker {
  display: block;
  margin-bottom: 4px;
  color: var(--primary);
  font-size: 10px;
  font-weight: 750;
  letter-spacing: 0.13em;
}
.service-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.restart-button {
  padding: 5px 9px;
  font-size: 11px;
}
.restart-button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.restart-note {
  margin-top: 14px;
  padding: 9px 11px;
  color: var(--color-muted);
  background: var(--color-surface-soft);
  border-left: 2px solid var(--color-border-strong);
  font-size: 11px;
}
.settings-grid > .surface {
  box-shadow: var(--shadow-panel);
}
.appearance-panel {
  padding: 22px 24px;
}
.appearance-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 28px;
  margin-top: 20px;
}
.appearance-group {
  display: grid;
  gap: 8px;
}
.appearance-group > span {
  color: var(--muted-foreground);
  font-size: 12px;
}
.appearance-options,
.palette-options {
  display: flex;
  gap: 6px;
}
.appearance-options {
  padding: 3px;
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--muted);
}
.appearance-options button,
.palette-options button {
  min-height: 34px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--muted-foreground);
  font-size: 12px;
  transition: background var(--motion-fast), border-color var(--motion-fast), color var(--motion-fast);
}
.appearance-options button {
  min-width: 66px;
  padding: 0 12px;
}
.appearance-options button:hover,
.palette-options button:hover {
  color: var(--foreground);
  background: var(--surface-hover);
}
.appearance-options button.active {
  color: var(--foreground);
  background: var(--surface-raised);
  border-color: var(--border-strong);
}
.palette-options button {
  display: grid;
  grid-template-columns: 18px auto;
  align-items: center;
  column-gap: 8px;
  min-width: 164px;
  padding: 7px 10px;
  text-align: left;
  border-color: var(--border);
  background: var(--surface-raised);
}
.palette-options button.active {
  color: var(--foreground);
  border-color: var(--primary);
  background: var(--primary-soft);
}
.palette-options b,
.palette-options small {
  display: block;
  grid-column: 2;
}
.palette-options b {
  font-size: 12px;
  font-weight: 600;
}
.palette-options small {
  color: var(--subtle-foreground);
  font-size: 10px;
}
.palette-options i {
  grid-row: span 2;
  width: 16px;
  height: 16px;
  border: 1px solid var(--border-strong);
  border-radius: 50%;
}
.violet-swatch { background: linear-gradient(135deg, #13111e 50%, #8064ff 50%); }
.steel-swatch { background: linear-gradient(135deg, #151a20 50%, #4d7f9f 50%); }
.graphite-swatch { background: linear-gradient(135deg, #151a20 50%, #8896a4 50%); }
.performance-panel {
  padding: 26px;
  overflow: hidden;
  background: var(--card);
}
.performance-head {
  align-items: flex-start;
}
.task-context {
  max-width: 360px;
  text-align: right;
}
.task-context span,
.task-context b,
.task-context small {
  display: block;
}
.task-context span,
.task-context small {
  color: var(--color-faint);
  font-size: 10px;
}
.task-context b {
  margin: 2px 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}
.resource-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  margin-top: 22px;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-panel);
}
.resource-grid article {
  padding: 17px 18px;
  background: color-mix(in srgb, var(--card) 84%, transparent);
  border-right: 1px solid var(--color-border);
}
.resource-grid article:last-child {
  border-right: 0;
}
.resource-grid article > div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.resource-grid span,
.run-totals span {
  color: var(--color-muted);
  font-size: 11px;
}
.resource-grid b {
  font-size: 15px;
}
.resource-grid progress {
  width: 100%;
  height: 5px;
  margin: 13px 0 7px;
  accent-color: var(--color-primary);
}
.resource-grid small,
.run-totals small {
  color: var(--color-faint);
  font-size: 10px;
}
.workload-layout {
  display: grid;
  grid-template-columns: 290px 1fr;
  gap: 28px;
  margin-top: 28px;
  padding-top: 24px;
  border-top: 1px solid var(--color-border);
}
.run-totals {
  display: grid;
  grid-template-columns: 1fr 1fr;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
}
.run-totals article {
  padding: 16px;
  background: color-mix(in srgb, var(--card) 88%, transparent);
  border-right: 1px solid var(--color-border);
  border-bottom: 1px solid var(--color-border);
}
.run-totals article:nth-child(2n) {
  border-right: 0;
}
.run-totals article:nth-last-child(-n + 2) {
  border-bottom: 0;
}
.run-totals span,
.run-totals b,
.run-totals small {
  display: block;
}
.run-totals b {
  margin: 5px 0 2px;
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 600;
}
.stage-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 9px;
}
.stage-title span {
  color: var(--color-faint);
  font-size: 10px;
}
.stage-row {
  display: grid;
  grid-template-columns: 170px minmax(100px, 1fr) 76px;
  align-items: center;
  gap: 14px;
  padding: 10px 0;
  border-top: 1px solid var(--color-border);
}
.stage-row > div:first-child b,
.stage-row > div:first-child small {
  display: block;
}
.stage-row > div:first-child b {
  font-size: 12px;
}
.stage-row > div:first-child small {
  margin-top: 2px;
  color: var(--color-faint);
  font-size: 9px;
}
.stage-row > strong {
  text-align: right;
  font-size: 11px;
}
.bar {
  height: 6px;
  overflow: hidden;
  border-radius: 99px;
  background: var(--muted);
}
.bar i {
  display: block;
  height: 100%;
  min-width: 2px;
  border-radius: inherit;
  background: var(--primary);
}
.performance-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 46px;
  color: var(--color-muted);
}
.performance-empty span {
  margin-top: 5px;
  font-size: 12px;
}
@media (max-width: 1000px) {
  .resource-grid {
    grid-template-columns: 1fr 1fr;
  }
  .resource-grid article:nth-child(2) {
    border-right: 0;
  }
  .resource-grid article:nth-child(-n + 2) {
    border-bottom: 1px solid var(--color-border);
  }
  .workload-layout {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 620px) {
  .settings-grid {
    grid-template-columns: 1fr;
  }
  .appearance-controls,
  .palette-options {
    align-items: stretch;
    flex-direction: column;
  }
  .palette-options button {
    width: 100%;
  }
}
@media (max-width: 700px) {
  .settings-grid,
  .resource-grid {
    grid-template-columns: 1fr;
  }
  .resource-grid article,
  .resource-grid article:nth-child(2) {
    border-right: 0;
    border-bottom: 1px solid var(--color-border);
  }
  .resource-grid article:last-child {
    border-bottom: 0;
  }
  .task-context,
  .updated-at {
    display: none;
  }
  .stage-row {
    grid-template-columns: 130px 1fr 68px;
  }
}
.performance-panel {
  background: var(--card);
}
</style>
