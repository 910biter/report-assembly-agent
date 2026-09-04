<script setup lang="ts">
import { computed, ref } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { RouterLink } from "vue-router";
import { api } from "@/api/http";
import type { MaterialSummary } from "@/api/types";
import AppIcon from "@/components/AppIcon.vue";

const search = ref("");
const type = ref("");
const status = ref("");
const usage = ref("");
const dateRange = ref("all");
const sortBy = ref("recent");
const selected = ref<number | null>(null);
const detailTab = ref("overview");
const list = useQuery({ queryKey: ["materials"], queryFn: () => api<MaterialSummary[]>("/api/materials") });
const detail = useQuery({ queryKey: computed(() => ["material", selected.value]), queryFn: () => api<any>(`/api/materials/${selected.value}`), enabled: computed(() => selected.value !== null) });
const types = computed(() => [...new Set((list.data.value || []).map(item => item.file_type).filter(Boolean))].sort());

function taskCount(item: MaterialSummary) { return item.tasks?.length || 0; }
function withinDate(item: MaterialSummary) {
  if (dateRange.value === "all") return true;
  const value = Date.parse(item.parsed_at || "");
  return Number.isFinite(value) && value >= Date.now() - Number(dateRange.value) * 86_400_000;
}
function matchesStatus(item: MaterialSummary) {
  if (!status.value) return true;
  if (status.value === "duplicate") return Boolean(item.is_duplicate);
  if (status.value === "ready") return item.parse_status === "ready" && !item.is_duplicate;
  return item.parse_status === status.value;
}
function matchesUsage(item: MaterialSummary) {
  if (!usage.value) return true;
  const count = taskCount(item);
  return usage.value === "unused" ? count === 0 : usage.value === "reused" ? count > 1 : count === 1;
}
const filtered = computed(() => {
  const keyword = search.value.trim().toLowerCase();
  return (list.data.value || [])
    .filter(item => !keyword || `${item.filename} ${(item.tasks || []).map(task => typeof task === "string" ? task : `${task.theme || ""} ${task.task_id}`).join(" ")}`.toLowerCase().includes(keyword))
    .filter(item => !type.value || item.file_type === type.value).filter(matchesStatus).filter(matchesUsage).filter(withinDate)
    .sort((a, b) => {
      if (sortBy.value === "name") return a.filename.localeCompare(b.filename, "zh-CN");
      if (sortBy.value === "units") return b.unit_count - a.unit_count;
      if (sortBy.value === "usage") return taskCount(b) - taskCount(a);
      return Date.parse(b.parsed_at || "") - Date.parse(a.parsed_at || "") || b.id - a.id;
    });
});
const hasFilters = computed(() => Boolean(search.value || type.value || status.value || usage.value || dateRange.value !== "all" || sortBy.value !== "recent"));
function resetFilters() { search.value = ""; type.value = ""; status.value = ""; usage.value = ""; dateRange.value = "all"; sortBy.value = "recent"; }
function selectMaterial(id: number) { selected.value = id; detailTab.value = "overview"; }
function formatDate(value?: string) { if (!value) return "尚未解析"; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }
function statusLabel(item: MaterialSummary) { return item.is_duplicate ? "重复" : item.parse_status === "ready" ? "已解析" : item.parse_status === "error" ? "异常" : "待解析"; }
function unitKindLabel(value: string) { return ({ text: "正文", paragraph: "段落", table: "表格", image: "图片", picture: "图片", heading: "标题" } as any)[String(value || "").toLowerCase()] || "内容"; }
const metadataRows = computed(() => {
  const metadata = detail.data.value?.units?.[0]?.metadata || {};
  const labels: Record<string, string> = {
    source_type: "内容来源", language: "识别语言", page_count: "页数",
    has_ocr: "文字识别", has_tables: "表格识别", has_images: "图片识别",
    title: "文档标题", author: "作者", created_at: "创建时间",
  };
  return Object.entries(metadata).flatMap(([key, value]) => {
    if (!labels[key] || value == null || typeof value === "object") return [];
    const display = typeof value === "boolean" ? (value ? "已启用" : "未发现") : String(value);
    return [{ key, label: labels[key], value: display }];
  });
});
</script>

