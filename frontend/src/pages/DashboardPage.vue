<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useRoute, useRouter, RouterLink } from "vue-router";
import { api } from "@/api/http";
import type { MaterialSummary, TaskSummary } from "@/api/types";
import StatusBadge from "@/components/StatusBadge.vue";
import AppIcon from "@/components/AppIcon.vue";
import UiButton from "@/components/ui/UiButton.vue";
import UiEmptyState from "@/components/ui/UiEmptyState.vue";
import UiMetricStrip from "@/components/ui/UiMetricStrip.vue";
import UiPanel from "@/components/ui/UiPanel.vue";
import UiPageHeader from "@/components/ui/UiPageHeader.vue";
import UiSectionHeader from "@/components/ui/UiSectionHeader.vue";
import { useUiStore } from "@/stores/ui";

const route = useRoute();
const router = useRouter();
const queryClient = useQueryClient();
const ui = useUiStore();
ui.ensureDraftId();
const createOpen = ref(false);
const form = ref<HTMLFormElement>();
const fileInput = ref<HTMLInputElement>();
const selectedFiles = ref<string[]>([]);
const selectedLibraryIds = ref<string[]>([]);
const selectedTemplateId = ref("");
const libraryOpen = ref(false);
const error = ref("");
const workflowMode = ref<"automatic" | "collaborative">("automatic");
const directoryReview = ref<"auto" | "required">("auto");
const tasks = useQuery({
  queryKey: ["tasks"],
  queryFn: () => api<TaskSummary[]>("/api/tasks"),
  refetchInterval: 8_000,
});
const materials = useQuery({
  queryKey: ["materials"],
  queryFn: () => api<MaterialSummary[]>("/api/materials"),
});
const templates = useQuery({
  queryKey: ["templates"],
  queryFn: () => api<any[]>("/api/style/variants"),
});
const recent = computed(() => (tasks.data.value || []).slice(0, 6));
const waiting = computed(() =>
  (tasks.data.value || []).filter((t) => t.stage === "review"),
);
const finished = computed(() =>
  (tasks.data.value || []).filter(
    (t) => t.stage === "done" || t.stage === "review",
  ),
);
const configuredMaterialCount = computed(
  () => selectedFiles.value.length + selectedLibraryIds.value.length,
);
const selectedTemplateLabel = computed(() => {
  const selected = (templates.data.value || []).find(
    (item) => String(item.id) === selectedTemplateId.value,
  );
  return selected?.name || selected?.label || "默认模板";
});
const metrics = computed(() => [
  {
    label: "全部任务",
    value: tasks.data.value?.length || 0,
    icon: "dashboard",
  },
  {
    label: "正在运行",
    value: (tasks.data.value || []).filter(
      (t) =>
        ![
          "done",
          "review",
          "failed",
          "paused",
          "created",
          "requirement_review",
          "directory_review",
        ].includes(t.stage),
    ).length,
    icon: "activity",
    tone: "info" as const,
  },
  {
    label: "待审核报告",
    value: waiting.value.length,
    icon: "review",
    tone: "warning" as const,
  },
  {
    label: "可复用材料",
    value: materials.data.value?.length || 0,
    icon: "files",
  },
]);
watch(
  () => route.query.create,
  (value) => {
    if (value) createOpen.value = true;
  },
  { immediate: true },
);
watch(
  [() => tasks.isSuccess.value, () => tasks.data.value?.length],
  ([ready, count]) => {
    if (ready && count === 0) createOpen.value = true;
  },
  { immediate: true },
);
function closeCreate() {
  createOpen.value = false;
  const query = { ...route.query };
  delete query.create;
  router.replace({ query });
}
function openFilePicker() {
  fileInput.value?.click();
}
function onFilesSelected(event: Event) {
  const input = event.target as HTMLInputElement;
  selectedFiles.value = Array.from(input.files || []).map((file) => file.name);
}

