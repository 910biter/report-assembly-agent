<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useRoute, useRouter, RouterLink } from "vue-router";
import { api, jsonInit } from "@/api/http";
import type { TaskSummary } from "@/api/types";
import StatusBadge from "@/components/StatusBadge.vue";
import MaterialComparisonWorkspace from "@/components/MaterialComparisonWorkspace.vue";
import UiPageHeader from "@/components/ui/UiPageHeader.vue";
import { stageGroups, stageMeta } from "@/domain/workflowStages";
import { useUiStore } from "@/stores/ui";
const GraphNetwork = defineAsyncComponent(
  () => import("@/components/GraphNetwork.vue"),
);
const route = useRoute();
const router = useRouter();
const ui = useUiStore();
const qc = useQueryClient();
const taskId = String(route.params.taskId);
function refreshAfterAssistantUpdate(event: Event) {
  const detail = (event as CustomEvent).detail || {};
  if (detail.taskId && String(detail.taskId) !== taskId) return;
  qc.invalidateQueries({ queryKey: ["task", taskId] });
  qc.invalidateQueries({ queryKey: ["task-analysis", taskId] });
  qc.invalidateQueries({ queryKey: ["task-materials", taskId] });
  qc.invalidateQueries({ queryKey: ["task-graph", taskId] });
  qc.invalidateQueries({ queryKey: ["report-versions"] });
}
onMounted(() => window.addEventListener("ira:task-artifact-updated", refreshAfterAssistantUpdate));
onBeforeUnmount(() => window.removeEventListener("ira:task-artifact-updated", refreshAfterAssistantUpdate));
const requestedTab = String(route.query.tab || "overview");
const active = ref(requestedTab === "runtime" ? "materials" : requestedTab);
const workspacePane = ref(requestedTab === "runtime" ? "process" : "materials");
const analysisType = ref("facts");
const selectedGraphEdge = ref<any>(null);
const graphBuildError = ref("");
const task = useQuery({
  queryKey: ["task", taskId],
  queryFn: () => api<TaskSummary>(`/api/tasks/${taskId}`),
  refetchInterval: (q) =>
    ["review", "done", "failed", "paused"].includes(String(q.state.data?.stage))
      ? 15000
      : 4000,
});
const isComparison = computed(
  () => task.data.value?.run_mode === "material_comparison",
);
watch(
  () => task.data.value?.run_mode,
  (mode) => {
    if (mode === "material_comparison" && !route.query.tab)
      active.value = "comparison";
  },
  { immediate: true },
);
const materials = useQuery({
  queryKey: ["task-materials", taskId],
  queryFn: () => api<any[]>(`/api/tasks/${taskId}/materials`),
  enabled: computed(() => ["materials", "overview"].includes(active.value)),
  staleTime: 30000,
});
const analysis = useQuery({
  queryKey: ["task-analysis", taskId],
  queryFn: () => api<any>(`/api/tasks/${taskId}/analysis`),
  enabled: computed(() => active.value === "analysis"),
  staleTime: 30000,
});
const graph = useQuery({
  queryKey: ["task-graph", taskId],
  queryFn: () => api<any>(`/api/tasks/${taskId}/graph`),
  enabled: computed(
    () => active.value === "analysis" && analysisType.value === "graph",
  ),
  staleTime: 30000,
  refetchInterval: (q) => (q.state.data?.build_active ? 4000 : false),
});
const graphChanges = useQuery({
  queryKey: ["task-graph-changes", taskId],
  queryFn: () => api<any>(`/api/tasks/${taskId}/graph/changesets`),
  enabled: computed(
    () => active.value === "analysis" && analysisType.value === "graph",
  ),
  staleTime: 30000,
});
const versions = useQuery({
  queryKey: ["report-versions", computed(() => task.data.value?.report_id)],
  queryFn: () =>
    api<any>(`/api/reports/${task.data.value?.report_id}/versions`),
  enabled: computed(
    () => active.value === "versions" && Boolean(task.data.value?.report_id),
  ),
});
const checkpointPlan = useQuery({
  queryKey: ["checkpoint-plan", taskId],
  queryFn: () =>
    api<any>(
      `/api/tasks/${taskId}/review-workspace?artifact_type=final_plan&limit=1`,
    ),
  enabled: computed(() => Boolean(task.data.value?.directory_review_pending)),
  staleTime: 5000,
});
const checkpointTheme = ref("");
const checkpointRequirements = ref("");
const checkpointStructure = ref<string[]>([]);
let requirementsDraftKey = "";
let directoryDraftKey = "";
watch(
  () => [
    task.data.value?.requirement_review_pending,
    task.data.value?.theme,
    task.data.value?.user_requirements,
  ],
  ([pending, theme, requirements]) => {
    const key = `${Boolean(pending)}:${theme || ""}:${requirements || ""}`;
    if (!pending || key === requirementsDraftKey) return;
    requirementsDraftKey = key;
    checkpointTheme.value = String(theme || "");
    checkpointRequirements.value = String(requirements || "");
  },
  { immediate: true },
);
watch(
  () => [
    task.data.value?.directory_review_pending,
    checkpointPlan.data.value?.items?.[0]?.current?.chapter_plans,
  ],
  ([pending, chapters]) => {
    if (!pending) return;
    const titles = (chapters || [])
      .map((chapter: any) => String(chapter?.title || "").trim())
      .filter(Boolean);
    const key = titles.join("\u001f");
    if (!titles.length || key === directoryDraftKey) return;
    directoryDraftKey = key;
    checkpointStructure.value = titles;
  },
  { immediate: true, deep: true },
);
const checkpointStructureTitles = computed(() =>
  checkpointStructure.value.map((title) => title.trim()).filter(Boolean),
);
const checkpointStructureEdited = computed(
  () => checkpointStructureTitles.value.join("\u001f") !== directoryDraftKey,
);
const command = useMutation({
  mutationFn: ({ path }: { path: string }) => api(path, { method: "POST" }),
  onSuccess: () => qc.invalidateQueries({ queryKey: ["task", taskId] }),
});
const confirmPlanning = useMutation({
  mutationFn: () =>
    api(
      `/api/tasks/${taskId}/requirements/confirm`,
      jsonInit("POST", {
        theme: checkpointTheme.value.trim(),
        requirements: checkpointRequirements.value.trim(),
        feedback: "",
      }),
    ),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: ["task", taskId] });
  },
});
const confirmDirectory = useMutation({
  mutationFn: () =>
    api(
      `/api/tasks/${taskId}/directory/confirm`,
      jsonInit("POST", {
        feedback: "",
        structure: checkpointStructureEdited.value
          ? checkpointStructureTitles.value
          : undefined,
      }),
    ),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: ["task", taskId] });
  },
});
const rebuildGraph = useMutation({
  mutationFn: () =>
    api(`/api/tasks/${taskId}/graph/rebuild`, { method: "POST" }),
  onMutate: () => { graphBuildError.value = ""; },
  onError: (error: any) => { graphBuildError.value = String(error?.message || error || "构建请求失败"); },
  onSuccess: async () => {
    graphBuildError.value = "";
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["task", taskId] }),
      qc.invalidateQueries({ queryKey: ["task-graph", taskId] }),
      qc.invalidateQueries({ queryKey: ["task-graph-changes", taskId] }),
    ]);
  },
});
const graphBuildStatus = computed(() =>
  String(graph.data.value?.build_status?.status || "unknown"),
);
const graphBuildActive = computed(() =>
  Boolean(graph.data.value?.build_active),
);
const graphAssertionCount = computed(() =>
  Number(
    graph.data.value?.stats?.assertion_count ??
      task.data.value?.graph_status?.assertion_count ??
      0,
  ),
);
  const graphBuildMessage = computed(() => {
    const status = graph.data.value?.build_status || {};
    const job = graph.data.value?.background_job || {};
    if (job.status === "queued" && job.waiting_for_main) {
      return "主报告任务完成后将自动开始构建关系网络。";
    }
    if (job.status === "queued") {
      return "关系网络已排队，等待可用的模型执行资源。";
    }
    if (job.status === "running") {
      const progress = job.progress || {};
      if (progress.total_batches) {
        return `正在抽取关系：${progress.completed_batches || 0}/${progress.total_batches} 批，已覆盖 ${progress.completed_facts || 0}/${progress.total_facts || 0} 条事实。`;
      }
      return "关系网络正在构建，完成后会自动刷新。";
    }
  if (status.status === "partial_ready") {
    const failedFacts = Number(status.failed_fact_ids?.length || 0);
    return failedFacts
      ? `已有关系可用，另有 ${failedFacts} 条事实尚未完成关系抽取。`
      : "已有部分关系可用，仍有批次需要重新构建。";
  }
  if (
    status.error === "MODEL_OUTPUT_TRUNCATED" ||
    String(status.error || "").includes("terminal batch")
  ) {
    return "关系抽取输出超过当前模型容量，可使用自适应拆批重新构建。";
  }
  return status.error || "构图只保留可回查事实的关系。";
});
const stages = computed(() =>
  task.data.value?.run_mode === "material_comparison"
    ? stageGroups.comparison
    : stageGroups.standard,
);
const taskTabs = computed(() =>
  isComparison.value
    ? [
        ["comparison", "对比结果"],
        ["materials", "材料与过程"],
      ]
    : [
        ["overview", "概览"],
        ["materials", "材料与过程"],
        ["analysis", "分析"],
        ["report", "报告"],
        ["versions", "版本"],
      ],
);
const stageIndex = computed(() => {
  const stage = String(task.data.value?.stage || "created");
  const index = stages.value.findIndex((item) => item.keys.includes(stage));
  return index >= 0 ? index : ["failed", "paused"].includes(stage) ? -1 : 0;
});
const processCurrentLabel = computed(() => {
  if (stageIndex.value >= 0) return stages.value[stageIndex.value]?.name || "处理中";
  return stageMeta[String(task.data.value?.stage || "")]?.label || "处理中";
});
const running = computed(
  () =>
    ![
      "created",
      "requirement_review",
      "directory_review",
      "review",
      "done",
      "failed",
      "paused",
    ].includes(task.data.value?.stage || "created"),
);
const stageProgress = computed(() => {
  const stage = String(task.data.value?.stage || "created");
  const progressByStage: Record<string, { label: string; key: string }> = {
    parsing: { label: "材料解析", key: "parse_progress" },
    material_analysis: { label: "材料理解", key: "material_analysis_progress" },
    evidence: { label: "事实与证据", key: "evidence_progress" },
    writing: { label: "报告生成", key: "write_progress" },
  };
  const config = progressByStage[stage];
  if (!config) return null;
  const progress = task.data.value?.[config.key] || {};
  const total = Number(progress.total);
  if (!Number.isFinite(total) || total <= 0) return null;
  return {
    label: config.label,
    done: progress.done ?? 0,
    total,
  };
});
const pauseRequested = computed(
  () => task.data.value?.queue_status?.status === "pause_requested",
);
const facts = computed(() => analysis.data.value?.facts || []);
const inferences = computed(() =>
  [
    ...(analysis.data.value?.inferences || []),
    ...(analysis.data.value?.external_inferences || []),
  ],
);
const conflicts = computed(() => analysis.data.value?.conflicts || []);
const artifactCounts = computed(() => task.data.value?.artifact_counts || {});
const factCount = computed(() =>
  analysis.data.value
    ? facts.value.length
    : Number(artifactCounts.value.facts || 0),
);
const inferenceCount = computed(() =>
  analysis.data.value
    ? inferences.value.length
    : Number(artifactCounts.value.inferences || 0),
);
function run() {
  command.mutate({ path: `/api/tasks/${taskId}/run` });
}
function confirmPlan() {
  confirmPlanning.mutate();
}
function confirmDirectoryPlan() {
  confirmDirectory.mutate();
}
function processStageStatus(index: number) {
  if (index < stageIndex.value) return "已完成";
  if (index > stageIndex.value) return "等待中";
  const stage = String(task.data.value?.stage || "created");
  if (stage === "failed") return "运行异常";
  if (stage === "paused") return "已暂停";
  if (stage === "review") return isComparison.value ? "待审阅" : "待审核";
  if (stage === "done") return "已完成";
  if (stage === "requirement_review") return "等待确认需求";
  if (stage === "directory_review") return "等待确认目录";
  return running.value ? "正在进行" : stage === "created" ? "待运行" : "等待开始";
}
function addCheckpointChapter() {
  checkpointStructure.value.push("");
}
function removeCheckpointChapter(index: number) {
  if (checkpointStructure.value.length <= 1) return;
  checkpointStructure.value.splice(index, 1);
}
function openCheckpointAssistant(kind: "requirements" | "directory") {
  const isRequirements = kind === "requirements";
  const plan = checkpointPlan.data.value?.items?.[0] || null;
  ui.openAssistant({
    taskId,
    artifact: isRequirements
      ? {
          artifact_type: "task_brief",
          object_id: taskId,
          artifact_version: String(task.data.value?.run_revision || 1),
          current: {
            theme: task.data.value?.theme || "",
            requirements: task.data.value?.user_requirements || "",
          },
          title: "报告需求",
        }
      : plan || {
          artifact_type: "final_plan",
          object_id: String(task.data.value?.plan_id || ""),
          artifact_version: String(task.data.value?.run_revision || 1),
          current: {},
          title: "最终目录",
        },
  });
}
watch(
  () => [route.query.tab, route.query.assistant],
  async ([tab, assistant]) => {
    if (tab !== "collaboration" && assistant !== "1") return;
    active.value = "overview";
    const query = { ...route.query };
    delete query.tab;
    delete query.assistant;
    await router.replace({ query });
    ui.openAssistant({ taskId });
  },
  { immediate: true },
);
function control(op: string) {
  command.mutate({ path: `/api/tasks/${taskId}/control/${op}` });
}
function confidence(x: any) {
  return (
    { high: "高", medium: "中", low: "低" }[
      String(x.confidence_level || "").toLowerCase()
    ] || "需人工复核"
  );
}
function versionsList() {
  const data = versions.data.value;
  return Array.isArray(data) ? data : data?.versions || [];
}
</script>
<template>
  <div v-if="task.data.value" class="page-stack task-workspace">
    <UiPageHeader class="task-head" :title="task.data.value.theme">
      <template #eyebrow>
        <RouterLink to="/tasks" class="back-link">任务</RouterLink>
      </template>
      <template #meta>
        <p class="muted task-meta">
          创建于 {{ task.data.value.created_at || "—" }} ·
          {{
            task.data.value.material_count ||
            task.data.value.material_ids?.length ||
            0
          }}
          份材料 · 第 {{ task.data.value.run_revision || 1 }} 轮
        </p>
      </template>
      <template #actions>
        <div class="button-row">
        <span
          v-if="isComparison"
          class="badge"
          :class="
            task.data.value.stage === 'failed'
              ? 'danger'
              : task.data.value.stage === 'review'
                ? 'warning'
                : task.data.value.stage === 'done'
                  ? 'success'
                  : ''
          "
          >{{
            task.data.value.stage === "failed"
              ? "对比异常"
              : task.data.value.stage === "review"
                ? "等待审阅"
                : task.data.value.stage === "done"
                  ? "审阅完成"
                  : "对比中"
          }}</span
        ><StatusBadge v-else :stage="task.data.value.stage" /><button
          v-if="
            ['created', 'failed'].includes(task.data.value.stage) ||
            task.data.value.requirement_review_pending
          "
          class="btn primary"
          :disabled="command.isPending.value"
          @click="
            task.data.value.requirement_review_pending ? confirmPlan() : run()
          "
        >
          {{
            task.data.value.requirement_review_pending
              ? "确认需求并开始规划"
              : task.data.value.stage === "failed"
                ? "重新运行"
                : "开始运行"
          }}</button
        ><button
          v-if="running"
          class="btn"
          :disabled="command.isPending.value || pauseRequested"
          @click="control('pause')"
        >
          {{ pauseRequested ? "正在暂停…" : "暂停" }}</button
        ><button
          v-if="task.data.value.stage === 'paused'"
          class="btn primary"
          @click="control('resume')"
        >
          继续运行</button
        ><RouterLink
          v-if="task.data.value.report_id"
          class="btn"
          :to="`/reports/${task.data.value.report_id}`"
          >打开报告</RouterLink
        ><RouterLink
          v-else-if="isComparison && task.data.value.comparison_report_id"
          class="btn"
          :to="`/reports/${task.data.value.comparison_report_id}`"
          >查看基线报告</RouterLink
        >
        </div>
      </template>
    </UiPageHeader>
    <section class="surface progress-block">
      <div v-if="stageIndex >= 0" class="progress-rail" :class="{ compact: isComparison }">
        <div
          v-for="(item, index) in stages"
          :key="item.name"
          class="progress-step"
          :class="{ done: index < stageIndex, current: index === stageIndex }"
        >
          <span>{{ index + 1 }}</span>
          <div>
            <b>{{ item.name }}</b
            ><small>{{
              index < stageIndex
                ? "已完成"
                : index === stageIndex
                  ? task.data.value.stage === "review"
                    ? isComparison
                      ? "待审阅"
                      : "待审核"
                    : "正在进行"
                  : "等待中"
            }}</small>
          </div>
        </div>
      </div>
      <div v-else class="progress-interruption">
        <span class="progress-interruption-mark" aria-hidden="true"></span>
        <div>
          <b>{{ processCurrentLabel }}</b>
          <small>{{ task.data.value.stage === "paused" ? "任务已暂停；暂停前的具体环节未记录" : "任务异常；失败环节未记录" }}</small>
        </div>
      </div>
    </section>
    <nav class="tabs workspace-tabs">
      <button
        v-for="tab in taskTabs"
        :key="tab[0]"
        class="tab"
        :class="{ active: active === tab[0] }"
        @click="active = tab[0]"
      >
        {{ tab[1] }}
      </button>
    </nav>
    <MaterialComparisonWorkspace
      v-if="active === 'comparison' && isComparison"
      embedded
      :report-id="Number(task.data.value.comparison_report_id)"
      :comparison-id="Number(task.data.value.comparison_id)"
    />
    <section v-else-if="active === 'overview'" class="workspace-grid">
      <div class="main-column">
        <div v-if="task.data.value.stage === 'failed'" class="notice warning">
          <b>任务运行异常</b><br />{{
            task.data.value.error ||
            task.data.value.failure_reason ||
            "请查看运行详情后重新运行。"
          }}
        </div>
        <div
          v-if="task.data.value.requirement_review_pending"
          class="notice planning-review"
        >
          <b>请确认报告需求</b>
          <p>
            材料理解已完成。可先与助手讨论主题、受众、重点和篇幅；确认后才会进入分析规划。
          </p>
          <div class="checkpoint-form">
            <label>
              <span>报告主题</span>
              <input v-model="checkpointTheme" placeholder="填写或在助手中讨论后补充" />
            </label>
            <label>
              <span>报告要求</span>
              <textarea v-model="checkpointRequirements" rows="4" placeholder="填写目标读者、重点、篇幅或约束" />
            </label>
          </div>
          <div class="button-row">
            <button
              class="btn"
              @click="openCheckpointAssistant('requirements')"
            >
              查看并讨论
            </button>
            <button
              class="btn primary"
              :disabled="confirmPlanning.isPending.value"
              @click="confirmPlan"
            >
              {{
                confirmPlanning.isPending.value
                  ? "正在继续…"
                  : "确认需求并开始规划"
              }}
            </button>
          </div>
        </div>
        <div
          v-if="task.data.value.directory_review_pending"
          class="notice planning-review"
        >
          <b>请确认最终目录</b>
          <p>
            事实和分析已经完成。可以查看目录，并与助手讨论章节顺序、合并拆分和重点安排。
          </p>
          <ol v-if="checkpointStructure.length" class="checkpoint-outline checkpoint-outline-editable">
            <li
              v-for="(_title, index) in checkpointStructure"
              :key="index"
            >
              <input v-model="checkpointStructure[index]" :aria-label="`第 ${index + 1} 章标题`" />
              <button type="button" class="text-button" :disabled="checkpointStructure.length <= 1" @click="removeCheckpointChapter(index)">删除</button>
            </li>
          </ol>
          <button type="button" class="text-button checkpoint-add" @click="addCheckpointChapter">添加章节</button>
          <div class="button-row">
            <button class="btn" @click="openCheckpointAssistant('directory')">
              查看并讨论
            </button>
            <button
              class="btn primary"
              :disabled="confirmDirectory.isPending.value || (checkpointStructureEdited && !checkpointStructureTitles.length)"
              @click="confirmDirectoryPlan"
            >
              {{
                confirmDirectory.isPending.value
                  ? "正在继续…"
                  : "确认目录并开始写作"
              }}
            </button>
          </div>
        </div>
        <div class="surface section-block">
          <div class="section-head">
            <div>
              <h2>当前状态</h2>
              <p class="muted">只展示此刻需要关注的结果和下一步操作</p>
            </div>
          </div>
          <div class="status-summary">
            <div>
              <span>当前阶段</span><b>{{ processCurrentLabel }}</b>
            </div>
            <div>
              <span>已经产生</span
              ><b>{{ task.data.value.report_id ? "报告草稿" : "分析产物" }}</b>
            </div>
            <div>
              <span>下一步</span
              ><b>{{
                task.data.value.stage === "review"
                  ? "完成审核"
                  : task.data.value.stage === "done"
                    ? "导出或增量更新"
                    : task.data.value.stage === "requirement_review"
                      ? "确认需求并开始规划"
                  : task.data.value.stage === "directory_review"
                      ? "确认目录并开始写作"
                      : task.data.value.stage === "paused"
                        ? "继续运行"
                        : task.data.value.stage === "failed"
                          ? "查看异常信息"
                      : running
                        ? "等待当前阶段完成"
                        : "开始运行"
              }}</b>
            </div>
          </div>
        </div>
        <div class="surface section-block">
          <div class="section-head">
            <div>
              <h2>质量概览</h2>
              <p class="muted">业务结果与技术诊断分开呈现</p>
            </div>
          </div>
          <div class="metric-strip flat">
            <div class="metric">
              <strong>{{
                materials.data.value?.length ||
                task.data.value.material_ids?.length ||
                0
              }}</strong
              ><span>材料</span>
            </div>
            <div class="metric">
              <strong>{{ factCount }}</strong
              ><span>事实</span>
            </div>
            <div class="metric">
              <strong>{{ inferenceCount }}</strong
              ><span>分析判断</span>
            </div>
          </div>
        </div>
      </div>
      <aside class="side-column">
        <div
          v-if="task.data.value.run_mode === 'material_comparison'"
          class="surface section-block"
        >
          <h2>对比结果</h2>
          <template v-if="task.data.value.material_comparison?.summary">
            <p>
              {{
                task.data.value.material_comparison.summary.new_fact_count || 0
              }}
              条新增事实，影响
              {{
                task.data.value.material_comparison.summary.affected_sections
                  ?.length || 0
              }}
              个章节。
            </p>
            <RouterLink
              class="btn primary"
              :to="`/reports/${task.data.value.comparison_report_id}`"
              >返回基线报告审阅变化</RouterLink
            >
          </template>
          <p v-else class="muted">
            完成后将在基线报告的“新增材料对比”中集中审阅。
          </p>
        </div>
        <div class="surface section-block">
          <h2>任务产物</h2>
          <RouterLink
            v-if="task.data.value.report_id"
            :to="`/reports/${task.data.value.report_id}`"
            class="artifact-link"
            ><div>
              <b>报告草稿</b
              ><span>{{
                task.data.value.stage === "review"
                  ? "可以进入审核"
                  : "持续生成中"
              }}</span>
            </div>
            <strong>打开</strong></RouterLink
          ><button class="artifact-link" @click="active = 'materials'; workspacePane = 'materials'">
            <div>
              <b>材料解析</b
              ><span
                >{{
                  materials.data.value?.filter((x: any) => x.units_count > 0)
                    .length || 0
                }}
                份可用</span
              >
            </div>
            <strong>查看</strong></button
          ><button class="artifact-link" @click="active = 'analysis'">
            <div>
              <b>分析结果</b
              ><span>{{ factCount }} 条事实 · {{ inferenceCount }} 条判断</span>
            </div>
            <strong>查看</strong>
          </button>
        </div>
      </aside>
    </section>
    <section v-else-if="active === 'materials'" class="workspace-browser">
      <nav class="tabs workspace-subtabs" aria-label="材料与运行过程">
        <button
          class="tab"
          :class="{ active: workspacePane === 'materials' }"
          @click="workspacePane = 'materials'"
        >材料</button>
        <button
          class="tab"
          :class="{ active: workspacePane === 'process' }"
          @click="workspacePane = 'process'"
        >运行过程</button>
      </nav>
      <section v-if="workspacePane === 'materials'" class="surface section-block">
        <div class="section-head">
          <div>
            <h2>{{ isComparison ? "新增材料" : "任务材料" }}</h2>
            <p class="muted">本任务实际使用的材料及解析状态</p>
          </div>
        </div>
        <div class="data-list">
          <div
            v-for="item in materials.data.value || []"
            :key="item.filename"
            class="data-row material-row"
          >
            <div>
              <b>{{ item.filename }}</b
              ><span
                >{{ item.file_type }} · {{ item.units_count }} 个内容单元<span
                  v-if="item.pages?.length"
                >
                  · {{ item.pages.length }} 页</span
                ></span
              >
            </div>
            <span
              class="badge"
              :class="
                item.parse_status === 'error'
                  ? 'danger'
                  : item.parse_status === 'ok'
                    ? 'success'
                    : 'warning'
              "
              >{{
                item.parse_status === "ok"
                  ? "已解析"
                  : item.parse_status === "partial"
                    ? "正文可用"
                    : item.parse_status === "error"
                      ? "解析失败"
                      : "处理中"
              }}</span
            >
          </div>
        </div>
      </section>
      <section v-else class="surface section-block process-panel">
        <div class="section-head">
          <div>
            <h2>运行过程</h2>
            <p class="muted">
              {{ isComparison
                ? "仅处理新增材料并核验变化，不生成或改写基线报告。"
                : "查看各环节的完成状态和当前处理进度。" }}
            </p>
          </div>
        </div>
        <div class="process-current">
          <div>
            <span>当前环节</span>
          <strong>{{ processCurrentLabel }}</strong>
          </div>
          <div v-if="stageProgress">
            <span>{{ stageProgress.label }}</span>
            <strong>{{ stageProgress.done }} / {{ stageProgress.total }}</strong>
          </div>
          <div v-else>
            <span>状态</span>
            <strong>{{ processStageStatus(stageIndex) }}</strong>
          </div>
        </div>
        <ol v-if="stageIndex >= 0" class="process-steps">
          <li
            v-for="(item, index) in stages"
            :key="item.name"
            :class="{
              complete: index < stageIndex,
              current: index === stageIndex,
            }"
            :aria-current="index === stageIndex ? 'step' : undefined"
          >
            <span class="process-step-index">{{ index < stageIndex ? "✓" : index + 1 }}</span>
            <div>
              <strong>{{ item.name }}</strong>
              <small>{{ processStageStatus(index) }}</small>
            </div>
          </li>
        </ol>
        <p v-if="task.data.value.stage === 'failed'" class="notice warning process-error">
          {{ task.data.value.error || task.data.value.failure_reason || "当前环节未完成，请查看任务状态。" }}
        </p>
      </section>
    </section>
    <section v-else-if="active === 'analysis'" class="analysis-layout">
      <aside class="analysis-nav surface">
        <button
          v-for="item in [
            ['facts', `事实 ${facts.length}`],
            ['inferences', `分析判断 ${inferences.length}`],
            ['graph', `关系网络 ${graphAssertionCount}`],
          ]"
          :key="item[0]"
          :class="{ active: analysisType === item[0] }"
          @click="analysisType = item[0]"
        >
          {{ item[1] }}
        </button>
      </aside>
      <div class="surface analysis-content">
        <template v-if="analysisType === 'facts'"
          ><details v-for="fact in facts" :key="fact.id" class="knowledge-item">
            <summary>
              <span>{{ fact.content }}</span
              ><b>{{ fact.evidence?.length || 0 }} 个来源</b>
            </summary>
            <blockquote v-for="ev in fact.evidence || []" :key="ev.quote">
              {{ ev.source_file }}{{ ev.page ? ` 第 ${ev.page} 页` : "" }}：{{
                ev.quote
              }}
            </blockquote>
          </details></template>
        <template v-else-if="analysisType === 'inferences'"
          ><article
            v-for="item in inferences"
            :key="item.id"
            class="knowledge-item inference"
          >
            <div>
              <span
                v-if="item.source_level === 'EXTERNAL_INFORMATION'"
                class="inference-source"
                >外部补充</span
              ><span v-else class="badge success"
                >置信度 {{ confidence(item) }}</span
              ><small v-if="item.source_level === 'EXTERNAL_INFORMATION'"
                >非本任务材料依据</small
              ><small v-else
                >依据事实
                {{ item.based_fact_ids?.join("、") || "待核验" }}</small
              >
            </div>
            <p>{{ item.content }}</p>
            <blockquote v-if="item.reasoning_chain">
              {{ item.reasoning_chain }}
            </blockquote>
          </article></template>
        <template v-else-if="analysisType === 'graph'"
          ><div class="graph-summary">
            <span
              class="badge"
              :class="
                graph.data.value?.mode === 'active' ? 'success' : 'warning'
              "
              >{{
                graph.data.value?.mode === "active"
                  ? "Graph RAG 已启用"
                  : "图谱观测模式"
              }}</span
            ><small>关系只保存有事实依据的实体联系。</small
            ><span v-if="graphBuildError" class="graph-build-error">{{ graphBuildError }}</span
              ><button class="btn graph-build-button" :disabled="rebuildGraph.isPending.value || graphBuildActive" @click="rebuildGraph.mutate()">{{ graphBuildActive ? (graph.data.value?.background_job?.status === "queued" ? "已排队" : "正在构建") : "构建知识图谱" }}</button
              ><span v-if="graphBuildActive" class="badge warning">{{ graph.data.value?.background_job?.status === "queued" ? "已排队" : "正在构建" }}</span
            ><span
              v-else-if="graphBuildStatus === 'partial_ready'"
              class="badge warning"
              >部分完成</span
            ><span
              v-else-if="graphBuildStatus === 'degraded'"
              class="badge danger"
              >构建失败</span
            >
          </div>
          <GraphNetwork
            v-if="graph.data.value?.edges?.length"
            :nodes="graph.data.value.nodes"
            :edges="graph.data.value.edges"
            @select="selectedGraphEdge = $event"
          />
          <article v-if="selectedGraphEdge" class="knowledge-item graph-edge">
            <div>
              <b
                >{{ selectedGraphEdge.subject_name }} —{{
                  selectedGraphEdge.predicate
                }}→
                {{
                  selectedGraphEdge.object_name ||
                  selectedGraphEdge.object_value
                }}</b
              ><span class="badge">{{
                selectedGraphEdge.status === "confirmed" ? "已确认" : "已校验"
              }}</span>
            </div>
            <small
              >依据事实 {{ selectedGraphEdge.fact_ids?.join("、") || "—" }} ·
              置信度
              {{
                confidence({ confidence_level: selectedGraphEdge.confidence })
              }}</small
            >
          </article>
          <div
            v-if="graphChanges.data.value?.changesets?.length"
            class="graph-changes"
          >
            <small>最近图谱变更</small>
            <span
              v-for="change in graphChanges.data.value.changesets.slice(0, 5)"
              :key="change.id"
              >{{ change.change_type }} · {{ change.status }}</span
            >
          </div>
          <div v-if="!graph.data.value?.edges?.length" class="empty">
            <div>
              <strong>尚未形成可展示的关系网络</strong
              ><span>{{ graphBuildMessage }}</span>
            </div>
          </div></template>

      </div>
    </section>
    <section
      v-else-if="active === 'report'"
      class="surface section-block report-entry"
      :class="{ 'tab-entry--empty': !task.data.value.report_id }"
    >
      <template v-if="task.data.value.report_id"
        ><div>
          <h2>
            {{
              task.data.value.stage === "review"
                ? "报告已形成，可以进入审核"
                : "报告草稿正在生成"
            }}
          </h2>
          <p class="muted">
            生成期间可查看当前草稿；只有关键写作链路完成后才显示为可审核。
          </p>
        </div>
        <div class="button-row">
          <RouterLink
            class="btn primary"
            :to="`/reports/${task.data.value.report_id}`"
            >进入报告编辑器</RouterLink
          ><a
            v-if="['review', 'done'].includes(task.data.value.stage)"
            class="btn"
            :href="`/api/reports/${task.data.value.report_id}/export`"
            >导出 Word</a
          >
        </div></template>
      <div v-else class="empty tab-empty-state">
        <div>
          <strong>尚未生成报告</strong>
          <span>完成分析后，系统将按照最终报告计划开始写作。</span>
        </div>
      </div>
    </section>
    <section
      v-else
      class="surface section-block version-entry"
      :class="{ 'tab-entry--empty': !versionsList().length }"
    >
      <div v-if="versionsList().length" class="section-head">
        <div>
          <h2>版本记录</h2>
          <p class="muted">每次快照和增量更新都形成可审阅的历史基线</p>
        </div>
      </div>
      <div v-if="versionsList().length" class="data-list">
        <div
          v-for="item in versionsList()"
          :key="item.id"
          class="data-row version-row"
        >
          <div>
            <b>v{{ item.version_label || item.version_no }}</b
            ><span>{{ item.change_summary || "报告版本快照" }}</span>
          </div>
          <span>{{ item.created_at || "—" }}</span
          ><RouterLink
            class="btn tertiary"
            :to="`/reports/${task.data.value.report_id}?version=${item.id}`"
            >审阅差异</RouterLink
          >
        </div>
      </div>
      <div v-else class="empty tab-empty-state">
        <div>
          <strong>暂无版本快照</strong>
          <span>报告编辑器中可生成快照或启动增量更新。</span>
        </div>
      </div>
    </section>
  </div>
  <div v-else-if="task.isLoading.value" class="loading-line"></div>
  <div v-else class="empty">
    <div><strong>任务不存在</strong>该任务可能已经删除。</div>
  </div>
