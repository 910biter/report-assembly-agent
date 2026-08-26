<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useRoute, RouterLink } from "vue-router";
import { api } from "@/api/http";
import type { TaskSummary } from "@/api/types";
import StatusBadge from "@/components/StatusBadge.vue";
import ArtifactReviewWorkspace from "@/components/ArtifactReviewWorkspace.vue";
import MaterialComparisonWorkspace from "@/components/MaterialComparisonWorkspace.vue";
const GraphNetwork = defineAsyncComponent(
  () => import("@/components/GraphNetwork.vue"),
);
const route = useRoute();
const qc = useQueryClient();
const taskId = String(route.params.taskId);
const active = ref(String(route.query.tab || "overview"));
const analysisType = ref("facts");
const detailsOpen = ref(false);
const selectedGraphEdge = ref<any>(null);
const task = useQuery({
  queryKey: ["task", taskId],
  queryFn: () => api<TaskSummary>(`/api/tasks/${taskId}`),
  refetchInterval: (q) =>
    ["review", "done", "failed", "paused"].includes(String(q.state.data?.stage))
      ? 15000
      : 4000,
});
const isComparison = computed(() => task.data.value?.run_mode === "material_comparison");
watch(() => task.data.value?.run_mode, mode => {
  if (mode === "material_comparison" && !route.query.tab) active.value = "comparison";
}, { immediate: true });
const materials = useQuery({
  queryKey: ["task-materials", taskId],
  queryFn: () => api<any[]>(`/api/tasks/${taskId}/materials`),
  enabled: computed(() => ["materials", "overview"].includes(active.value)),
  staleTime: 30000,
});
const analysis = useQuery({
  queryKey: ["task-analysis", taskId],
  queryFn: () => api<any>(`/api/tasks/${taskId}/analysis`),
  enabled: computed(() => ["analysis", "overview"].includes(active.value)),
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
const command = useMutation({
  mutationFn: ({ path }: { path: string }) => api(path, { method: "POST" }),
  onSuccess: () => qc.invalidateQueries({ queryKey: ["task", taskId] }),
});
const rebuildGraph = useMutation({
  mutationFn: () => api(`/api/tasks/${taskId}/graph/rebuild`, { method: "POST" }),
  onSuccess: async () => {
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
const graphBuildActive = computed(() => Boolean(graph.data.value?.build_active));
const graphBuildMessage = computed(() => {
  const status = graph.data.value?.build_status || {};
  if (status.status === "partial_ready") {
    const failedFacts = Number(status.failed_fact_ids?.length || 0);
    return failedFacts
      ? `已有关系可用，另有 ${failedFacts} 条事实尚未完成关系抽取。`
      : "已有部分关系可用，仍有批次需要重新构建。";
  }
  if (status.error === "MODEL_OUTPUT_TRUNCATED" || String(status.error || "").includes("terminal batch")) {
    return "关系抽取输出超过当前模型容量，可使用自适应拆批重新构建。";
  }
  return status.error || "构图只保留可回查事实的关系。";
});
const stages = computed(() =>
  task.data.value?.run_mode === "material_comparison"
    ? [
        {
          name: "新增材料准备",
          keys: [
            "created",
            "parsing",
            "dedup",
            "material_analysis",
            "planning",
          ],
        },
        { name: "证据与变化分析", keys: ["evidence", "conflict", "analysis"] },
        { name: "变化审阅", keys: ["review", "done"] },
      ]
    : [
        { name: "材料准备", keys: ["created", "parsing", "dedup"] },
        {
          name: "分析规划",
          keys: [
            "material_analysis",
            "planning",
            "evidence",
            "conflict",
            "analysis",
          ],
        },
        { name: "报告生成", keys: ["writing", "knowledge"] },
        { name: "审核完成", keys: ["review", "done"] },
      ],
);
const taskTabs = computed(() => isComparison.value
  ? [["comparison", "对比结果"], ["materials", "新增材料"], ["runtime", "运行详情"]]
  : [["overview", "概览"], ["materials", "材料"], ["analysis", "分析"], ["report", "报告"], ["versions", "版本"], ["collaboration", "协作审阅"]]);
const stageIndex = computed(() =>
  Math.max(
    0,
    stages.value.findIndex((x) =>
      x.keys.includes(task.data.value?.stage || ""),
    ),
  ),
);
const running = computed(
  () =>
    !["created", "review", "done", "failed", "paused"].includes(
      task.data.value?.stage || "created",
    ),
);
const facts = computed(() => analysis.data.value?.facts || []);
const inferences = computed(() =>
  (analysis.data.value?.inferences || []).filter(
    (x: any) => x.source_level === "MATERIAL_INFERENCE",
  ),
);
const conflicts = computed(() => analysis.data.value?.conflicts || []);
function run() {
  command.mutate({ path: `/api/tasks/${taskId}/run` });
}
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
    <header class="task-head">
      <div>
        <RouterLink to="/tasks" class="back-link">任务 /</RouterLink>
        <h1>{{ task.data.value.theme }}</h1>
        <p class="muted">
          创建于 {{ task.data.value.created_at || "—" }} ·
          {{
            task.data.value.material_count ||
            task.data.value.material_ids?.length ||
            0
          }}
          份材料 · 第 {{ task.data.value.run_revision || 1 }} 轮
        </p>
      </div>
      <div class="button-row">
        <span v-if="isComparison" class="badge" :class="task.data.value.stage === 'failed' ? 'danger' : task.data.value.stage === 'review' ? 'warning' : task.data.value.stage === 'done' ? 'success' : ''">{{ task.data.value.stage === 'failed' ? '对比异常' : task.data.value.stage === 'review' ? '等待审阅' : task.data.value.stage === 'done' ? '审阅完成' : '对比中' }}</span><StatusBadge v-else :stage="task.data.value.stage" /><button
          v-if="['created', 'failed'].includes(task.data.value.stage)"
          class="btn primary"
          :disabled="command.isPending.value"
          @click="run"
        >
          {{
            task.data.value.stage === "failed" ? "重新运行" : "开始运行"
          }}</button
        ><button v-if="running" class="btn" @click="control('pause')">
          暂停</button
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
    </header>
    <section class="surface progress-block">
      <div class="progress-rail" :class="{ compact: isComparison }">
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
                    ? isComparison ? "待审阅" : "待审核"
                    : "正在进行"
                  : "等待中"
            }}</small>
          </div>
        </div>
      </div>
      <button class="details-toggle" @click="detailsOpen = !detailsOpen">
        {{ detailsOpen ? "收起运行详情" : "查看运行详情" }}
      </button>
      <div v-if="detailsOpen" class="run-details">
        <div>
          <span>内部阶段</span><b>{{ task.data.value.stage }}</b>
        </div>
        <div>
          <span>队列状态</span
          ><b>{{ task.data.value.queue_status?.status || "—" }}</b>
        </div>
        <div>
          <span>解析进度</span
          ><b
            >{{ task.data.value.parse_progress?.done || 0 }} /
            {{ task.data.value.parse_progress?.total || "—" }}</b
          >
        </div>
        <div>
          <span>写作进度</span
          ><b
            >{{ task.data.value.write_progress?.done || 0 }} /
            {{ task.data.value.write_progress?.total || "—" }}</b
          >
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
    <section v-else-if="active === 'runtime' && isComparison" class="surface section-block">
      <div class="section-head"><div><h2>运行详情</h2><p class="muted">对比任务只执行新增材料解析、事实提取和变化核验，不生成或改写报告。</p></div></div>
      <div class="status-summary"><div><span>内部阶段</span><b>{{ task.data.value.stage }}</b></div><div><span>队列状态</span><b>{{ task.data.value.queue_status?.status || "—" }}</b></div><div><span>解析进度</span><b>{{ task.data.value.parse_progress?.done || 0 }} / {{ task.data.value.parse_progress?.total || "—" }}</b></div></div>
    </section>
    <section v-else-if="active === 'overview'" class="workspace-grid">
      <div class="main-column">
        <div v-if="task.data.value.stage === 'failed'" class="notice warning">
          <b>任务运行异常</b><br />{{
            task.data.value.error ||
            task.data.value.failure_reason ||
            "请查看运行详情后重新运行。"
          }}
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
              <span>当前阶段</span><b>{{ stages[stageIndex]?.name }}</b>
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
              <strong>{{ facts.length }}</strong
              ><span>事实</span>
            </div>
            <div class="metric">
              <strong>{{ inferences.length }}</strong
              ><span>分析判断</span>
            </div>
            <div class="metric">
              <strong>{{
                conflicts.length + (task.data.value.qa_notes?.length || 0)
              }}</strong
              ><span>待核验</span>
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
          ><button class="artifact-link" @click="active = 'materials'">
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
              ><span
                >{{ facts.length }} 条事实 ·
                {{ inferences.length }} 条判断</span
              >
            </div>
            <strong>查看</strong>
          </button>
        </div>
      </aside>
    </section>
    <section v-else-if="active === 'materials'" class="surface section-block">
      <div class="section-head">
        <div>
          <h2>任务材料</h2>
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
    <section v-else-if="active === 'analysis'" class="analysis-layout">
      <aside class="analysis-nav surface">
        <button
          v-for="item in [
            ['facts', `事实 ${facts.length}`],
            ['inferences', `分析判断 ${inferences.length}`],
            [
              'graph',
              `关系网络 ${graph.data.value?.stats?.assertion_count || 0}`,
            ],
            ['conflicts', `冲突与待核验 ${conflicts.length}`],
            ['qa', `质量检查 ${task.data.value.qa_notes?.length || 0}`],
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
          </details></template
        ><template v-else-if="analysisType === 'inferences'"
          ><article
            v-for="item in inferences"
            :key="item.id"
            class="knowledge-item inference"
          >
            <div>
              <span class="badge success">置信度 {{ confidence(item) }}</span
              ><small
                >依据事实
                {{ item.based_fact_ids?.join("、") || "待核验" }}</small
              >
            </div>
            <p>{{ item.content }}</p>
            <blockquote v-if="item.reasoning_chain">
              {{ item.reasoning_chain }}
            </blockquote>
          </article></template
        ><template v-else-if="analysisType === 'graph'"
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
            ><span
              v-if="graphBuildActive"
              class="badge warning"
              >正在构建</span
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
              ><span>{{ graphBuildMessage }}</span
              ><button
                v-if="graph.data.value?.mode !== 'off'"
                class="btn"
                :disabled="rebuildGraph.isPending.value || graphBuildActive"
                @click="rebuildGraph.mutate()"
              >{{ graphBuildActive ? '正在重建' : '重新构建关系网络' }}</button>
            </div>
          </div></template
        ><template v-else-if="analysisType === 'conflicts'"
          ><article
            v-for="item in conflicts"
            :key="item.id"
            class="knowledge-item conflict"
          >
            <b>{{ item.fact_key }}</b>
            <p v-for="entry in item.entries || []" :key="entry.statement">
              {{ entry.file }}：{{ entry.statement }}
            </p>
          </article>
          <div v-if="!conflicts.length" class="empty">
            <div>
              <strong>未发现明确冲突</strong>冲突核验不会因为结果为空而删除。
            </div>
          </div></template
        ><template v-else
          ><article
            v-for="(item, index) in task.data.value.qa_notes || []"
            :key="index"
            class="knowledge-item conflict"
          >
            <b>{{ item.type || "质量问题" }}</b>
            <p>{{ item.note || item.quote }}</p>
          </article>
          <div v-if="!task.data.value.qa_notes?.length" class="empty">
            <div><strong>暂无质量问题</strong>深度检查结果会在这里出现。</div>
          </div></template
        >
      </div>
    </section>
    <ArtifactReviewWorkspace
      v-else-if="active === 'collaboration'"
      :task-id="taskId"
      :report-id="task.data.value.report_id"
      :run-revision="task.data.value.run_revision || 1"
    />
    <section
      v-else-if="active === 'report'"
      class="surface section-block report-entry"
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
        </div></template
      >
      <div v-else class="empty">
        <div>
          <strong>尚未生成报告</strong
          >完成分析后，系统将按照最终报告计划开始写作。
        </div>
      </div>
    </section>
    <section v-else class="surface section-block">
      <div class="section-head">
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
      <div v-else class="empty">
        <div>
          <strong>暂无版本快照</strong>报告编辑器中可生成快照或启动增量更新。
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
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 24px;
}
.task-head h1 {
  margin: 3px 0;
}
.back-link {
  color: var(--color-primary);
  font-size: 12px;
}
.progress-block {
  padding: 20px 24px;
}
.progress-rail {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
}
.progress-rail.compact {
  grid-template-columns: repeat(3, 1fr);
}
.progress-step {
  position: relative;
  display: flex;
  gap: 12px;
  align-items: center;
}
.progress-step::after {
  content: "";
  position: absolute;
  left: 42px;
  right: 12px;
  top: 14px;
  height: 1px;
  background: var(--color-border-strong);
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
  border-radius: 50%;
  background: #fff;
  color: var(--color-faint);
  font-size: 12px;
}
.progress-step.done > span {
  color: #fff;
  border-color: var(--color-success);
  background: var(--color-success);
}
.progress-step.current > span {
  color: #fff;
  border-color: var(--color-primary);
  background: var(--color-primary);
}
.progress-step b,
.progress-step small {
  display: block;
}
.progress-step small {
  color: var(--color-faint);
  font-size: 11px;
}
.details-toggle {
  margin: 16px 0 0;
  border: 0;
  background: transparent;
  color: var(--color-primary);
  padding: 0;
}
.run-details {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--color-border);
}
.run-details span,
.run-details b {
  display: block;
}
.run-details span {
  color: var(--color-muted);
  font-size: 12px;
}
.workspace-tabs {
  padding: 0 4px;
}
.workspace-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 24px;
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
  padding: 13px 0;
  border: 0;
  border-top: 1px solid var(--color-border);
  background: transparent;
  text-align: left;
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
  border: 0;
  background: transparent;
  text-align: left;
  border-radius: 4px;
}
.analysis-nav button.active {
  color: var(--color-primary);
  background: var(--color-primary-soft);
  font-weight: 650;
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
  border-left: 2px solid #aebbc9;
  background: var(--color-surface-soft);
  color: var(--color-muted);
}
.knowledge-item.inference {
  border-left: 2px solid #73a983;
  padding-left: 14px;
}
.knowledge-item.inference > div {
  display: flex;
  justify-content: space-between;
}
.knowledge-item.inference p {
  margin: 12px 0 0;
}
.knowledge-item.conflict {
  border-left: 2px solid #d39a48;
  padding-left: 14px;
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
  .run-details,
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
  .run-details,
  .status-summary {
    grid-template-columns: 1fr;
  }
  .version-row {
    grid-template-columns: 1fr auto;
  }
  .version-row > span {
    display: none;
  }
}
</style>
