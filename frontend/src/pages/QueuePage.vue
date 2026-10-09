<script setup lang="ts">
import { computed } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { RouterLink } from "vue-router";
import { api } from "@/api/http";
import { authMode } from "@/lib/auth";
import UiPageHeader from "@/components/ui/UiPageHeader.vue";

type QueueJob = { task_id: string; status: "running" | "queued"; ahead: number };
type QueueSnapshot = { running: boolean; waiting: number; own_tasks: QueueJob[] };

const queue = useQuery({
  queryKey: ["personal-queue"],
  queryFn: () => api<QueueSnapshot>("/api/queue/me"),
  enabled: computed(() => authMode.value === "on"),
  refetchInterval: 8_000,
});
</script>

<template>
  <div class="page-stack queue-page">
    <UiPageHeader title="运行队列" />
    <p v-if="authMode === 'off'" class="queue-message">当前未启用多用户队列视图。</p>
    <p v-else-if="queue.isPending.value" class="queue-message">正在读取队列…</p>
    <p v-else-if="queue.isError.value" class="queue-message">队列暂时不可用，请稍后刷新。</p>
    <template v-else-if="queue.data.value">
      <section class="queue-overview" aria-label="队列概况">
        <div><span class="queue-label">执行状态</span><strong>{{ queue.data.value.running ? "正在处理" : "空闲" }}</strong></div>
        <div><span class="queue-label">等待任务</span><strong>{{ queue.data.value.waiting }}</strong></div>
        <div><span class="queue-label">我的任务</span><strong>{{ queue.data.value.own_tasks.length }}</strong></div>
      </section>
      <section class="queue-section">
        <h2>我的排队任务</h2>
        <p v-if="!queue.data.value.own_tasks.length" class="queue-message">当前没有运行中或等待中的任务。</p>
        <ul v-else class="queue-list">
          <li v-for="job in queue.data.value.own_tasks" :key="job.task_id">
            <RouterLink :to="`/tasks/${job.task_id}`">
              <span class="queue-job-id">{{ job.task_id.slice(0, 12) }}</span>
              <span class="queue-position">{{ job.status === "running" ? "运行中" : `前方 ${job.ahead} 个任务` }}</span>
              <span aria-hidden="true">↗</span>
            </RouterLink>
          </li>
        </ul>
      </section>
      <p class="queue-footnote">队列按当前调度顺序更新。其他用户的任务内容不会在这里显示。</p>
    </template>
  </div>
</template>

<style scoped>
.queue-page { max-width: 920px; }
.queue-overview { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border: 1px solid var(--border); border-radius: 8px; background: var(--card); }
.queue-overview > div { display: grid; gap: 12px; min-width: 0; padding: 22px 24px; }
.queue-overview > div + div { border-left: 1px solid var(--border); }
.queue-label { color: var(--muted-foreground); font-size: 12px; }
.queue-overview strong { color: var(--foreground); font-size: 23px; font-weight: 600; }
.queue-section h2 { margin: 0 0 12px; color: var(--foreground); font-size: 15px; font-weight: 600; }
.queue-list { margin: 0; padding: 0; list-style: none; border-top: 1px solid var(--border); }
.queue-list li { border-bottom: 1px solid var(--border); }
.queue-list a { display: flex; align-items: center; gap: 16px; min-height: 50px; padding: 8px 12px; color: var(--foreground); text-decoration: none; }
.queue-list a:hover { background: var(--surface-hover); }
.queue-job-id { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-variant-numeric: tabular-nums; }
.queue-position { color: var(--muted-foreground); font-size: 12px; }
.queue-message, .queue-footnote { margin: 0; color: var(--muted-foreground); font-size: 13px; }
@media (max-width: 600px) {
  .queue-overview > div { gap: 8px; padding: 16px 12px; }
  .queue-overview strong { font-size: 18px; }
}
</style>