<template>
  <div class="page-stack materials-page">
    <header class="page-header"><h1>材料库</h1></header>
    <section class="surface material-shell" :class="{ 'has-detail': selected !== null }">
      <div class="material-list">
        <div class="filter-bar">
          <label class="search-box"><AppIcon name="search" :size="16" /><input v-model="search" placeholder="搜索文件名或关联任务" /></label>
          <select v-model="type"><option value="">全部类型</option><option v-for="item in types" :key="item" :value="item">{{ item.toUpperCase() }}</option></select>
          <select v-model="status"><option value="">全部状态</option><option value="ready">已解析</option><option value="pending">待解析</option><option value="duplicate">重复材料</option></select>
          <select v-model="usage"><option value="">全部使用关系</option><option value="unused">尚未使用</option><option value="single">单任务使用</option><option value="reused">多任务复用</option></select>
          <select v-model="dateRange"><option value="all">全部时间</option><option value="7">近 7 天</option><option value="30">近 30 天</option><option value="90">近 90 天</option></select>
          <select v-model="sortBy"><option value="recent">最近解析</option><option value="name">按文件名</option><option value="units">内容单元最多</option><option value="usage">引用任务最多</option></select>
          <button v-if="hasFilters" class="btn tertiary" @click="resetFilters">重置</button>
        </div>
        <div class="result-meta"><span>共 {{ filtered.length }} 份材料</span><span>{{ (list.data.value || []).filter(item => taskCount(item) > 1).length }} 份被多个任务复用</span></div>
        <div v-if="list.isLoading.value" class="loading-line"></div>
        <div class="table-head"><span>文件名</span><span>类型</span><span>状态</span><span>内容单元</span><span>关联任务</span><span>解析时间</span></div>
        <button v-for="item in filtered" :key="item.id" class="file-row" :class="{ selected: selected === item.id }" @click="selectMaterial(item.id)">
          <span class="file-name"><b>{{ item.filename }}</b><small v-if="item.is_duplicate">与材料 #{{ item.duplicate_of }} 内容重复</small></span><span class="type-label">{{ (item.file_type || '—').toUpperCase() }}</span><span class="parse-state" :class="item.is_duplicate ? 'duplicate' : item.parse_status"><i></i>{{ statusLabel(item) }}</span><span>{{ item.unit_count }}</span><span>{{ taskCount(item) }}</span><time>{{ formatDate(item.parsed_at) }}</time>
        </button>
        <div v-if="!filtered.length && !list.isLoading.value" class="empty"><div><strong>没有匹配的材料</strong>{{ hasFilters ? '调整筛选条件后再试。' : '材料会在创建任务时进入材料库。' }}</div></div>
      </div>

      <aside v-if="selected !== null" class="material-detail open">
        <template v-if="detail.data.value">
          <div class="detail-head"><div><small>材料详情</small><h2>{{ detail.data.value.filename }}</h2></div><button class="icon-button" aria-label="关闭详情" @click="selected = null"><AppIcon name="close" :size="17" /></button></div>
          <div class="tabs"><button v-for="tab in [['overview','概览'],['content','解析内容'],['meta','元数据']]" :key="tab[0]" class="tab" :class="{ active: detailTab === tab[0] }" @click="detailTab = tab[0]">{{ tab[1] }}</button></div>
          <div v-if="detailTab === 'overview'" class="detail-body"><dl><dt>文件类型</dt><dd>{{ (detail.data.value.file_type || '—').toUpperCase() }}</dd><dt>解析状态</dt><dd>{{ statusLabel(detail.data.value) }}</dd><dt>解析时间</dt><dd>{{ formatDate(detail.data.value.parsed_at) }}</dd><dt>内容单元</dt><dd>{{ detail.data.value.units?.length || 0 }}</dd><dt>关联任务</dt><dd>{{ detail.data.value.tasks?.length || 0 }}</dd></dl><h3>使用记录</h3><RouterLink v-for="task in detail.data.value.tasks || []" :key="task.task_id || task" :to="`/tasks/${task.task_id || task}`" class="task-use"><span>{{ task.theme || task.task_id || task }}</span><b>打开任务</b></RouterLink><div v-if="!detail.data.value.tasks?.length" class="quiet-empty">尚未被任何任务使用</div></div>
          <div v-else-if="detailTab === 'content'" class="unit-list"><details v-for="unit in detail.data.value.units || []" :key="unit.id"><summary><span>{{ unitKindLabel(unit.kind) }}</span><b>{{ unit.page ? `第 ${unit.page} 页` : '文档内容' }}</b></summary><p>{{ unit.content || unit.image_desc || '无文本内容' }}</p></details></div>
          <div v-else class="detail-body metadata"><dl v-if="metadataRows.length"><template v-for="row in metadataRows" :key="row.key"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></template></dl><div v-else class="quiet-empty">该材料没有需要额外展示的文档属性</div></div>
        </template>
        <div v-else-if="detail.isLoading.value" class="detail-loading"><div class="loading-line"></div></div><div v-else class="empty"><div><strong>选择一份材料</strong>查看解析内容、元数据和任务关系。</div></div>
      </aside>
    </section>
  </div>
</template>

