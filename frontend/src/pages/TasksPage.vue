<script setup lang="ts">
import { computed, ref } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { RouterLink } from "vue-router";
import { api } from "@/api/http";
import type { TaskSummary } from "@/api/types";
import StatusBadge from "@/components/StatusBadge.vue";
import AppIcon from "@/components/AppIcon.vue";
import UiDataBrowser from "@/components/ui/UiDataBrowser.vue";
import UiButton from "@/components/ui/UiButton.vue";
import UiPageHeader from "@/components/ui/UiPageHeader.vue";

type TaskView =
  | "all"
  | "comparison"
  | "pending"
  | "active"
  | "paused"
  | "review"
  | "completed"
  | "failed";
const queryClient = useQueryClient();
const search = ref("");
const view = ref<TaskView>("all");
const dateRange = ref("all");
const variant = ref("");
const sortBy = ref("updated_desc");
const selectedId = ref<string | null>(null);
const tasks = useQuery({
  queryKey: ["tasks"],
  queryFn: () => api<TaskSummary[]>("/api/tasks"),
  refetchInterval: 8_000,
});
const templates = useQuery({
  queryKey: ["templates"],
  queryFn: () => api<any[]>("/api/style/variants"),
});
const templateNames = computed(
  () =>
    new Map(
      (templates.data.value || []).map((item) => [
        Number(item.id),
        item.name || item.label || `模板 ${item.id}`,
      ]),
    ),
);

function inView(task: TaskSummary, target: TaskView) {
  if (target === "all") return true;
  if (target === "comparison") return task.run_mode === "material_comparison";
  if (task.run_mode === "material_comparison") return false;
  if (target === "pending") return task.stage === "created";
  if (target === "paused") return task.stage === "paused";
  if (target === "review") return task.stage === "review";
  if (target === "completed") return task.stage === "done";
  if (target === "failed") return task.stage === "failed";
  return !["created", "review", "done", "failed", "paused"].includes(
    task.stage,
  );
}
function parseTimestamp(value?: string) {
  if (!value) return 0;
  const numeric = Number(value);
  if (Number.isFinite(numeric) && String(value).trim() !== "")
    return numeric < 1e12 ? numeric * 1000 : numeric;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}
