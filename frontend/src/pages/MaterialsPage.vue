<script setup lang="ts">
import { computed, ref } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { RouterLink, useRouter } from "vue-router";
import { api } from "@/api/http";
import type { MaterialSummary } from "@/api/types";
import AppIcon from "@/components/AppIcon.vue";
import UiDataBrowser from "@/components/ui/UiDataBrowser.vue";
import UiPageHeader from "@/components/ui/UiPageHeader.vue";
import UiButton from "@/components/ui/UiButton.vue";
import { useUiStore } from "@/stores/ui";

const search = ref("");
const type = ref("");
const status = ref("");
const usage = ref("");
const dateRange = ref("all");
const sortBy = ref("recent");
const router = useRouter();
const ui = useUiStore();
const openingAssistant = ref(false);
const selected = ref<number | null>(null);
const detailTab = ref("overview");
const list = useQuery({
  queryKey: ["materials"],
  queryFn: () => api<MaterialSummary[]>("/api/materials"),
});
const detail = useQuery({
  queryKey: computed(() => ["material", selected.value]),
  queryFn: () => api<any>(`/api/materials/${selected.value}`),
  enabled: computed(() => selected.value !== null),
});
const types = computed(() =>
  [
    ...new Set(
      (list.data.value || []).map((item) => item.file_type).filter(Boolean),
    ),
  ].sort(),
);

function taskCount(item: MaterialSummary) {
  return item.tasks?.length || 0;
}
function withinDate(item: MaterialSummary) {
  if (dateRange.value === "all") return true;
  const value = Date.parse(item.parsed_at || "");
  return (
    Number.isFinite(value) &&
    value >= Date.now() - Number(dateRange.value) * 86_400_000
  );
}
function matchesStatus(item: MaterialSummary) {
  if (!status.value) return true;
  if (status.value === "duplicate") return Boolean(item.is_duplicate);
  if (status.value === "ready")
    return item.parse_status === "ready" && !item.is_duplicate;
  return item.parse_status === status.value;
}
function matchesUsage(item: MaterialSummary) {
  if (!usage.value) return true;
  const count = taskCount(item);
  return usage.value === "unused"
    ? count === 0
    : usage.value === "reused"
      ? count > 1
      : count === 1;
}
const filtered = computed(() => {
  const keyword = search.value.trim().toLowerCase();
  return (list.data.value || [])
    .filter(
      (item) =>
        !keyword ||
        `${item.filename} ${(item.tasks || []).map((task) => (typeof task === "string" ? task : `${task.theme || ""} ${task.task_id}`)).join(" ")}`
          .toLowerCase()
          .includes(keyword),
    )
    .filter((item) => !type.value || item.file_type === type.value)
    .filter(matchesStatus)
    .filter(matchesUsage)
    .filter(withinDate)
    .sort((a, b) => {
      if (sortBy.value === "name")
        return a.filename.localeCompare(b.filename, "zh-CN");
      if (sortBy.value === "units") return b.unit_count - a.unit_count;
      if (sortBy.value === "usage") return taskCount(b) - taskCount(a);
      return (
        Date.parse(b.parsed_at || "") - Date.parse(a.parsed_at || "") ||
        b.id - a.id
      );
    });
});
const hasFilters = computed(() =>
  Boolean(
    search.value ||
    type.value ||
    status.value ||
    usage.value ||
    dateRange.value !== "all" ||
    sortBy.value !== "recent",
  ),
);
function resetFilters() {
  search.value = "";
  type.value = "";
  status.value = "";
  usage.value = "";
  dateRange.value = "all";
  sortBy.value = "recent";
}
async function selectMaterial(id: number) {
  selected.value = id;
  detailTab.value = "overview";
  await discussMaterial();
}
function formatDate(value?: string) {
  if (!value) return "尚未解析";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
}
function statusLabel(item: MaterialSummary) {
  return item.is_duplicate
    ? "重复"
    : item.parse_status === "ready"
      ? "已解析"
      : item.parse_status === "error"
        ? "异常"
        : "待解析";
}
function unitKindLabel(value: string) {
  return (
    (
      {
        text: "正文",
        paragraph: "段落",
        table: "表格",
        image: "图片",
        picture: "图片",
        heading: "标题",
      } as any
    )[String(value || "").toLowerCase()] || "内容"
  );
}
async function discussMaterial() {
  if (!selected.value || openingAssistant.value) return;
  openingAssistant.value = true;
  try {
    const result = await api<{ task_id: string }>(`/api/materials/${selected.value}/assistant-session`, { method: "POST" });
    await router.replace({ path: "/materials", query: { materialSession: result.task_id, materialId: String(selected.value), assistant: "1" } });
  } finally {
    openingAssistant.value = false;
  }
}
</script>

