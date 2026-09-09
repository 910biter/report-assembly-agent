<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useRoute, useRouter } from "vue-router";
import { api, uploadForm } from "@/api/http";
import AppIcon from "@/components/AppIcon.vue";
import UiButton from "@/components/ui/UiButton.vue";
import UiPageHeader from "@/components/ui/UiPageHeader.vue";
import UiPanel from "@/components/ui/UiPanel.vue";
import { useUiStore } from "@/stores/ui";

const route = useRoute();
const router = useRouter();
const ui = useUiStore();
const queryClient = useQueryClient();
const fileInput = ref<HTMLInputElement | null>(null);
const files = ref<File[]>([]);
const uploadProgress = ref(0);
const selectedTaskId = computed(() => String(route.query.task || ""));
const documents = useQuery({
  queryKey: ["document-analyses"],
  queryFn: () => api<any[]>("/api/documents"),
  refetchInterval: (query) =>
    (query.state.data || []).some((item: any) => !["review", "failed"].includes(item.stage))
      ? 2500
      : false,
});
const detail = useQuery({
  queryKey: computed(() => ["document-analysis", selectedTaskId.value]),
  queryFn: () => api<any>(`/api/documents/${selectedTaskId.value}`),
  enabled: computed(() => Boolean(selectedTaskId.value)),
  refetchInterval: 2500,
});
const parse = useMutation({
  mutationFn: (items: File[]) => {
    const form = new FormData();
    items.forEach((file) => form.append("files", file));
    return uploadForm<{ task_id: string }>("/api/documents/parse", form, (loaded, total) => {
      uploadProgress.value = total ? Math.round((loaded / total) * 100) : 0;
    });
  },
  onSuccess: async (result) => {
    files.value = [];
    uploadProgress.value = 0;
    await queryClient.invalidateQueries({ queryKey: ["document-analyses"] });
    await router.replace({ path: "/documents", query: { task: result.task_id } });
  },
});
const active = computed(() => detail.data.value?.analysis || {});
const isProcessing = computed(() => Boolean(detail.data.value) && !["review", "failed"].includes(detail.data.value.stage));
function progressValue(value: any) {
  const done = Number(value?.done || 0);
  const total = Number(value?.total || 0);
  return total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
}
const parseProgress = computed(() => detail.data.value?.parse_progress || {});
const understandingProgress = computed(() => detail.data.value?.material_analysis_progress || {});
const currentProgress = computed(() => detail.data.value?.stage === "material_analysis"
  ? progressValue(understandingProgress.value)
  : progressValue(parseProgress.value));
const progressLabel = computed(() => detail.data.value?.stage === "material_analysis" ? "材料理解" : "文档解析");

function chooseFiles() {
  fileInput.value?.click();
}
function onFiles(event: Event) {
  files.value = Array.from((event.target as HTMLInputElement).files || []);
}
function startParsing() {
  if (files.value.length) parse.mutate(files.value);
}
function openDiscussion() {
  if (!selectedTaskId.value) return;
  ui.openAssistant({
    artifact_type: "material_role",
    object_id: String(active.value?.documents?.[0]?.material_id || ""),
    title: "文档理解结果",
  });
}
function selectRun(taskId: string) {
  router.replace({ path: "/documents", query: { task: taskId } });
}
watch(() => route.query.task, () => detail.refetch());
</script>

