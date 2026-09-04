<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useRoute, useRouter, RouterLink } from "vue-router";
import { api } from "@/api/http";
import type { MaterialSummary, TaskSummary } from "@/api/types";
import StatusBadge from "@/components/StatusBadge.vue";
import AppIcon from "@/components/AppIcon.vue";
import { useUiStore } from "@/stores/ui";

const route = useRoute(); const router = useRouter(); const queryClient = useQueryClient();
const ui = useUiStore();
ui.ensureDraftId();
const createOpen = ref(false); const form = ref<HTMLFormElement>(); const error = ref("");
const workflowMode = ref<"automatic" | "collaborative">("automatic");
const directoryReview = ref<"auto" | "required">("auto");
const tasks = useQuery({ queryKey: ["tasks"], queryFn: () => api<TaskSummary[]>("/api/tasks"), refetchInterval: 8_000 });
const materials = useQuery({ queryKey: ["materials"], queryFn: () => api<MaterialSummary[]>("/api/materials") });
const templates = useQuery({ queryKey: ["templates"], queryFn: () => api<any[]>("/api/style/variants") });
const recent = computed(() => (tasks.data.value || []).slice(0, 6));
const waiting = computed(() => (tasks.data.value || []).filter(t => t.stage === "review"));
const finished = computed(() => (tasks.data.value || []).filter(t => t.stage === "done" || t.stage === "review"));
watch(() => route.query.create, value => { if (value) createOpen.value = true; }, { immediate: true });
watch(
  [() => tasks.isSuccess.value, () => tasks.data.value?.length],
  ([ready, count]) => { if (ready && count === 0) createOpen.value = true; },
  { immediate: true },
);
function closeCreate() {
  createOpen.value = false;
  const query = { ...route.query };
  delete query.create;
  router.replace({ query });
}

const createTask = useMutation({
  mutationFn: async () => {
    if (!form.value) throw new Error("表单尚未就绪");
    const data = new FormData(form.value);
    return api<{ task_id: string }>("/api/tasks", { method: "POST", body: data });
  },
  onSuccess: async result => { await queryClient.invalidateQueries({ queryKey: ["tasks"] }); router.push(`/tasks/${result.task_id}`); },
  onError: e => { error.value = e instanceof Error ? e.message : "创建失败"; },
});
</script>
<template>
  <div class="page-stack dashboard">
    <header class="page-header"><div><h1>工作台</h1></div><button class="btn primary header-create" type="button" @click="createOpen ? closeCreate() : createOpen = true"><AppIcon :name="createOpen ? 'close' : 'plus'" :size="16" />{{ createOpen ? '收起创建' : '新建任务' }}</button></header>
    <section v-if="createOpen" class="surface create-panel">
      <form ref="form" class="create-form" @submit.prevent="createTask.mutate()">
        <h2 class="form-title">新建报告任务</h2>
        <input type="hidden" name="interaction_draft_id" :value="ui.draftId" />
        <label class="field field-wide workflow-mode"><span>模式</span><select v-model="workflowMode"><option value="automatic">自动生成</option><option value="collaborative">协作规划</option></select></label>
        <input type="hidden" name="workflow_mode" :value="workflowMode" />
        <input type="hidden" name="requirement_review" :value="workflowMode === 'collaborative' ? 'required' : 'auto'" />
        <input type="hidden" name="directory_review" :value="workflowMode === 'collaborative' ? directoryReview : 'auto'" />
        <label class="field field-wide"><span>报告主题 <em v-if="workflowMode === 'collaborative'">（可后补）</em></span><input v-model="ui.taskDraft.theme" name="theme" :required="workflowMode === 'automatic'" placeholder="例如：可信执行环境远程证明机制研究综述" /></label>
        <label v-if="workflowMode === 'automatic'" class="field field-wide"><span>报告要求</span><textarea v-model="ui.taskDraft.requirements" name="requirements" rows="4" placeholder="描述用途、重点、篇幅或必须回答的问题。无需配置系统参数。"></textarea></label>
        <details v-else class="field-wide optional-field">
          <summary><span>报告要求</span><small>{{ ui.taskDraft.requirements.trim() ? "已填写" : "可选" }}</small></summary>
          <label class="field"><textarea v-model="ui.taskDraft.requirements" name="requirements" rows="4" placeholder="描述用途、重点、篇幅或必须回答的问题。"></textarea></label>
        </details>
        <label class="field"><span>上传新材料</span><input name="files" type="file" multiple /></label>
        <label class="field"><span>从材料库选择</span><select name="existing_material_ids" multiple size="5"><option v-for="item in materials.data.value || []" :key="item.id" :value="item.id">{{ item.filename }}</option></select></label>
        <label class="field"><span>文档模板</span><select name="variant_id"><option value="">使用默认模板</option><option v-for="item in templates.data.value || []" :key="item.id" :value="item.id">{{ item.name || item.label || `模板 ${item.id}` }}</option></select></label>
        <div v-if="workflowMode === 'collaborative'" class="field-wide collaboration-controls"><button type="button" class="btn tertiary" @click="ui.openAssistant()">讨论需求</button><label><span>目录确认</span><select v-model="directoryReview"><option value="auto">自动继续</option><option value="required">确认后写作</option></select></label></div>
        <div class="submit-row"><span v-if="error" class="error-text">{{ error }}</span><button class="btn primary" :disabled="createTask.isPending.value">{{ createTask.isPending.value ? '正在准备…' : workflowMode === 'automatic' ? '创建并进入任务' : '上传材料并开始' }}</button></div>
      </form>
    </section>
    <section class="metric-strip"><div class="metric"><strong>{{ tasks.data.value?.length || 0 }}</strong><span>全部任务</span></div><div class="metric"><strong>{{ (tasks.data.value || []).filter(t => !['done','review','failed','paused','created','requirement_review','directory_review'].includes(t.stage)).length }}</strong><span>正在运行</span></div><div class="metric"><strong>{{ waiting.length }}</strong><span>待审核报告</span></div><div class="metric"><strong>{{ materials.data.value?.length || 0 }}</strong><span>可复用材料</span></div></section>
    <div class="dashboard-grid">
      <section class="surface section-block recent"><div class="section-head"><h2>最近任务</h2><RouterLink class="btn tertiary" to="/tasks">查看全部</RouterLink></div><div v-if="recent.length" class="data-list"><RouterLink v-for="task in recent" :key="task.task_id" :to="`/tasks/${task.task_id}`" class="data-row task-row"><div><strong>{{ task.theme }}</strong><small>{{ task.created_at || task.task_id }}</small></div><span>{{ task.material_count || 0 }} 份材料</span><StatusBadge :stage="task.stage" /></RouterLink></div><div v-else class="empty"><div><strong>尚无任务</strong>从上方创建第一个报告任务。</div></div></section>
      <aside class="side-stack"><section class="surface section-block"><div class="section-head"><h2>待审核</h2></div><RouterLink v-for="task in waiting.slice(0,4)" :key="task.task_id" :to="task.report_id ? `/reports/${task.report_id}` : `/tasks/${task.task_id}`" class="compact-link"><span>{{ task.theme }}</span><b>进入审核</b></RouterLink><div v-if="!waiting.length" class="quiet-empty">当前没有待审核报告</div></section><section v-if="finished.length" class="surface section-block"><h2>最近完成</h2><RouterLink v-for="task in finished.slice(0,3)" :key="task.task_id" :to="`/tasks/${task.task_id}`" class="compact-link"><span>{{ task.theme }}</span><StatusBadge :stage="task.stage" /></RouterLink></section></aside>
    </div>
  </div>