<template>
  <div class="page-stack materials-page">
    <UiPageHeader title="材料库" />
    <UiDataBrowser
      class="material-browser"
      :class="{ 'material-browser--detail-open': selected !== null }"
      :detail-open="selected !== null"
      detail-width="372px"
      label="材料浏览器"
    >
      <div class="material-list">
        <div class="filter-bar">
          <label class="search-box"
            ><AppIcon name="search" :size="16" /><input
              v-model="search"
              placeholder="搜索文件名或关联任务"
          /></label>
          <select v-model="type">
            <option value="">全部类型</option>
            <option v-for="item in types" :key="item" :value="item">
              {{ item.toUpperCase() }}
            </option>
          </select>
          <select v-model="status">
            <option value="">全部状态</option>
            <option value="ready">已解析</option>
            <option value="pending">待解析</option>
            <option value="duplicate">重复材料</option>
          </select>
          <select v-model="usage">
            <option value="">全部使用关系</option>
            <option value="unused">尚未使用</option>
            <option value="single">单任务使用</option>
            <option value="reused">多任务复用</option>
          </select>
          <select v-model="dateRange">
            <option value="all">全部时间</option>
            <option value="7">近 7 天</option>
            <option value="30">近 30 天</option>
            <option value="90">近 90 天</option>
          </select>
          <select v-model="sortBy">
            <option value="recent">最近解析</option>
            <option value="name">按文件名</option>
            <option value="units">内容单元最多</option>
            <option value="usage">引用任务最多</option>
          </select>
          <button v-if="hasFilters" class="btn tertiary" @click="resetFilters">
            重置
          </button>
        </div>
        <div class="result-meta">
          <span>共 {{ filtered.length }} 份材料</span
          ><span
            >{{
              (list.data.value || []).filter((item) => taskCount(item) > 1)
                .length
            }}
            份被多个任务复用</span
          >
        </div>
        <div v-if="list.isLoading.value" class="loading-line"></div>
        <div class="table-head">
          <span>文件名</span><span>类型</span><span>状态</span
          ><span>内容单元</span><span>关联任务</span><span>解析时间</span>
        </div>
        <button
          v-for="item in filtered"
          :key="item.id"
          class="file-row"
          :class="{ selected: selected === item.id }"
          @click="selectMaterial(item.id)"
        >
          <span class="file-name"
            ><b>{{ item.filename }}</b
            ><small v-if="item.is_duplicate"
              >与材料 #{{ item.duplicate_of }} 内容重复</small
            ></span
          ><span class="type-label">{{
            (item.file_type || "—").toUpperCase()
          }}</span
          ><span
            class="parse-state"
            :class="item.is_duplicate ? 'duplicate' : item.parse_status"
            ><i></i>{{ statusLabel(item) }}</span
          ><span>{{ item.unit_count }}</span
          ><span>{{ taskCount(item) }}</span
          ><time>{{ formatDate(item.parsed_at) }}</time>
        </button>
        <div v-if="!filtered.length && !list.isLoading.value" class="empty">
          <div>
            <strong>没有匹配的材料</strong
            >{{
              hasFilters
                ? "调整筛选条件后再试。"
                : "材料会在创建任务时进入材料库。"
            }}
          </div>
        </div>
      </div>

      <template v-if="selected !== null" #detail><div class="material-detail">
        <template v-if="detail.data.value">
          <div class="detail-head">
            <div>
              <small>材料详情</small>
              <h2>{{ detail.data.value.filename }}</h2>
            </div>
            <button
              class="icon-button"
              aria-label="关闭详情"
              @click="selected = null"
            >
              <AppIcon name="close" :size="17" />
            </button>
          </div>
          <div class="tabs">
            <button
              v-for="tab in [
                ['overview', '概览'],
                ['content', '解析内容'],
              ]"
              :key="tab[0]"
              class="tab"
              :class="{ active: detailTab === tab[0] }"
              @click="detailTab = tab[0]"
            >
              {{ tab[1] }}
            </button>
          </div>
          <div v-if="detailTab === 'overview'" class="detail-body">
            <dl>
              <dt>文件类型</dt>
              <dd>{{ (detail.data.value.file_type || "—").toUpperCase() }}</dd>
              <dt>解析状态</dt>
              <dd>{{ statusLabel(detail.data.value) }}</dd>
              <dt>解析时间</dt>
              <dd>{{ formatDate(detail.data.value.parsed_at) }}</dd>
              <dt>内容单元</dt>
              <dd>{{ detail.data.value.units?.length || 0 }}</dd>
              <dt>关联任务</dt>
              <dd>{{ detail.data.value.tasks?.length || 0 }}</dd>
            </dl>
            <section v-if="detail.data.value.insight?.topic || detail.data.value.insight?.key_points?.length" class="material-insight">
              <small>材料理解</small>
              <h3>{{ detail.data.value.insight.topic || '已解析材料' }}</h3>
              <p v-if="detail.data.value.insight.doc_type || detail.data.value.insight.material_role">{{ [detail.data.value.insight.doc_type, detail.data.value.insight.material_role].filter(Boolean).join(' · ') }}</p>
              <ul v-if="detail.data.value.insight.key_points?.length"><li v-for="item in detail.data.value.insight.key_points" :key="item">{{ item }}</li></ul>
              <ol v-if="detail.data.value.insight.key_sections?.length"><li v-for="item in detail.data.value.insight.key_sections" :key="item">{{ item }}</li></ol>
            </section>
            <UiButton v-if="detail.data.value.parse_status === 'ready'" class="material-discuss" variant="outline" :loading="openingAssistant" @click="discussMaterial">与助手讨论</UiButton>
            <h3>使用记录</h3>
            <RouterLink
              v-for="task in detail.data.value.tasks || []"
              :key="task.task_id || task"
              :to="`/tasks/${task.task_id || task}`"
              class="task-use"
              ><span>{{ task.theme || task.task_id || task }}</span
              ><b>打开任务</b></RouterLink
            >
            <div v-if="!detail.data.value.tasks?.length" class="quiet-empty">
              尚未被任何任务使用
            </div>
          </div>
          <div v-else-if="detailTab === 'content'" class="unit-list">
            <details
              v-for="unit in detail.data.value.units || []"
              :key="unit.id"
            >
              <summary>
                <span>{{ unitKindLabel(unit.kind) }}</span
                ><b>{{ unit.page ? `第 ${unit.page} 页` : "文档内容" }}</b>
              </summary>
              <p>{{ unit.content || unit.image_desc || "无文本内容" }}</p>
            </details>
          </div>
        </template>
        <div v-else-if="detail.isLoading.value" class="detail-loading">
          <div class="loading-line"></div>
        </div>
        <div v-else class="empty">
          <div>
            <strong>选择一份材料</strong>查看解析内容、材料理解和任务关系。
          </div>
        </div>
      </div></template>
    </UiDataBrowser>
  </div>
