<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useRoute, useRouter, RouterLink } from "vue-router";
import { api } from "@/api/http";
import type { MaterialSummary, TaskSummary } from "@/api/types";
import StatusBadge from "@/components/StatusBadge.vue";

const route = useRoute(); const router = useRouter(); const queryClient = useQueryClient();
const createOpen = ref(false); const form = ref<HTMLFormElement>(); const error = ref("");
const tasks = useQuery({ queryKey: ["tasks"], queryFn: () => api<TaskSummary[]>("/api/tasks"), refetchInterval: 8_000 });
const materials = useQuery({ queryKey: ["materials"], queryFn: () => api<MaterialSummary[]>("/api/materials") });
const templates = useQuery({ queryKey: ["templates"], queryFn: () => api<any[]>("/api/style/variants") });
const recent = computed(() => (tasks.data.value || []).slice(0, 6));
const waiting = computed(() => (tasks.data.value || []).filter(t => t.stage === "review"));
const finished = computed(() => (tasks.data.value || []).filter(t => t.stage === "done" || t.stage === "review"));
watch(() => route.query.create, value => { if (value) createOpen.value = true; }, { immediate: true });
onMounted(() => { if (!(tasks.data.value || []).length) createOpen.value = true; });

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
    <header class="page-header"><div><h1>工作台</h1><p>从材料进入分析，以可核验的证据完成报告。</p></div></header>
    <section v-if="createOpen" class="surface create-panel">
      <div class="create-intro"><div class="intro-heading"><h2>新建报告任务</h2><button type="button" @click="closeCreate">收起</button></div><p>提供业务目标、材料和模板，系统将自动理解材料并规划报告。</p></div>
      <form ref="form" class="create-form" @submit.prevent="createTask.mutate()">
        <label class="field field-wide"><span>报告主题</span><input name="theme" required placeholder="例如：可信执行环境远程证明机制研究综述" /></label>
        <label class="field field-wide"><span>报告要求</span><textarea name="requirements" rows="4" placeholder="描述用途、重点、篇幅或必须回答的问题。无需配置系统参数。"></textarea></label>
        <label class="field"><span>上传新材料</span><input name="files" type="file" multiple /></label>
        <label class="field"><span>从材料库选择</span><select name="existing_material_ids" multiple size="5"><option v-for="item in materials.data.value || []" :key="item.id" :value="item.id">{{ item.filename }}</option></select></label>
        <label class="field"><span>文档模板</span><select name="variant_id"><option value="">使用默认模板</option><option v-for="item in templates.data.value || []" :key="item.id" :value="item.id">{{ item.name || item.label || `模板 ${item.id}` }}</option></select></label>
        <div class="submit-row"><span v-if="error" class="error-text">{{ error }}</span><button class="btn primary" :disabled="createTask.isPending.value">{{ createTask.isPending.value ? '正在创建…' : '创建并进入任务' }}</button></div>
      </form>
    </section>
    <section class="metric-strip"><div class="metric"><strong>{{ tasks.data.value?.length || 0 }}</strong><span>全部任务</span></div><div class="metric"><strong>{{ (tasks.data.value || []).filter(t => !['done','review','failed','paused','created'].includes(t.stage)).length }}</strong><span>正在运行</span></div><div class="metric"><strong>{{ waiting.length }}</strong><span>待审核报告</span></div><div class="metric"><strong>{{ materials.data.value?.length || 0 }}</strong><span>可复用材料</span></div></section>
    <div class="dashboard-grid">
      <section class="surface section-block recent"><div class="section-head"><div><h2>最近任务</h2><p class="muted">继续处理最近的报告任务</p></div><RouterLink class="btn tertiary" to="/tasks">查看全部</RouterLink></div><div v-if="recent.length" class="data-list"><RouterLink v-for="task in recent" :key="task.task_id" :to="`/tasks/${task.task_id}`" class="data-row task-row"><div><strong>{{ task.theme }}</strong><small>{{ task.created_at || task.task_id }}</small></div><span>{{ task.material_count || 0 }} 份材料</span><StatusBadge :stage="task.stage" /></RouterLink></div><div v-else class="empty"><div><strong>尚无任务</strong>从上方创建第一个报告任务。</div></div></section>
      <aside class="side-stack"><section class="surface section-block"><div class="section-head"><div><h2>待审核</h2><p class="muted">已形成可阅读草稿</p></div></div><RouterLink v-for="task in waiting.slice(0,4)" :key="task.task_id" :to="task.report_id ? `/reports/${task.report_id}` : `/tasks/${task.task_id}`" class="compact-link"><span>{{ task.theme }}</span><b>进入审核</b></RouterLink><div v-if="!waiting.length" class="quiet-empty">当前没有待审核报告</div></section><section v-if="finished.length" class="surface section-block"><h2>最近完成</h2><RouterLink v-for="task in finished.slice(0,3)" :key="task.task_id" :to="`/tasks/${task.task_id}`" class="compact-link"><span>{{ task.theme }}</span><StatusBadge :stage="task.stage" /></RouterLink></section></aside>
    </div>
  </div>
</template>
<style scoped>
.create-panel { display: grid; grid-template-columns: 280px 1fr; overflow: hidden; }.create-intro { display:flex; flex-direction:column; justify-content:center; padding:28px; color:#fff; background:#294f79; }.intro-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.intro-heading button{padding:4px 0;border:0;background:transparent;color:#dbe7f4;font-size:12px}.intro-heading button:hover{color:#fff}.create-intro h2 { margin:0; font-size:20px; }.create-intro p { margin:10px 0 0;color:#dbe7f4;line-height:1.7; }.create-form { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 26px; }.field-wide,.submit-row { grid-column: 1/-1; }.submit-row { display:flex; align-items:center; justify-content:flex-end; gap:12px; }.dashboard-grid { display:grid; grid-template-columns:minmax(0,1.65fr) minmax(300px,.75fr); gap:24px; }.task-row { grid-template-columns:minmax(0,1fr) 90px auto; }.task-row strong,.task-row small { display:block; }.task-row small { color:var(--color-faint); margin-top:2px; }.side-stack { display:grid; align-content:start; gap:24px; }.compact-link { display:flex; justify-content:space-between; align-items:center; gap:12px; padding:12px 0; border-top:1px solid var(--color-border); }.compact-link span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.compact-link b { color:var(--color-primary); font-size:12px; white-space:nowrap; }.quiet-empty { padding:28px 0 8px; color:var(--color-faint); text-align:center; }
@media(max-width:980px){.create-panel,.dashboard-grid{grid-template-columns:1fr}.create-form{grid-template-columns:1fr}.field,.field-wide,.submit-row{grid-column:1}}
</style>