<template>
  <div class="page-stack document-page">
    <UiPageHeader title="文档解析" />
    <div class="document-layout">
      <section class="document-main">
        <UiPanel class="parse-workbench">
          <div class="workbench-copy">
            <span class="workbench-icon"><AppIcon name="documents" :size="19" /></span>
            <div>
              <h2>上传并理解文档</h2>
              <p>解析结果会保存到材料库，可在后续报告任务中直接复用。</p>
            </div>
          </div>
          <input ref="fileInput" class="visually-hidden" type="file" multiple @change="onFiles" />
          <div class="parse-actions">
            <UiButton @click="chooseFiles"><AppIcon name="plus" :size="16" />选择文档</UiButton>
            <UiButton variant="outline" :disabled="!files.length || parse.isPending.value" :loading="parse.isPending.value" @click="startParsing">
              开始解析
            </UiButton>
          </div>
          <div v-if="files.length" class="selected-files">
            <span v-for="file in files" :key="`${file.name}:${file.size}`"><AppIcon name="document" :size="14" />{{ file.name }}</span>
          </div>
          <div v-if="parse.isPending.value" class="upload-status">正在上传 {{ uploadProgress }}%</div>
          <p v-if="parse.error.value" class="form-error">{{ String(parse.error.value.message || "解析任务创建失败") }}</p>
        </UiPanel>

        <UiPanel v-if="selectedTaskId" class="analysis-result">
          <template v-if="detail.isLoading.value">
            <div class="loading-line"></div>
          </template>
          <template v-else-if="detail.data.value">
            <div class="result-head">
              <div>
                <small>{{ isProcessing ? "正在处理" : detail.data.value.stage === "failed" ? "处理异常" : "解析完成" }}</small>
                <h2>{{ detail.data.value.theme }}</h2>
              </div>
              <UiButton v-if="!isProcessing && detail.data.value.stage !== 'failed'" variant="outline" @click="openDiscussion">与助手讨论</UiButton>
            </div>
            <p v-if="isProcessing" class="processing-copy">正在解析文件并形成材料理解。已完成的内容会在此处保留。</p>
            <div v-if="isProcessing" class="processing-progress">
              <div class="processing-progress__head"><span>{{ progressLabel }}</span><b>{{ currentProgress }}%</b></div>
              <div class="progress-track"><i :style="{ width: `${currentProgress}%` }"></i></div>
              <div class="processing-progress__steps">
                <span :class="{ active: detail.data.value.stage === 'parsing', done: detail.data.value.stage === 'material_analysis' }">解析材料 {{ parseProgress.done || 0 }}/{{ parseProgress.total || detail.data.value.material_ids?.length || "—" }}</span>
                <span :class="{ active: detail.data.value.stage === 'material_analysis' }">形成理解 {{ understandingProgress.done || 0 }}/{{ understandingProgress.total || detail.data.value.material_ids?.length || "—" }}</span>
              </div>
            </div>
            <p v-else-if="detail.data.value.error" class="form-error">{{ detail.data.value.error }}</p>
            <template v-else>
              <section class="summary-block">
                <h3>摘要</h3><p>{{ active.summary || "尚未形成摘要。" }}</p>
              </section>
              <section v-for="document in active.documents || []" :key="document.material_id" class="document-result">
                <div class="document-title"><AppIcon name="document" :size="16" /><div><small>{{ document.doc_type || "已解析材料" }}</small><h3>{{ document.filename }}</h3></div></div>
                <p>{{ document.summary || document.title }}</p>
                <div v-if="document.outline?.length" class="outline-block"><b>提纲</b><ol><li v-for="item in document.outline" :key="item">{{ item }}</li></ol></div>
                <div v-if="document.key_points?.length" class="key-points"><b>关键信息</b><ul><li v-for="item in document.key_points" :key="item">{{ item }}</li></ul></div>
              </section>
            </template>
          </template>
        </UiPanel>
      </section>
      <aside class="document-history">
        <h2>最近解析</h2>
        <button v-for="item in documents.data.value || []" :key="item.task_id" class="history-item" :class="{ active: item.task_id === selectedTaskId }" @click="selectRun(item.task_id)">
          <span><AppIcon name="document" :size="15" /></span><div><b>{{ item.theme }}</b><small>{{ item.material_count }} 份材料 · {{ item.stage === 'review' ? '已完成' : item.stage === 'failed' ? '异常' : '处理中' }}</small></div>
        </button>
        <p v-if="!documents.isLoading.value && !(documents.data.value || []).length" class="quiet-empty">尚无文档解析记录</p>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.document-layout { display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: 28px; align-items: start; }