</template>

<style scoped>
.material-list {
  min-width: 0;
}
.filter-bar {
  display: grid;
  grid-template-columns: minmax(180px, 2fr) repeat(5, minmax(88px, 1fr)) auto;
  min-width: 0;
  gap: 9px;
  padding: 14px 18px;
}
.material-browser--detail-open .filter-bar {
  grid-template-columns: minmax(180px, 1fr) repeat(2, minmax(96px, 120px));
}
.material-browser--detail-open .table-head,
.material-browser--detail-open .file-row {
  grid-template-columns: minmax(230px, 1fr) 56px 80px 68px;
}
.material-browser--detail-open .table-head > *:nth-child(5),
.material-browser--detail-open .table-head > *:nth-child(6),
.material-browser--detail-open .file-row > *:nth-child(5),
.material-browser--detail-open .file-row > *:nth-child(6) {
  display: none;
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
.result-meta {
  display: flex;
  justify-content: space-between;
  min-height: 32px;
  padding: 0 18px;
  color: var(--subtle-foreground);
  font-size: 12px;
}
.table-head,
.file-row {
  display: grid;
  grid-template-columns: minmax(180px, 1.5fr) 56px 76px 68px 64px 112px;
  min-width: 0;
  gap: 12px;
  align-items: center;
}
.table-head {
  padding: 9px 18px;
  background: transparent;
  color: var(--subtle-foreground);
  background: var(--muted);
  border-block: 1px solid var(--border);
  font-size: 11px;
  font-weight: 650;
  letter-spacing: 0.04em;
}
.file-row {
  position: relative;
  width: 100%;
  min-height: 66px;
  padding: 11px 18px;
  text-align: left;
  border: 0;
  border-bottom: 1px solid var(--border);
  background: transparent;
  transition:
    background var(--motion-fast),
    padding var(--motion-fast);
}
.file-row::before {
  position: absolute;
  left: 0;
  top: 17px;
  bottom: 17px;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: transparent;
  content: "";
}
.file-row:hover,
.file-row.selected {
  background: var(--surface-hover);
}
.file-row.selected::before {
  background: var(--primary);
}
.file-row:hover {
  padding-inline: 18px;
}
.file-row > span,
.file-row time {
  color: var(--muted-foreground);
  font-size: 13px;
}
.file-name {
  min-width: 0;
}
.file-name b,
.file-name small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-name b {
  color: var(--foreground);
  font-weight: 650;
}
.file-name small {
  color: var(--warning);
  font-size: 11px;
}
.type-label {
  font-size: 11px;
  letter-spacing: 0.04em;
}
.parse-state {
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}
.parse-state i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--subtle-foreground);
}
.parse-state.ready i {
  background: var(--success);
  box-shadow: 0 0 0 3px var(--success-soft);
}
.parse-state.pending i {
  background: var(--warning);
}
.parse-state.error i {
  background: var(--destructive);
}
.parse-state.duplicate i {
  background: var(--subtle-foreground);
}
.material-detail {
  min-width: 0;
}
.detail-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 22px 22px 12px;
}
.detail-head small {
  color: var(--subtle-foreground);
  font-size: 11px;
}
.detail-head h2 {
  margin: 3px 0;
  word-break: break-all;
  line-height: 1.45;
}
.icon-button {
  display: grid;
  place-items: center;
  flex: 0 0 30px;
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
.material-detail .tabs {
  padding: 0 22px;
}
.detail-body,
.unit-list {
  padding: 22px;
  margin: 0;
}
.detail-body dl {
  display: grid;
  grid-template-columns: 78px 1fr;
  gap: 9px;
  margin: 0 0 24px;
}
.detail-body dt {
  color: var(--muted-foreground);
}
.detail-body dd {
  margin: 0;
}
.task-use {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 11px 0;
  border-top: 1px solid var(--border);
  color: var(--primary);
}
.task-use span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-use b {
  font-size: 12px;
  white-space: nowrap;
}
.material-insight { margin: 0 0 18px; padding: 14px 0; border-top: 1px solid var(--border); }.material-insight small { color: var(--subtle-foreground); font-size: 11px; }.material-insight h3 { margin: 4px 0; }.material-insight p, .material-insight li { color: var(--muted-foreground); line-height: 1.6; }.material-insight ul, .material-insight ol { margin: 8px 0 0; padding-left: 18px; }.material-discuss { width: 100%; margin: 2px 0 18px; }
.quiet-empty {
  padding: 20px 0;
  color: var(--subtle-foreground);
  text-align: center;
}
.unit-list {
  display: grid;
  gap: 8px;
  max-height: 550px;
  overflow: auto;
}
.unit-list details {
  padding: 11px;
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--surface-raised);
}
.unit-list summary {
  display: flex;
  justify-content: space-between;
  cursor: pointer;
}
.unit-list summary span {
  color: var(--primary);
}
.unit-list summary b {
  font-size: 12px;
}
.unit-list p {
  white-space: pre-wrap;
}
.metadata {
  padding-top: 20px;
}
@media (max-width: 1250px) {
  .filter-bar {
    grid-template-columns: repeat(3, minmax(105px, 1fr));
  }
  .search-box {
    grid-column: 1/-1;
  }
}
@media (max-width: 1080px) {
}
@media (max-width: 800px) {
  .filter-bar {
    grid-template-columns: 1fr 1fr;
  }
  .search-box {
    grid-column: 1/-1;
  }
  .table-head,
  .file-row {
    grid-template-columns: minmax(220px, 1fr) 58px 80px 65px;
  }
  .table-head > *:nth-child(5),
  .table-head > *:nth-child(6),
  .file-row > *:nth-child(5),
  .file-row > *:nth-child(6) {
    display: none;
  }
}
@media (max-width: 560px) {
  .table-head,
  .file-row {
    grid-template-columns: minmax(0, 1fr) 74px;
  }
  .table-head > *:nth-child(2),
  .table-head > *:nth-child(4),
  .file-row > *:nth-child(2),
  .file-row > *:nth-child(4) {
    display: none;
  }
}
</style>