</template>
<style scoped>
.task-head {
  align-items: flex-start;
}
.task-head h1 {
  max-width: 920px;
  margin: 6px 0 8px;
  color: var(--foreground);
  font-size: clamp(24px, 2vw, 32px);
  letter-spacing: -0.035em;
  line-height: 1.18;
}
.back-link {
  display: inline-flex;
  margin-bottom: 4px;
  color: var(--color-primary);
  font-size: 13px;
  font-weight: 650;
}
.task-meta {
  margin: 6px 0 0;
}
.progress-block {
  overflow: hidden;
  padding: 18px 22px 16px;
  border-color: var(--border);
  background: var(--card);
}
.progress-interruption {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 36px;
}
.progress-interruption-mark {
  width: 8px;
  height: 8px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--warning);
}
.progress-interruption b,
.progress-interruption small {
  display: block;
}
.progress-interruption b {
  font-size: 13px;
  font-weight: 600;
}
.progress-interruption small {
  margin-top: 3px;
  color: var(--muted-foreground);
  font-size: 12px;
}
.progress-rail {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 4px;
}
.progress-rail.compact {
  grid-template-columns: repeat(3, 1fr);
}
.progress-step {
  position: relative;
  display: flex;
  min-width: 0;
  gap: 11px;
  align-items: center;
}
.progress-step::after {
  content: "";
  position: absolute;
  left: 38px;
  right: 0;
  top: 14px;
  height: 1px;
  background: color-mix(in srgb, var(--border-strong) 82%, transparent);
}
.progress-step:last-child::after {
  display: none;
}
.progress-step > span {
  position: relative;
  z-index: 1;
  display: grid;
  place-items: center;
  width: 29px;
  height: 29px;
  border: 1px solid var(--color-border-strong);
  border-radius: 8px;
  background: var(--card);
  color: var(--color-faint);
  font-size: 12px;
}
.progress-step > div {
  position: relative;
  z-index: 1;
  min-width: 0;
  padding: 2px 8px 2px 1px;
  background: var(--card);
}
.progress-step.done > span {
  color: var(--foreground);
  border-color: var(--border-strong);
  background: var(--surface-hover);
}
.progress-step.current > span {
  color: var(--foreground);
  border-color: var(--color-primary);
  background: var(--color-primary);
}
.progress-step b,
.progress-step small {
  display: block;
}
.progress-step b {
  overflow: hidden;
  color: var(--foreground);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.progress-step small {
  color: var(--color-faint);
  font-size: 11px;
}
.workspace-tabs {
  position: sticky;
  z-index: 5;
  top: 0;
  padding: 7px 4px 9px;
  background: color-mix(in srgb, var(--background) 92%, transparent);
  backdrop-filter: blur(8px);
}
.workspace-browser {
  display: grid;
  min-width: 0;
  gap: 12px;
}
.workspace-subtabs {
  position: static;
  z-index: auto;
  display: flex;
  width: fit-content;
  max-width: 100%;
  padding: 0 4px;
  background: transparent;
  backdrop-filter: none;
}
.process-panel .section-head {
  margin-bottom: 4px;
}
.process-current {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
  padding: 14px 0 16px;
  border-bottom: 1px solid var(--border);
}
.process-current > div {
  min-width: 0;
}
.process-current span,
.process-current strong {
  display: block;
}
.process-current span {
  color: var(--muted-foreground);
  font-size: 12px;
}
.process-current strong {
  margin-top: 5px;
  font-size: 14px;
  font-weight: 600;
}
.process-steps {
  display: grid;
  gap: 0;
  margin: 0;
  padding: 18px 0 0;
  list-style: none;
}
.process-steps li {
  position: relative;
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  gap: 12px;
  min-width: 0;
  padding: 0 0 18px;
}
.process-steps li:not(:last-child)::before {
  position: absolute;
  top: 30px;
  bottom: 0;
  left: 14px;
  width: 1px;
  background: var(--border);
  content: "";
}
.process-step-index {
  position: relative;
  z-index: 1;
  display: grid;
  width: 30px;
  height: 30px;
  place-items: center;
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  background: var(--surface-raised);
  color: var(--muted-foreground);
  font-size: 12px;
}
.process-steps li.current .process-step-index {
  border-color: color-mix(in srgb, var(--primary) 62%, var(--border));
  background: var(--primary-soft);
  color: var(--foreground);
}
.process-steps li.complete .process-step-index {
  color: var(--foreground);
}
.process-steps li > div {
  min-width: 0;
  padding-top: 2px;
}
.process-steps strong,
.process-steps small {
  display: block;
}
.process-steps strong {
  font-size: 13px;
  font-weight: 550;
}
.process-steps small {
  margin-top: 3px;
  color: var(--muted-foreground);
  font-size: 12px;
}
.process-steps li.current small {
  color: var(--accent);
}
.process-error {
  margin-top: 4px;
}
.workspace-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 28px;
}
.main-column,
.side-column {
  display: grid;
  align-content: start;
  gap: 24px;
}
.status-summary {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
}
.status-summary span,
.status-summary b {
  display: block;
}
.status-summary span {
  color: var(--color-muted);
  font-size: 12px;
}
.flat {
  border: 0;
}
.artifact-link {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 15px 3px;
  border: 0;
  border-top: 1px solid var(--color-border);
  background: transparent;
  text-align: left;
}
.artifact-link:hover {
  background: var(--muted);
}
.artifact-link div b,
.artifact-link div span {
  display: block;
}
.artifact-link div span {
  color: var(--color-muted);
  font-size: 12px;
}
.artifact-link > strong {
  color: var(--color-primary);
  font-size: 12px;
}
.material-row {
  grid-template-columns: 1fr auto;
}
.material-row div b,
.material-row div span {
  display: block;
}
.material-row div span {
  color: var(--color-muted);
  font-size: 12px;
}
.analysis-layout {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 24px;
}
.analysis-nav {
  align-self: start;
  padding: 8px;
}
.analysis-nav button {
  width: 100%;
  padding: 11px 12px;
  border: 1px solid transparent;
  background: var(--surface-raised);
  color: var(--foreground);
  text-align: left;
  font-size: 13px;
  font-weight: 550;
  border-radius: var(--radius-sm);
  transition: background-color var(--motion-fast), border-color var(--motion-fast);
}
.analysis-nav button:hover {
  background: var(--surface-hover);
  border-color: var(--border-strong);
}
.analysis-nav button.active {
  color: var(--foreground);
  background: var(--primary-soft);
  border-color: color-mix(in srgb, var(--primary) 58%, var(--border));
  font-weight: 600;
}
.analysis-content {
  padding: 8px 24px;
}
.knowledge-item {
  padding: 16px 0;
  border-bottom: 1px solid var(--color-border);
}
.knowledge-item summary {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  cursor: pointer;
}
.knowledge-item summary span {
  font-weight: 550;
}
.knowledge-item summary b {
  color: var(--color-primary);
  font-size: 12px;
  white-space: nowrap;
}
.knowledge-item blockquote {
  margin: 12px 0 0;
  padding: 10px 14px;
  border-left: 2px solid var(--border-strong);
  background: var(--color-surface-soft);
  color: var(--color-muted);
}
.knowledge-item.inference {
  border-left: 2px solid var(--success);
  padding-left: 14px;
}
.knowledge-item.inference > div {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.knowledge-item.inference p {
  margin: 12px 0 0;
}
.inference-source {
  color: var(--color-muted);
  border: 1px solid var(--color-border);
  background: var(--color-surface-soft);
  border-radius: var(--radius-sm);
  padding: 2px 7px;
  font-size: 12px;
}
.knowledge-item.conflict {
  border-left: 2px solid var(--warning);
  padding-left: 14px;
}
.conflict-review {
  margin-bottom: 18px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
}
.conflict-review > header {
  padding: 16px 18px;
  border-bottom: 1px solid var(--color-border);
}
.conflict-review > header > div,
.conflict-side {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.conflict-review > header b {
  display: block;
  margin-top: 10px;
  font-size: 16px;
}
.conflict-review > header p {
  margin: 7px 0 0;
  color: var(--color-muted);
}
.conflict-type {
  padding: 3px 7px;
  border-radius: 3px;
  background: var(--surface-hover);
  color: var(--muted-foreground);
  font-size: 12px;
  font-weight: 650;
}
.conflict-type.direct_contradiction {
  background: var(--destructive-soft);
  color: var(--destructive);
}
.conflict-type.qualification,
.conflict-type.temporal_difference {
  background: var(--warning-soft);
  color: var(--warning);
}
.conflict-pair {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 190px minmax(0, 1fr);
  align-items: stretch;
}
.conflict-pair > section {
  min-width: 0;
  padding: 16px 18px;
}
.conflict-pair > section:first-child {
  grid-column: 1;
}
.conflict-pair > section:nth-child(2) {
  grid-column: 3;
}
.conflict-relation {
  grid-column: 2;
  grid-row: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 16px;
  border-inline: 1px solid var(--color-border);
  background: var(--color-surface-soft);
  text-align: center;
}
.conflict-relation span {
  color: var(--color-primary);
  font-size: 12px;
  font-weight: 650;
}
.conflict-relation b {
  font-size: 13px;
  line-height: 1.6;
}
.conflict-side span {
  color: var(--color-primary);
  font-weight: 650;
}
.conflict-side small,
.conflict-review footer {
  color: var(--color-muted);
  font-size: 12px;
}
.conflict-pair section > strong {
  display: block;
  margin-top: 12px;
  line-height: 1.65;
}
.conflict-pair blockquote {
  margin: 12px 0;
  padding: 10px 12px;
  border-left: 2px solid var(--color-border-strong);
  background: var(--color-surface-soft);
  color: var(--color-muted);
  line-height: 1.6;
}
.conflict-ambiguous {
  padding: 18px;
  background: var(--warning-soft);
}
.conflict-ambiguous > strong {
  color: var(--warning);
}
.conflict-ambiguous > p {
  color: var(--color-muted);
  line-height: 1.65;
}
.conflict-candidates {
  display: grid;
  gap: 8px;
}
.conflict-candidates section {
  padding: 10px 12px;
  border-left: 2px solid var(--warning);
  background: var(--surface-raised);
}
.conflict-candidates section small,
.conflict-candidates section b,
.conflict-candidates section footer {
  display: block;
}
.conflict-candidates section b {
  margin: 5px 0;
  line-height: 1.55;
}
@media (max-width: 900px) {
  .conflict-pair {
    grid-template-columns: 1fr;
  }
  .conflict-pair > section:first-child,
  .conflict-pair > section:nth-child(2),
  .conflict-relation {
    grid-column: 1;
  }
  .conflict-pair > section:first-child {
    grid-row: 1;
  }
  .conflict-relation {
    grid-row: 2;
    border: 1px solid var(--color-border);
    border-inline: 0;
  }
  .conflict-pair > section:nth-child(2) {
    grid-row: 3;
  }
}
.graph-summary,
.graph-changes,
.graph-edge > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.graph-summary {
  padding: 12px 0;
  border-bottom: 1px solid var(--color-border);
}
.graph-summary small,
.graph-edge small,
.graph-changes small {
  color: var(--color-muted);
}
.graph-changes {
  justify-content: flex-start;
  flex-wrap: wrap;
  padding: 12px 0;
  border-bottom: 1px solid var(--color-border);
}
.graph-changes span {
  font-size: 12px;
  color: var(--color-muted);
}
.report-entry {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}
.tab-entry--empty {
  display: grid;
  min-height: 260px;
}
.report-entry.tab-entry--empty,
.version-entry.tab-entry--empty {
  min-height: 260px;
  grid-template-columns: minmax(0, 1fr);
}
.tab-empty-state {
  width: 100%;
  min-width: 0;
  min-height: 0;
  padding: 24px;
  display: grid;
  place-items: center;
  text-align: center;
}
.tab-empty-state > div {
  width: min(100%, 460px);
  margin-inline: auto;
  justify-self: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}
.tab-empty-state > div > span {
  display: block;
  margin-top: 4px;
}
.version-entry {
  min-height: 0;
}
.report-entry h2,
.report-entry p {
  margin-bottom: 3px;
}
.version-row {
  grid-template-columns: 1fr 160px auto;
}
.version-row div b,
.version-row div span {
  display: block;
}
.version-row div span {
  color: var(--color-muted);
}
.checkpoint-form {
  display: grid;
  gap: 8px;
  margin: 14px 0;
}
.checkpoint-form label {
  display: grid;
  gap: 7px;
  padding: 11px 13px;
  border-left: 2px solid var(--primary);
  border-radius: 0 8px 8px 0;
  background: color-mix(in srgb, var(--primary) 5%, var(--card));
}
.checkpoint-form label > span {
  color: var(--color-muted);
  font-size: 12px;
}
.checkpoint-form input,
.checkpoint-form textarea,
.checkpoint-outline input {
  width: 100%;
  min-width: 0;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--surface-raised);
  color: var(--color-text);
  font: inherit;
  line-height: 1.55;
}
.checkpoint-form input,
.checkpoint-outline input {
  height: 34px;
  padding: 0 10px;
}
.checkpoint-form textarea {
  min-height: 92px;
  padding: 8px 10px;
  resize: vertical;
}
.checkpoint-form input:focus,
.checkpoint-form textarea:focus,
.checkpoint-outline input:focus {
  outline: none;
  border-color: var(--primary);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--primary) 20%, transparent);
}
.checkpoint-outline {
  margin: 12px 0;
  padding-left: 22px;
  line-height: 1.8;
}
.checkpoint-outline-editable {
  display: grid;
  gap: 8px;
  padding-left: 30px;
}
.checkpoint-outline-editable li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-left: 4px;
}
.text-button {
  flex: 0 0 auto;
  border: 0;
  background: transparent;
  color: var(--color-muted);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.text-button:hover:not(:disabled) {
  color: var(--color-text);
}
.text-button:disabled {
  cursor: not-allowed;
  opacity: .45;
}
.checkpoint-add {
  margin: -2px 0 12px;
  text-align: left;
}
@media (max-width: 900px) {
  .task-head,
  .report-entry {
    display: grid;
  }
  .workspace-grid {
    grid-template-columns: 1fr;
  }
  .progress-rail {
    grid-template-columns: 1fr;
    gap: 12px;
  }
  .progress-step::after {
    display: none;
  }
  .status-summary {
    grid-template-columns: repeat(2, 1fr);
  }
  .analysis-layout {
    grid-template-columns: 1fr;
  }
  .analysis-nav {
    display: flex;
    overflow: auto;
  }
  .analysis-nav button {
    width: auto;
    white-space: nowrap;
  }
}
@media (max-width: 600px) {
  .status-summary {
    grid-template-columns: 1fr;
  }
  .process-current {
    align-items: flex-start;
    flex-direction: column;
    gap: 12px;
  }
  .version-row {
    grid-template-columns: 1fr auto;
  }
  .version-row > span {
    display: none;
  }
}
</style>