.document-main { display: grid; gap: 20px; min-width: 0; }
.parse-workbench { padding: 24px; }
.workbench-copy, .result-head, .document-title { display: flex; align-items: center; gap: 13px; }
.workbench-copy h2, .result-head h2, .document-history h2 { margin: 0; font-size: 16px; }
.workbench-copy p, .processing-copy, .summary-block p, .document-result > p { color: var(--muted-foreground); line-height: 1.7; }
.workbench-icon { display: grid; place-items: center; width: 38px; height: 38px; color: var(--accent); background: var(--primary-soft); border: 1px solid color-mix(in srgb, var(--primary) 32%, var(--border)); border-radius: 8px; }
.parse-actions { display: flex; gap: 10px; margin-top: 20px; }
.selected-files { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.selected-files span { display: inline-flex; align-items: center; gap: 6px; max-width: 100%; padding: 6px 9px; color: var(--muted-foreground); background: var(--muted); border: 1px solid var(--border); border-radius: var(--radius-control); font-size: 12px; }
.upload-status, .form-error { margin: 14px 0 0; font-size: 12px; color: var(--muted-foreground); }.form-error { color: var(--destructive); }
.processing-progress { margin-top: 18px; padding: 14px 0 4px; border-top: 1px solid var(--border); }.processing-progress__head, .processing-progress__steps { display: flex; justify-content: space-between; gap: 12px; }.processing-progress__head { color: var(--muted-foreground); font-size: 12px; }.processing-progress__head b { color: var(--foreground); font-weight: 600; }.progress-track { height: 5px; margin-top: 9px; overflow: hidden; background: var(--muted); border-radius: 99px; }.progress-track i { display: block; height: 100%; background: var(--primary); border-radius: inherit; transition: width 180ms ease; }.processing-progress__steps { margin-top: 9px; color: var(--subtle-foreground); font-size: 11px; }.processing-progress__steps .active, .processing-progress__steps .done { color: var(--muted-foreground); }
.analysis-result { padding: 24px; }.result-head { justify-content: space-between; align-items: flex-start; gap: 18px; }.result-head small, .document-title small { color: var(--subtle-foreground); font-size: 11px; }.summary-block { margin-top: 22px; padding: 16px 0; border-block: 1px solid var(--border); }.summary-block h3, .document-result h3 { margin: 0; font-size: 14px; }.summary-block p { margin: 8px 0 0; }.document-result { padding: 20px 0; border-bottom: 1px solid var(--border); }.document-result > p { margin: 12px 0; }.outline-block, .key-points { margin-top: 14px; }.outline-block b, .key-points b { font-size: 12px; }.outline-block ol, .key-points ul { margin: 8px 0 0; padding-left: 20px; color: var(--muted-foreground); line-height: 1.75; }
.document-history { padding-top: 4px; }.document-history h2 { margin-bottom: 12px; }.history-item { display: flex; width: 100%; gap: 10px; padding: 12px 10px; border: 1px solid transparent; border-bottom-color: var(--border); background: transparent; color: var(--foreground); text-align: left; }.history-item:hover, .history-item.active { background: var(--surface-hover); border-color: var(--border); border-radius: 8px; }.history-item > span { color: var(--subtle-foreground); }.history-item div { min-width: 0; }.history-item b, .history-item small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.history-item b { font-size: 12px; font-weight: 600; }.history-item small { margin-top: 4px; color: var(--muted-foreground); font-size: 11px; }.visually-hidden { position: absolute; width: 1px; height: 1px; clip: rect(0 0 0 0); overflow: hidden; }
@media (max-width: 980px) { .document-layout { grid-template-columns: 1fr; }.document-history { order: -1; }.history-item { display: inline-flex; width: auto; max-width: 260px; margin-right: 8px; }.document-history { white-space: nowrap; overflow-x: auto; } }
</style>