</template>
<style scoped>
.header-create{display:inline-flex;align-items:center;gap:7px}.create-panel { overflow:hidden; }.create-form { display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:26px; }.form-title{grid-column:1/-1;margin:0 0 4px;font-size:19px}.field-wide,.submit-row { grid-column: 1/-1; }.submit-row { display:flex; align-items:center; justify-content:flex-end; gap:12px; }.dashboard-grid { display:grid; grid-template-columns:minmax(0,1.65fr) minmax(300px,.75fr); gap:24px; }.task-row { grid-template-columns:minmax(0,1fr) 90px auto; }.task-row strong,.task-row small { display:block; }.task-row small { color:var(--color-faint); margin-top:2px; }.side-stack { display:grid; align-content:start; gap:24px; }.compact-link { display:flex; justify-content:space-between; align-items:center; gap:12px; padding:12px 0; border-top:1px solid var(--color-border); }.compact-link span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.compact-link b { color:var(--color-primary); font-size:12px; white-space:nowrap; }.quiet-empty { padding:28px 0 8px; color:var(--color-faint); text-align:center; }
.workflow-mode select{max-width:240px}.optional-field{border:1px solid var(--color-border);border-radius:6px;background:var(--color-surface-soft)}.optional-field summary{display:flex;align-items:center;justify-content:space-between;padding:11px 13px;cursor:pointer;font-size:13px;font-weight:650;list-style:none}.optional-field summary::-webkit-details-marker{display:none}.optional-field summary::after{content:"";width:7px;height:7px;margin-left:10px;border-right:1.5px solid var(--color-muted);border-bottom:1.5px solid var(--color-muted);transform:rotate(45deg) translateY(-2px);transition:transform var(--motion-fast)}.optional-field[open] summary{border-bottom:1px solid var(--color-border)}.optional-field[open] summary::after{transform:rotate(225deg) translate(-1px,-1px)}.optional-field summary small{margin-left:auto;color:var(--color-muted);font-size:12px;font-weight:400}.optional-field .field{display:block;padding:12px 13px}.optional-field textarea{box-sizing:border-box;width:100%}.collaboration-controls{display:flex;align-items:center;justify-content:space-between;padding-top:2px}.collaboration-controls label{display:flex;align-items:center;gap:8px;color:var(--color-muted);font-size:12px}.collaboration-controls select{width:120px;padding:6px 8px}
@media(max-width:980px){.create-panel,.dashboard-grid{grid-template-columns:1fr}.create-form{grid-template-columns:1fr}.field,.field-wide,.submit-row{grid-column:1}}
</style>