<style scoped>
.material-shell{display:grid;grid-template-columns:minmax(0,1fr);min-height:650px;overflow:hidden}.material-shell.has-detail{grid-template-columns:minmax(0,1fr) 360px}.material-list{min-width:0}.filter-bar{display:grid;grid-template-columns:minmax(250px,1fr) 100px 120px 140px 110px 130px auto;gap:9px;padding:16px}.material-shell.has-detail .filter-bar{grid-template-columns:minmax(220px,1fr) repeat(2,minmax(105px,120px))}.material-shell.has-detail .table-head,.material-shell.has-detail .file-row{grid-template-columns:minmax(230px,1fr) 56px 80px 68px}.material-shell.has-detail .table-head>*:nth-child(5),.material-shell.has-detail .table-head>*:nth-child(6),.material-shell.has-detail .file-row>*:nth-child(5),.material-shell.has-detail .file-row>*:nth-child(6){display:none}.search-box{position:relative}.search-box :deep(svg){position:absolute;left:11px;top:50%;z-index:1;transform:translateY(-50%);color:var(--color-faint)}.search-box input{padding-left:35px}.result-meta{display:flex;justify-content:space-between;min-height:31px;padding:0 16px;color:var(--color-faint);font-size:12px}.table-head,.file-row{display:grid;grid-template-columns:minmax(250px,1.5fr) 60px 82px 76px 72px 128px;gap:12px;align-items:center}.table-head{padding:8px 16px;color:var(--color-faint);background:var(--color-surface-soft);border-block:1px solid var(--color-border);font-size:12px}.file-row{width:100%;min-height:62px;padding:10px 16px;text-align:left;border:0;border-bottom:1px solid var(--color-border);background:#fff;transition:background var(--motion-fast)}.file-row:hover,.file-row.selected{background:#f4f7fb}.file-row>span,.file-row time{color:var(--color-muted);font-size:13px}.file-name{min-width:0}.file-name b,.file-name small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.file-name b{color:var(--color-text);font-weight:600}.file-name small{color:var(--color-warning);font-size:11px}.type-label{font-size:11px!important}.parse-state{display:flex;align-items:center;gap:6px;white-space:nowrap}.parse-state i{width:6px;height:6px;border-radius:50%;background:#87919c}.parse-state.ready i{background:var(--color-success)}.parse-state.pending i{background:#d08b27}.parse-state.error i{background:var(--color-danger)}.parse-state.duplicate i{background:var(--color-faint)}.material-detail{min-width:0;border-left:1px solid var(--color-border);background:#fbfcfd}.detail-head{display:flex;justify-content:space-between;gap:12px;padding:20px 20px 10px}.detail-head small{color:var(--color-faint)}.detail-head h2{margin:3px 0;word-break:break-all}.icon-button{display:grid;place-items:center;flex:0 0 30px;width:30px;height:30px;border:0;background:transparent;color:var(--color-muted);border-radius:4px}.icon-button:hover{background:var(--color-surface-soft)}.material-detail .tabs{padding:0 20px}.detail-body,.unit-list,.metadata{padding:20px;margin:0}.detail-body dl{display:grid;grid-template-columns:78px 1fr;gap:9px;margin:0 0 24px}.detail-body dt{color:var(--color-muted)}.detail-body dd{margin:0}.task-use{display:flex;justify-content:space-between;gap:12px;padding:10px 0;border-top:1px solid var(--color-border);color:var(--color-primary)}.task-use span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.task-use b{font-size:12px;white-space:nowrap}.quiet-empty{padding:20px 0;color:var(--color-faint);text-align:center}.unit-list{display:grid;gap:8px;max-height:550px;overflow:auto}.unit-list details{padding:10px;border:1px solid var(--color-border);background:#fff}.unit-list summary{display:flex;justify-content:space-between;cursor:pointer}.unit-list summary span{color:var(--color-primary)}.unit-list summary b{font-size:12px}.unit-list p{white-space:pre-wrap}.metadata{white-space:pre-wrap;overflow:auto;font-size:12px}.detail-loading{padding-top:20px}@media(max-width:1250px){.filter-bar{grid-template-columns:repeat(3,minmax(105px,1fr))}.search-box{grid-column:1/-1}}@media(max-width:1080px){.material-shell.has-detail{grid-template-columns:1fr}.material-detail{position:fixed;inset:245px 0 0 auto;z-index:22;width:min(400px,100%);box-shadow:var(--shadow-float);transform:translateX(100%);transition:transform var(--motion-fast);overflow:auto}.material-detail.open{transform:none}}@media(max-width:800px){.filter-bar{grid-template-columns:1fr 1fr}.search-box{grid-column:1/-1}.table-head,.file-row{grid-template-columns:minmax(220px,1fr) 58px 80px 65px}.table-head>*:nth-child(5),.table-head>*:nth-child(6),.file-row>*:nth-child(5),.file-row>*:nth-child(6){display:none}}@media(max-width:560px){.table-head,.file-row{grid-template-columns:minmax(0,1fr) 74px}.table-head>*:nth-child(2),.table-head>*:nth-child(4),.file-row>*:nth-child(2),.file-row>*:nth-child(4){display:none}}
</style>
