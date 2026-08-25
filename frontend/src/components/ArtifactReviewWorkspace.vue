<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { api } from "@/api/http";
import ReviewCopilot from "@/components/ReviewCopilot.vue";

const props = defineProps<{
  taskId: string;
  reportId?: number;
  runRevision?: number;
}>();
const qc = useQueryClient();
const artifactType = ref("task_brief");
const search = ref("");
const offset = ref(0);
const selected = ref<any>(null);
const pageSize = 40;
const workspace = useQuery({
  queryKey: ["review-workspace", props.taskId, artifactType, search, offset],
  queryFn: () =>
    api<any>(
      `/api/tasks/${props.taskId}/review-workspace?artifact_type=${encodeURIComponent(artifactType.value)}&q=${encodeURIComponent(search.value)}&offset=${offset.value}&limit=${pageSize}`,
    ),
  staleTime: 5000,
});
const notifications = useQuery({
  queryKey: ["interaction-notifications", props.taskId],
  queryFn: () =>
    api<any[]>(`/api/interaction-notifications?task_id=${props.taskId}`),
  refetchInterval: 5000,
});
const items = computed(() => workspace.data.value?.items || []);
watch(
  items,
  (value) => {
    if (!value.length) selected.value = null;
    else if (
      !selected.value ||
      !value.some((item: any) => item.object_id === selected.value.object_id)
    )
      selected.value = value[0];
  },
  { immediate: true },
);
watch([artifactType, search], () => {
  offset.value = 0;
  selected.value = null;
});

async function readNotice(item: any) {
  if (item.status === "unread") {
    await api(`/api/interaction-notifications/${item.id}/read`, {
      method: "PATCH",
    });
    await qc.invalidateQueries({
      queryKey: ["interaction-notifications", props.taskId],
    });
  }
  if (item.action_url) location.href = item.action_url;
}
function chooseType(value: string) {
  artifactType.value = value;
}
function pretty(value: any) {
  if (value == null || value === "") return "—";
  if (Array.isArray(value))
    return (
      value
        .map((item: any) =>
          typeof item === "object"
            ? item.title || item.content || JSON.stringify(item)
            : item,
        )
        .join("、") || "—"
    );
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}
</script>