const createTask = useMutation({
  mutationFn: async () => {
    if (!form.value) throw new Error("表单尚未就绪");
    const data = new FormData(form.value);
    return api<{ task_id: string }>("/api/tasks", {
      method: "POST",
      body: data,
    });
  },
  onSuccess: async (result) => {
    await queryClient.invalidateQueries({ queryKey: ["tasks"] });
    router.push(`/tasks/${result.task_id}`);
  },
  onError: (e) => {
    error.value = e instanceof Error ? e.message : "创建失败";
  },
});
</script>
<template>
  <div class="page-stack dashboard">
    <UiPageHeader title="工作台" description="从材料准备到报告生成，在一个工作区完成整编任务。" featured>
      <template #actions
        ><UiButton
          :variant="createOpen ? 'outline' : 'default'"
          @click="createOpen ? closeCreate() : (createOpen = true)"
          ><AppIcon :name="createOpen ? 'close' : 'plus'" :size="16" />{{
            createOpen ? "收起创建" : "新建任务"
          }}</UiButton
        ></template
      >
    </UiPageHeader>
    <UiPanel
      v-if="createOpen"
      class="create-panel"
      :padded="false"
      variant="raised"
    >
      <form
        ref="form"
        class="create-form"
        @submit.prevent="createTask.mutate()"
      >
        <div class="form-heading">
          <div><h2>新建任务</h2><p>选择材料、模板与工作方式，系统会自动完成后续整编。</p></div>
          <span class="workspace-state">{{ workflowMode === "automatic" ? "直接生成" : "协作规划" }}</span>
        </div>
        <input type="hidden" name="interaction_draft_id" :value="ui.draftId" />
        <section class="setup-section material-section">
          <div class="step-heading">
            <span class="step-number">1</span>
            <div><h3>材料来源</h3><p>上传新文件，或从材料库选择已有材料。</p></div>
          </div>
          <div class="source-actions">
            <UiButton type="button" @click="openFilePicker"><AppIcon name="upload" :size="16" />上传新材料</UiButton>
            <UiButton type="button" variant="outline" @click="libraryOpen = !libraryOpen"><AppIcon name="files" :size="16" />从材料库选择</UiButton>
          </div>
          <input ref="fileInput" class="visually-hidden" name="files" type="file" multiple @change="onFilesSelected" />
          <div v-if="selectedFiles.length" class="file-chips" aria-label="已上传材料">
            <span v-for="name in selectedFiles" :key="name" class="file-chip"><AppIcon name="file" :size="14" />{{ name }}</span>
          </div>
          <details class="library-picker" :open="libraryOpen">
            <summary>材料库中的已有材料 <small>{{ materials.data.value?.length || 0 }} 份可选</small></summary>
            <select v-model="selectedLibraryIds" name="existing_material_ids" multiple size="6">
              <option v-for="item in materials.data.value || []" :key="item.id" :value="String(item.id)">{{ item.filename }}</option>
            </select>
          </details>
        </section>
        <section class="setup-section template-section">
          <div class="step-heading">
            <span class="step-number">2</span>
            <div><h3>文档模板</h3><p>选择导出报告使用的版式与样式。</p></div>
          </div>
          <label class="field template-select"
            ><span>使用模板</span
            ><select v-model="selectedTemplateId" name="variant_id">
              <option value="">使用默认模板</option>
              <option v-for="item in templates.data.value || []" :key="item.id" :value="String(item.id)">{{ item.name || item.label || `模板 ${item.id}` }}</option>
            </select>
          </label>
        </section>
        <section class="setup-section flow-section">
          <div class="step-heading">
            <span class="step-number">3</span>
            <div><h3>写作流程</h3><p>选择任务如何从材料理解进入报告写作。</p></div>
          </div>
          <div class="workflow-mode">
          <span class="field-label">工作方式</span>
          <div class="mode-switch" role="radiogroup" aria-label="工作方式">
            <button
              type="button"
              :class="{ active: workflowMode === 'automatic' }"
              @click="workflowMode = 'automatic'"
            >
              <b>直接生成</b><small>按已有主题直接运行</small></button
            ><button
              type="button"
              :class="{ active: workflowMode === 'collaborative' }"
              @click="workflowMode = 'collaborative'"
            >
              <b>协作规划</b><small>材料解析后共同确定方向</small>
            </button>
          </div>
          </div>
        </section>
        <input type="hidden" name="workflow_mode" :value="workflowMode" />
        <input
          type="hidden"
          name="requirement_review"
          :value="workflowMode === 'collaborative' ? 'required' : 'auto'"
        />
        <input
          type="hidden"
          name="directory_review"
          :value="workflowMode === 'collaborative' ? directoryReview : 'auto'"
        />
        <label v-if="workflowMode === 'automatic'" class="field task-brief-field theme-field"
          ><span>报告主题</span
          ><input
            v-model="ui.taskDraft.theme"
            name="theme"
            required
            placeholder="例如：可信执行环境远程证明机制研究综述"
        /></label>
        <details v-else class="optional-field task-brief-field theme-field">
          <summary>
            <span>报告主题</span
            ><small>{{ ui.taskDraft.theme.trim() ? "已填写" : "可选" }}</small>
          </summary>
          <label class="field">
            <input
              v-model="ui.taskDraft.theme"
              name="theme"
              placeholder="如果已有方向，可以先填写报告主题"
            />
          </label>
        </details>
        <label v-if="workflowMode === 'automatic'" class="field task-brief-field requirements-field"
          ><span>报告要求</span
          ><textarea
            v-model="ui.taskDraft.requirements"
            name="requirements"
            rows="4"
            placeholder="描述用途、重点、篇幅或必须回答的问题。无需配置系统参数。"
          ></textarea>
        </label>
        <details v-else class="optional-field task-brief-field requirements-field">
          <summary>
            <span>报告要求</span
            ><small>{{
              ui.taskDraft.requirements.trim() ? "已填写" : "可选"
            }}</small>
          </summary>
          <label class="field">
            <textarea
              v-model="ui.taskDraft.requirements"
              name="requirements"
              rows="4"
              placeholder="描述用途、重点、篇幅或必须回答的问题。"
            ></textarea>
          </label>
        </details>
        <div
          v-if="workflowMode === 'collaborative'"
          class="collaboration-controls"
        >
          <UiButton
            type="button"
            variant="ghost"
            size="sm"
            @click="ui.openAssistant()"
            >讨论需求</UiButton
          ><label
            ><span>目录生成后</span
            ><select v-model="directoryReview">
              <option value="auto">自动继续写作</option>
              <option value="required">等待我确认</option>
            </select></label
          >
        </div>
        <div class="submit-row">
          <span class="selection-summary"><AppIcon name="files" :size="15" />已配置 <b>{{ configuredMaterialCount }}</b> 份材料<i></i>{{ selectedTemplateLabel }}</span>
          <span v-if="error" class="error-text">{{ error }}</span
          ><UiButton type="submit" :loading="createTask.isPending.value">{{
            workflowMode === "automatic" ? "创建并进入任务" : "上传材料并开始"
          }}</UiButton>
        </div>
      </form>
    </UiPanel>
    <UiMetricStrip :items="metrics" />
    <div class="dashboard-grid">
      <UiPanel class="section-block recent"
        ><UiSectionHeader title="最近任务"
          ><template #actions
            ><RouterLink class="btn tertiary" to="/tasks"
              >查看全部</RouterLink
            ></template
          ></UiSectionHeader
        >
        <div v-if="recent.length" class="data-list">
          <RouterLink
            v-for="task in recent"
            :key="task.task_id"
            :to="`/tasks/${task.task_id}`"
            class="data-row task-row"
            ><div>
              <strong>{{ task.theme }}</strong
              ><small>{{ task.created_at || task.task_id }}</small>
            </div>
            <span>{{ task.material_count || 0 }} 份材料</span
            ><StatusBadge :stage="task.stage"
          /></RouterLink>
        </div>
        <UiEmptyState
          v-else
          title="尚无任务"
          description="创建第一个报告任务后，进度和审阅项会出现在这里。"
      /></UiPanel>
      <aside class="side-stack">
        <section class="surface section-block">
          <div class="section-head"><h2>待审核</h2></div>
          <RouterLink
            v-for="task in waiting.slice(0, 4)"
            :key="task.task_id"
            :to="
              task.report_id
                ? `/reports/${task.report_id}`
                : `/tasks/${task.task_id}`
            "
            class="compact-link"
            ><span>{{ task.theme }}</span
            ><b>进入审核</b></RouterLink
          >
          <div v-if="!waiting.length" class="quiet-empty">
            当前没有待审核报告
          </div>
        </section>
        <section v-if="finished.length" class="surface section-block">
          <h2>最近完成</h2>
          <RouterLink
            v-for="task in finished.slice(0, 3)"
            :key="task.task_id"
            :to="`/tasks/${task.task_id}`"
            class="compact-link"
            ><span>{{ task.theme }}</span
            ><StatusBadge :stage="task.stage"
          /></RouterLink>
        </section>
      </aside>
    </div>
  </div>