function withinDate(task: TaskSummary) {
  if (dateRange.value === "all") return true;
  const value = parseTimestamp(task.updated_at || task.created_at);
  return (
    value > 0 && value >= Date.now() - Number(dateRange.value) * 86_400_000
  );
}
function timestamp(task: TaskSummary) {
  return parseTimestamp(task.updated_at || task.created_at);
}
const viewItems = computed(() => [
  {
    key: "all" as const,
    label: "全部",
    count: (tasks.data.value || []).length,
  },
  {
    key: "comparison" as const,
    label: "材料对比",
    count: (tasks.data.value || []).filter((item) => inView(item, "comparison"))
      .length,
  },
  {
    key: "pending" as const,
    label: "待运行",
    count: (tasks.data.value || []).filter((item) => inView(item, "pending"))
      .length,
  },
  {
    key: "active" as const,
    label: "进行中",
    count: (tasks.data.value || []).filter((item) => inView(item, "active"))
      .length,
  },
  {
    key: "paused" as const,
    label: "已暂停",
    count: (tasks.data.value || []).filter((item) => inView(item, "paused"))
      .length,
  },
  {
    key: "review" as const,
    label: "待审核",
    count: (tasks.data.value || []).filter((item) => inView(item, "review"))
      .length,
  },
  {
    key: "completed" as const,
    label: "已完成",
    count: (tasks.data.value || []).filter((item) => inView(item, "completed"))
      .length,
  },
  {
    key: "failed" as const,
    label: "异常",
    count: (tasks.data.value || []).filter((item) => inView(item, "failed"))
      .length,
  },
]);
const filtered = computed(() => {
  const keyword = search.value.trim().toLowerCase();
  return (tasks.data.value || [])
    .filter(
      (task) =>
        !keyword ||
        `${task.theme} ${task.task_id} ${task.update_reason || ""}`
          .toLowerCase()
          .includes(keyword),
    )
    .filter((task) => inView(task, view.value))
    .filter(withinDate)
    .filter(
      (task) =>
        !variant.value || String(task.variant_id || "") === variant.value,
    )
    .sort((a, b) => {
      if (sortBy.value === "created_desc")
        return parseTimestamp(b.created_at) - parseTimestamp(a.created_at);
      if (sortBy.value === "name")
        return a.theme.localeCompare(b.theme, "zh-CN");
      if (sortBy.value === "materials_desc")
        return (b.material_count || 0) - (a.material_count || 0);
      return timestamp(b) - timestamp(a);
    });
});
const selected = computed(
  () =>
    (tasks.data.value || []).find(
      (item) => item.task_id === selectedId.value,
    ) || null,
);
const hasFilters = computed(() =>
  Boolean(
    search.value ||
    dateRange.value !== "all" ||
    variant.value ||
    sortBy.value !== "updated_desc",
  ),
);
function resetFilters() {
  search.value = "";
  dateRange.value = "all";
  variant.value = "";
  sortBy.value = "updated_desc";
}
function formatDate(value?: string) {
  const timestamp = parseTimestamp(value);
  if (!timestamp) return "—";
  return new Date(timestamp).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
const remove = useMutation({
  mutationFn: (id: string) => api(`/api/tasks/${id}`, { method: "DELETE" }),
  onSuccess: () => {
    selectedId.value = null;
    queryClient.invalidateQueries({ queryKey: ["tasks"] });
  },
});
function deleteTask(task: TaskSummary) {
  if (confirm(`删除任务“${task.theme}”？该操作不可恢复。`))
    remove.mutate(task.task_id);
}
function taskType(task: TaskSummary) {
  return task.run_mode === "material_comparison"
    ? "新增材料对比"
    : task.incremental_update
      ? "增量更新"
      : "首次生成";
}
</script>

<template>
  <div class="page-stack tasks-page">
    <UiPageHeader title="任务">
      <template #actions
        ><UiButton as="a" href="/?create=1" class="page-action"
          ><AppIcon name="plus" :size="16" />新建任务</UiButton
        ></template
      >
    </UiPageHeader>
    <UiDataBrowser :detail-open="Boolean(selected)" detail-width="332px" label="任务浏览器">
      <div class="browser-main">
        <nav class="view-tabs" aria-label="任务视图">
          <button
            v-for="item in viewItems"
            :key="item.key"
            :class="{ active: view === item.key }"
            @click="view = item.key"
          >
            {{ item.label
            }}<span>{{ tasks.isLoading.value ? "—" : item.count }}</span>
          </button>
        </nav>
        <div class="filter-bar">
          <label class="search-box"
            ><AppIcon name="search" :size="16" /><input
              v-model="search"
              placeholder="搜索任务名称、ID 或更新说明"
          /></label>
          <select v-model="dateRange">
            <option value="all">全部时间</option>
            <option value="7">近 7 天</option>
            <option value="30">近 30 天</option>
            <option value="90">近 90 天</option>
          </select>
          <select v-model="variant">
            <option value="">全部模板</option>
            <option
              v-for="item in templates.data.value || []"
              :key="item.id"
              :value="String(item.id)"
            >
              {{ item.name || item.label || `模板 ${item.id}` }}
            </option>
          </select>
          <select v-model="sortBy">
            <option value="updated_desc">最近更新</option>
            <option value="created_desc">最近创建</option>
            <option value="materials_desc">材料最多</option>
            <option value="name">按名称</option>
          </select>
          <button
            v-if="hasFilters"
            class="btn tertiary reset"
            @click="resetFilters"
          >
            重置
          </button>
        </div>
        <div class="result-meta">
          <span>{{
            tasks.isLoading.value
              ? "正在加载任务…"
              : `共 ${filtered.length} 个任务`
          }}</span
          ><span v-if="tasks.isFetching.value && !tasks.isLoading.value"
            >正在更新…</span
          >
        </div>
        <div v-if="tasks.isLoading.value" class="loading-line"></div>
        <div v-if="filtered.length" class="task-table">
          <div class="table-head">
            <span>任务名称</span><span>状态</span><span>材料</span
            ><span>模板</span><span>最近更新</span><span></span>
          </div>
          <div
            v-for="task in filtered"
            :key="task.task_id"
            class="task-row"
            :class="{ selected: selectedId === task.task_id }"
            @click="selectedId = task.task_id"
          >
            <div class="task-name">
              <strong>{{ task.theme }}</strong
              ><small
                ><span
                  v-if="task.run_mode === 'material_comparison'"
                  class="task-kind"
                  >材料对比</span
                ><span class="mono">{{ task.task_id }}</span></small
              >
            </div>
            <StatusBadge :stage="task.stage" /><span
              >{{ task.material_count || 0 }} 份</span
            ><span class="truncate">{{
              task.run_mode === "material_comparison"
                ? "基线报告"
                : task.variant_id
                  ? templateNames.get(Number(task.variant_id)) ||
                    `模板 ${task.variant_id}`
                  : "默认模板"
            }}</span
            ><time>{{ formatDate(task.updated_at || task.created_at) }}</time
            ><RouterLink
              class="open-link"
              :to="`/tasks/${task.task_id}`"
              @click.stop
              >{{
                task.run_mode === "material_comparison" ? "审阅" : "打开"
              }}</RouterLink
            >
          </div>
        </div>
        <div v-else-if="!tasks.isLoading.value" class="empty">
          <div>
            <strong>没有匹配的任务</strong>调整筛选条件，或创建新的报告任务。
          </div>
        </div>
      </div>
      <template v-if="selected" #detail><div class="task-detail">
        <div class="detail-head">
          <small>任务摘要</small
          ><button
            class="icon-button"
            aria-label="关闭详情"
            @click="selectedId = null"
          >
            <AppIcon name="close" :size="17" />
          </button>
        </div>
        <h2>{{ selected.theme }}</h2>
        <StatusBadge :stage="selected.stage" />
        <dl>
          <dt>任务 ID</dt>
          <dd class="mono">{{ selected.task_id }}</dd>
          <dt>材料数量</dt>
          <dd>{{ selected.material_count || 0 }} 份</dd>
          <dt>报告版本</dt>
          <dd>第 {{ selected.run_revision || 1 }} 版</dd>
          <dt>任务类型</dt>
          <dd>{{ taskType(selected) }}</dd>
          <dt>使用模板</dt>
          <dd>
            {{
              selected.variant_id
                ? templateNames.get(Number(selected.variant_id)) ||
                  `模板 ${selected.variant_id}`
                : "默认模板"
            }}
          </dd>
          <dt>创建时间</dt>
          <dd>{{ formatDate(selected.created_at) }}</dd>
          <dt>最近更新</dt>
          <dd>{{ formatDate(selected.updated_at || selected.created_at) }}</dd>
        </dl>
        <p v-if="selected.update_reason" class="update-reason">
          <b>更新说明</b>{{ selected.update_reason }}
        </p>
        <div class="detail-actions">
          <RouterLink class="btn primary" :to="`/tasks/${selected.task_id}`"
            >打开任务</RouterLink
          ><button class="btn danger" @click="deleteTask(selected)">
            删除任务
          </button>
        </div>
      </div></template>
    </UiDataBrowser>
  </div>
</template>

<style scoped>
.page-action {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  box-shadow: none;
}
.browser-main {
  min-width: 0;
}
.view-tabs {
  display: flex;
  gap: 4px;
  padding: 16px 18px 0;
  border-bottom: 1px solid var(--border);
  background: var(--muted);
}
.view-tabs button {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 8px 12px 12px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--muted-foreground);
  transition:
    color var(--motion-fast),
    background var(--motion-fast);
}
.view-tabs button:hover {
  color: var(--foreground);
  background: var(--muted);
}
.view-tabs button.active {
  color: var(--primary);
  border-bottom-color: var(--primary);
  font-weight: 700;
}
.view-tabs span {
  min-width: 21px;
  padding: 1px 5px;
  border-radius: 999px;
  background: var(--muted);
  color: var(--subtle-foreground);
  font-size: 11px;
}
.view-tabs button.active span {
  background: var(--primary-soft);
  color: var(--primary);
}
.filter-bar {
  display: grid;
  grid-template-columns: minmax(180px, 2fr) repeat(3, minmax(112px, 1fr)) auto;
  min-width: 0;
  gap: 10px;
  padding: 14px 18px;
}
.search-box {
  position: relative;
}
.search-box :deep(svg) {
  position: absolute;
  left: 12px;
  top: 50%;
  z-index: 1;
  transform: translateY(-50%);
  color: var(--subtle-foreground);
}
.search-box input {
  padding-left: 37px;
  background: var(--surface-raised);
}
.reset {
  padding-inline: 7px;
}
.result-meta {
  display: flex;
  justify-content: space-between;
  min-height: 32px;
  padding: 0 18px;
  color: var(--subtle-foreground);
  font-size: 12px;
}
.table-head,
.task-row {
  display: grid;
  grid-template-columns: minmax(180px, 1.5fr) minmax(96px, 0.55fr) minmax(56px, 0.32fr) minmax(
      100px,
      0.7fr
    ) 108px 38px;
  min-width: 0;
  gap: 12px;
  align-items: center;
}
.table-head {
  padding: 9px 18px;
  background: transparent;
  border-block: 1px solid var(--border);
  color: var(--subtle-foreground);
  font-size: 11px;
  font-weight: 650;
  letter-spacing: 0.04em;
}
.task-row {
  position: relative;
  min-height: 68px;
  padding: 11px 18px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  transition:
    background var(--motion-fast),
    padding var(--motion-fast);
}
.task-row::before {
  position: absolute;
  left: 0;
  top: 17px;
  bottom: 17px;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: transparent;
  content: "";
}
.task-row:hover,
.task-row.selected {
  background: var(--surface-hover);
}
.task-row.selected::before {
  background: var(--primary);
}
.task-row:hover {
  padding-inline: 18px;
}
.task-name strong,
.task-name small {
  display: block;
}
.task-name strong {
  font-weight: 650;
  letter-spacing: -0.01em;
}
.task-name small {
  margin-top: 3px;
  color: var(--subtle-foreground);
  font-size: 11px;
}
.task-row > span,
.task-row time {
  min-width: 0;
  color: var(--muted-foreground);
  font-size: 13px;
}
.task-row > :nth-child(2) {
  overflow: hidden;
}
.task-row > :nth-child(2) :deep(.ui-badge) {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
}
.truncate {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.open-link {
  color: var(--primary);
  font-size: 13px;
  font-weight: 650;
}
.task-detail {
  padding: 22px;
}
.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--subtle-foreground);
}
.icon-button {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border: 0;
  background: transparent;
  color: var(--muted-foreground);
  border-radius: 7px;
}
.icon-button:hover {
  background: var(--muted);
}
.task-detail h2 {
  margin: 12px 0 10px;
  line-height: 1.45;
}
.task-detail dl {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr);
  gap: 11px;
  margin: 24px 0;
  padding-top: 18px;
  border-top: 1px solid var(--border);
}
.task-detail dt {
  color: var(--subtle-foreground);
}
.task-detail dd {
  min-width: 0;
  margin: 0;
  word-break: break-all;
}
.update-reason {
  display: grid;
  gap: 5px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--card);
  color: var(--muted-foreground);
}
.update-reason b {
  color: var(--foreground);
}
.detail-actions {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  margin-top: 24px;
}
.detail-actions .btn {
  text-align: center;
}
@media (max-width: 1180px) {
}
@media (max-width: 900px) {
  .filter-bar {
    grid-template-columns: 1fr 1fr;
  }
  .search-box {
    grid-column: 1/-1;
  }
  .table-head,
  .task-row {
    grid-template-columns: minmax(220px, 1fr) minmax(92px, auto) 84px 42px;
  }
  .table-head > *:nth-child(3),
  .table-head > *:nth-child(4),
  .task-row > *:nth-child(3),
  .task-row > *:nth-child(4) {
    display: none;
  }
}
@media (max-width: 600px) {
  .tasks-page .page-header {
    min-height: 58px;
  }
  .filter-bar {
    grid-template-columns: 1fr;
  }
  .search-box {
    grid-column: auto;
  }
  .view-tabs {
    overflow: auto;
  }
  .view-tabs button {
    white-space: nowrap;
  }
  .table-head,
  .task-row {
    grid-template-columns: minmax(0, 1fr) auto 36px;
  }
  .table-head > *:nth-child(5),
  .task-row > *:nth-child(5) {
    display: none;
  }
}
.task-name small {
  display: flex;
  align-items: center;
  gap: 7px;
}
.task-kind {
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--info-soft);
  color: var(--info);
}
</style>