<template>
  <section class="collaboration-shell">
    <header class="collaboration-head">
      <div>
        <h2>协作审阅</h2>
        <p>
          随时讨论阶段产物。系统继续后台运行，批准的语义修改会生成候选版本供你比较。
        </p>
      </div>
      <span class="badge">异步协作</span>
    </header>
    <div v-if="(notifications.data.value || []).length" class="notice-strip">
      <button
        v-for="item in (notifications.data.value || []).slice(0, 3)"
        :key="item.id"
        :class="{ unread: item.status === 'unread' }"
        @click="readNotice(item)"
      >
        <span></span>
        <div>
          <b>{{ item.title }}</b
          ><small>{{ item.message }}</small>
        </div>
        <strong>查看</strong>
      </button>
    </div>
    <div class="collaboration-grid">
      <aside class="artifact-groups">
        <button
          v-for="group in workspace.data.value?.groups || []"
          :key="group.artifact_type"
          :disabled="!group.available"
          :class="{ active: artifactType === group.artifact_type }"
          @click="chooseType(group.artifact_type)"
        >
          <span>{{ group.label }}</span
          ><b>{{ group.count }}</b>
        </button>
      </aside>
      <section class="artifact-browser">
        <div class="browser-tools">
          <input v-model="search" placeholder="搜索当前阶段产物" /><span
            >{{ workspace.data.value?.total || 0 }} 项</span
          >
        </div>
        <div v-if="items.length" class="artifact-list">
          <article
            v-for="item in items"
            :key="`${item.artifact_type}:${item.object_id}`"
            :class="{ active: selected?.object_id === item.object_id }"
          >
            <button type="button" @click="selected = item">
              <b>{{ item.title }}</b
              ><span>{{ item.summary || "打开查看详情" }}</span>
            </button>
            <dl v-if="selected?.object_id === item.object_id" class="inline-detail">
              <template v-for="(value, key) in item.current" :key="key">
                <dt>{{ key }}</dt>
                <dd>{{ pretty(value) }}</dd>
              </template>
            </dl>
          </article>
        </div>
        <div v-else class="empty">
          <div>
            <strong>该阶段尚无产物</strong>系统完成相应阶段后会自动出现在这里。
          </div>
        </div>
        <div v-if="(workspace.data.value?.total || 0) > pageSize" class="pager">
          <button
            class="btn"
            :disabled="offset === 0"
            @click="offset = Math.max(0, offset - pageSize)"
          >
            上一页</button
          ><span
            >{{ offset + 1 }}–{{
              Math.min(offset + pageSize, workspace.data.value.total)
            }}</span
          ><button
            class="btn"
            :disabled="offset + pageSize >= workspace.data.value.total"
            @click="offset += pageSize"
          >
            下一页
          </button>
        </div>
      </section>
      <aside class="artifact-review">
        <template v-if="selected">
          <div class="review-target">
            <small>讨论对象</small>
            <b>{{ selected.title }}</b>
          </div>
          <ReviewCopilot
            compact
            :task-id="taskId"
            :report-id="reportId"
            :artifact-type="selected.artifact_type"
            :artifact-version="
              selected.artifact_version || String(runRevision || 1)
            "
            :object-id="selected.object_id"
            :current="selected.current"
            @applied="
              qc.invalidateQueries({ queryKey: ['review-workspace', taskId] })
            "
          />
        </template>
        <div v-else class="empty">
          <div><strong>选择一个产物</strong>查看内容并与助手讨论。</div>
        </div>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.collaboration-shell {
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  height: min(780px, calc(100vh - 190px));
  min-height: 600px;
  overflow: hidden;
  background: #fff;
  border: 1px solid var(--color-border);
}
.collaboration-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 20px 24px;
  border-bottom: 1px solid var(--color-border);
}
.collaboration-head h2,
.collaboration-head p {
  margin: 0;
}
.collaboration-head p {
  margin-top: 4px;
  color: var(--color-muted);
}
.notice-strip {
  display: grid;
  border-bottom: 1px solid var(--color-border);
}
.notice-strip button {
  display: grid;
  grid-template-columns: 8px 1fr auto;
  align-items: center;
  gap: 10px;
  padding: 10px 18px;
  text-align: left;
  border: 0;
  border-bottom: 1px solid var(--color-border);
  background: #fafbfd;
}
.notice-strip button:last-child {
  border-bottom: 0;
}
.notice-strip button > span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-border-strong);
}
.notice-strip button.unread > span {
  background: var(--color-primary);
}
.notice-strip b,
.notice-strip small {
  display: block;
}
.notice-strip small {
  margin-top: 2px;
  color: var(--color-muted);
}
.notice-strip strong {
  color: var(--color-primary);
  font-size: 12px;
}
.collaboration-grid {
  display: grid;
  grid-template-columns: 180px minmax(280px, 380px) minmax(420px, 1fr);
  min-height: 0;
  overflow: hidden;
}
.artifact-groups,
.artifact-browser {
  border-right: 1px solid var(--color-border);
}
.artifact-groups {
  padding: 12px;
  overflow: auto;
}
.artifact-groups button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 10px 12px;
  text-align: left;
  border: 0;
  border-left: 2px solid transparent;
  background: transparent;
  color: var(--color-muted);
}
.artifact-groups button.active {
  border-left-color: var(--color-primary);
  background: var(--color-primary-soft);
  color: var(--color-primary);
}
.artifact-groups button:disabled {
  opacity: 0.4;
}
.artifact-groups b {
  font-size: 12px;
}
.artifact-browser {
  display: grid;
  grid-template-rows: auto 1fr auto;
  min-height: 0;
  min-width: 0;
}
.browser-tools {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  border-bottom: 1px solid var(--color-border);
}
.browser-tools input {
  min-width: 0;
  flex: 1;
}
.browser-tools span {
  color: var(--color-muted);
  font-size: 12px;
}
.artifact-list {
  overflow: auto;
}
.artifact-list button {
  display: grid;
  width: 100%;
  gap: 4px;
  padding: 13px 14px;
  text-align: left;
  border: 0;
  border-bottom: 1px solid var(--color-border);
  background: #fff;
}
.artifact-list article.active {
  background: #f1f6fb;
  box-shadow: inset 2px 0 var(--color-primary);
}
.artifact-list article.active button { background: transparent; }
.artifact-list span {
  display: -webkit-box;
  overflow: hidden;
  color: var(--color-muted);
  font-size: 12px;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.artifact-list .inline-detail {
  display: grid;
  grid-template-columns: 86px minmax(0, 1fr);
  width: 100%;
  margin: 0;
  padding: 8px 14px 12px;
  border-top: 1px solid #dbe5ef;
  font-size: 11px;
  cursor: text;
}
.inline-detail dt,
.inline-detail dd {
  margin: 0;
  padding: 5px 0;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.inline-detail dt { color: var(--color-faint); }
.inline-detail dd { color: var(--color-text); }
.artifact-review {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
  padding: 20px;
  overflow: hidden;
}
.review-target {
  margin-bottom: 14px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--color-border);
}
.review-target small,
.review-target b { display: block; }
.review-target small {
  color: var(--color-primary);
}
.review-target b { margin-top: 2px; }
.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 10px;
  border-top: 1px solid var(--color-border);
}
.pager span {
  color: var(--color-muted);
  font-size: 12px;
}
@media (max-width: 1100px) {
  .collaboration-shell { height: auto; max-height: none; overflow: visible; }
  .collaboration-grid {
    grid-template-columns: 160px 1fr;
    min-height: 640px;
    overflow: visible;
  }
  .artifact-review {
    grid-column: 1/-1;
    height: 560px;
    border-top: 1px solid var(--color-border);
  }
}
</style>