</template>
<style scoped>
.eyebrow,
.section-kicker {
  display: block;
  color: var(--primary);
  font-size: 10px;
  font-weight: 750;
  letter-spacing: 0.13em;
}
.create-panel {
  overflow: hidden;
  background: var(--card);
  border-color: var(--border);
  border-radius: var(--radius-feature);
  box-shadow: inset 0 1px 0 var(--surface-highlight);
}
.create-form {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 0;
}
.setup-section {
  display: grid;
  gap: 12px;
  padding: 20px 24px;
  border-top: 1px solid var(--border);
  background: transparent;
}
.material-section {
  grid-column: 1 / 8;
  grid-row: span 2;
  order: 3;
}
.template-section,
.flow-section,
.collaboration-controls {
  grid-column: 8 / -1;
  border-left: 1px solid var(--border);
}
.template-section { order: 3; }
.flow-section { order: 4; }
.step-heading {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.step-number {
  display: grid;
  flex: 0 0 26px;
  place-items: center;
  width: 26px;
  height: 26px;
  border: 1px solid var(--border-strong);
  border-radius: 50%;
  color: var(--primary);
  background: var(--muted);
  font-size: 12px;
  font-weight: 650;
}
.step-heading h3 {
  margin: 1px 0 3px;
  font-size: 16px;
  font-weight: 600;
}
.step-heading p {
  margin: 0;
  color: var(--muted-foreground);
  font-size: 12px;
}
.source-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
.file-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.file-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 32px;
  max-width: 220px;
  padding: 0 10px;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--muted-foreground);
  background: var(--muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: border-color var(--motion-fast), background var(--motion-fast);
}
.file-chip:hover {
  border-color: var(--border-strong);
  background: var(--surface-hover);
}
.library-picker {
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--surface-raised);
  transition: border-color var(--motion-fast), background var(--motion-fast);
}
.library-picker:hover {
  border-color: var(--border-strong);
  background: var(--surface-hover);
}
.library-picker summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 38px;
  padding: 0 12px;
  cursor: pointer;
  color: var(--muted-foreground);
  font-size: 12px;
  font-weight: 600;
  list-style: none;
}
.library-picker summary::-webkit-details-marker {
  display: none;
}
.library-picker summary::after {
  content: "⌄";
  color: var(--subtle-foreground);
  font-size: 15px;
}
.library-picker[open] summary {
  margin-bottom: 10px;
  border-bottom: 1px solid var(--border);
  color: var(--foreground);
}
.library-picker[open] summary::after {
  transform: rotate(180deg);
}
.library-picker summary small {
  margin-left: auto;
  margin-right: 10px;
  color: var(--subtle-foreground);
  font-weight: 500;
}
.library-picker select {
  margin: 0 12px 12px;
  width: calc(100% - 24px);
}
.template-select {
  max-width: none;
}
.selection-summary {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin-right: auto;
  color: var(--muted-foreground);
  font-size: 12px;
}
.selection-summary b {
  color: var(--foreground);
  font-weight: 600;
}
.selection-summary i {
  width: 1px;
  height: 13px;
  margin: 0 2px;
  background: var(--border-strong);
}
.form-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  grid-column: 1/-1;
  padding: 22px 24px 18px;
  border-bottom: 1px solid var(--border);
}
.form-heading h2 {
  margin: 0 0 3px;
  font-size: 19px;
  letter-spacing: -0.025em;
}
.form-heading p {
  margin: 0;
  color: var(--subtle-foreground);
  font-size: 12px;
}
.workspace-state {
  flex: 0 0 auto;
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--muted-foreground);
  background: var(--muted);
  font-size: 11px;
}
.task-brief-field {
  order: 1;
  min-width: 0;
  margin: 8px 0 20px;
}
.theme-field {
  grid-column: 1 / 7;
  margin-left: 28px;
  margin-right: 8px;
}
.requirements-field {
  grid-column: 7 / -1;
  margin-left: 8px;
  margin-right: 28px;
}
.field-label {
  display: block;
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 650;
}
.mode-switch {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0;
  overflow: hidden;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-control);
  background: var(--surface-raised);
}
.mode-switch button {
  display: grid;
  gap: 2px;
  min-height: 58px;
  padding: 10px 12px;
  text-align: center;
  color: var(--muted-foreground);
  border: 0;
  border-radius: 0;
  background: transparent;
  transition: color var(--motion-fast), background var(--motion-fast);
}
.mode-switch button + button {
  border-left: 1px solid var(--border);
}
.mode-switch button:hover {
  background: var(--surface-hover);
}
.mode-switch button.active {
  color: var(--foreground);
  background: var(--surface-hover);
  box-shadow: inset 0 -2px var(--primary);
}
.mode-switch button.active b,
.mode-switch button.active small {
  color: inherit;
}
.mode-switch b {
  font-size: 13px;
}
.mode-switch small {
  font-size: 12px;
}
.optional-field {
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--surface-raised);
}
.optional-field summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 11px 13px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 650;
  list-style: none;
}
.optional-field summary::-webkit-details-marker {
  display: none;
}
.optional-field summary::after {
  content: "";
  width: 7px;
  height: 7px;
  margin-left: 10px;
  border-right: 1.5px solid var(--color-muted);
  border-bottom: 1.5px solid var(--color-muted);
  transform: rotate(45deg) translateY(-2px);
  transition: transform var(--motion-fast);
}
.optional-field[open] summary {
  border-bottom: 1px solid var(--border);
}
.optional-field[open] summary::after {
  transform: rotate(225deg) translate(-1px, -1px);
}
.optional-field summary small {
  margin-left: auto;
  color: var(--muted-foreground);
  font-size: 12px;
  font-weight: 500;
}
.optional-field .field {
  display: block;
  padding: 12px 13px;
}
.optional-field textarea {
  box-sizing: border-box;
  width: 100%;
}
.collaboration-controls {
  order: 5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: 12px 24px 20px;
  border-top: 1px solid var(--border);
}
.collaboration-controls label {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted-foreground);
  font-size: 12px;
}
.collaboration-controls select {
  width: 140px;
  padding: 6px 8px;
}
.submit-row {
  grid-column: 1/-1;
  order: 6;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  min-height: 68px;
  padding: 14px 28px;
  border-top: 1px solid var(--border);
  background: var(--muted);
}
.dashboard-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.65fr) minmax(300px, 0.75fr);
  gap: 24px;
}
.dashboard-grid > .surface {
  box-shadow: inset 0 1px 0 var(--surface-highlight);
}
.section-kicker {
  margin-bottom: 3px;
  font-size: 9px;
}
.task-row {
  grid-template-columns: minmax(0, 1fr) 90px auto;
}
.task-row strong,
.task-row small {
  display: block;
}
.task-row strong {
  font-weight: 650;
}
.task-row small {
  color: var(--subtle-foreground);
  margin-top: 2px;
  font-size: 12px;
}
.side-stack {
  display: grid;
  align-content: start;
  gap: 24px;
}
.compact-link {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 13px 0;
  border-top: 1px solid var(--border);
  transition: background var(--motion-fast), color var(--motion-fast);
}
.compact-link:hover {
  background: color-mix(in srgb, var(--primary-soft) 48%, var(--surface-hover));
}
.compact-link span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.compact-link b {
  color: var(--primary);
  font-size: 12px;
  white-space: nowrap;
}
.quiet-empty {
  padding: 28px 0 8px;
  color: var(--subtle-foreground);
  text-align: center;
}
@media (max-width: 980px) {
  .dashboard-grid {
    grid-template-columns: 1fr;
  }
  .create-form {
    grid-template-columns: 1fr;
  }
  .task-brief-field,
  .material-section,
  .template-section,
  .flow-section,
  .collaboration-controls,
  .submit-row {
    grid-column: 1;
    grid-row: auto;
    margin-inline: 0;
  }
  .template-section,
  .flow-section,
  .collaboration-controls {
    border-left: 0;
  }
}
@media (max-width: 620px) {
  .form-heading {
    align-items: start;
    flex-direction: column;
  }
  .mode-switch {
    grid-template-columns: 1fr;
  }
  .create-form {
    display: grid;
    grid-template-columns: 1fr;
  }
  .form-heading,
  .setup-section,
  .submit-row {
    padding-inline: 20px;
  }
  .task-brief-field {
    margin-inline: 20px;
  }
  .collaboration-controls {
    align-items: flex-start;
    gap: 14px;
    flex-direction: column;
  }
}
</style>
