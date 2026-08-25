<script setup lang="ts">
import { computed, ref } from "vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { useRoute, RouterLink } from "vue-router";
import { api, jsonInit } from "@/api/http";
import type { ReportData, Sentence } from "@/api/types";
import StatusBadge from "@/components/StatusBadge.vue";
import VersionReviewWorkspace from "@/components/VersionReviewWorkspace.vue";
import MaterialComparisonWorkspace from "@/components/MaterialComparisonWorkspace.vue";
import ReviewCopilot from "@/components/ReviewCopilot.vue";

const route = useRoute();
const queryClient = useQueryClient();
const reportId = Number(route.params.reportId);
const mode = ref<"edit" | "trace">("edit");
const sideTab = ref("evidence");
const selected = ref<Sentence | null>(null);
const discussionScope = ref<any>(null);
const saveState = ref("已保存");
const incrementOpen = ref(false);
const comparisonOpen = ref(false);
const versionOpen = ref(Boolean(route.query.version));
const updateReason = ref("");
const incrementalFiles = ref<HTMLInputElement>();
const incrementalMaterialIds = ref<number[]>([]);
const sourceComparisonId = ref<number | null>(null);

const report = useQuery({
  queryKey: ["report", reportId],
  queryFn: () => api<ReportData>(`/api/reports/${reportId}`),
});
const materials = useQuery({
  queryKey: ["materials"],
  queryFn: () => api<any[]>("/api/materials"),
  enabled: incrementOpen,
});
const qaIssues = computed(() => report.data.value?.qa_issues || []);
const currentDetails = computed(() => selected.value);
const discussionTarget = computed(
  () =>
    discussionScope.value || {
      artifactType: "report_title",
      objectId: "",
      current: { title: report.data.value?.title || "" },
    },
);

function choose(sentence: Sentence) {
  selected.value = sentence;
  discussionScope.value = {
    artifactType: "sentence",
    objectId: sentence.id,
    current: {
      content: sentence.content,
      source_refs: {
        fact_ids: sentence.fact_ids || [],
        inference_ids: sentence.inference_ids || [],
      },
    },
  };
  if (mode.value === "trace") sideTab.value = "evidence";
}
function discussTitle() {
  discussionScope.value = {
    artifactType: "report_title",
    objectId: "",
    current: { title: report.data.value?.title || "" },
  };
  sideTab.value = "discuss";
}
function discussSection(section: any) {
  discussionScope.value = {
    artifactType: "section_title",
    objectId: section.title,
    current: { title: section.title },
  };
  sideTab.value = "discuss";
}
function comparisonHandoff(payload: any) {
  incrementalMaterialIds.value = payload.material_ids || [];
  sourceComparisonId.value = payload.comparison_id || null;
  updateReason.value = payload.update_reason || "";
  comparisonOpen.value = false;
  incrementOpen.value = true;
}
async function saveTitle(event: FocusEvent) {
  const value = (event.target as HTMLElement).innerText.trim();
  if (!value || value === report.data.value?.title) return;
  saveState.value = "正在保存";
  try {
    await api(`/api/reports/${reportId}`, jsonInit("PUT", { title: value }));
    if (report.data.value) report.data.value.title = value;
    saveState.value = "已保存";
  } catch {
    saveState.value = "保存失败";
  }
}
async function saveSection(section: any, index: number, event: FocusEvent) {
  const visible = (event.target as HTMLElement).innerText.trim();
  if (!visible) return;
  saveState.value = "正在保存";
  try {
    const result = await api<any>(
      `/api/reports/${reportId}/sections`,
      jsonInit("PUT", {
        old_title: section.title,
        new_title: visible,
        section_index: index + 1,
      }),
    );
    section.title = result.title;
    section.display_title = result.display_title;
    saveState.value = "已保存";
  } catch {
    saveState.value = "保存失败";
  }
}
async function saveSentence(sentence: Sentence, event: FocusEvent) {
  const value = (event.target as HTMLElement).innerText.trim();
  if (value === sentence.content) return;
  saveState.value = "正在保存";
  try {
    const result = await api<any>(
      `/api/reports/${reportId}/sentences/${sentence.id}`,
      jsonInit("PUT", { content: value }),
    );
    sentence.content = result.content || value;
    saveState.value = "已保存";
  } catch {
    saveState.value = "保存失败";
  }
}
async function finalize(force = false) {
  try {
    await api(`/api/reports/${reportId}/finalize`, jsonInit("POST", { force }));
    await queryClient.invalidateQueries({ queryKey: ["report", reportId] });
  } catch (error: any) {
    if (
      error?.payload?.error === "QA_BLOCKING" &&
      confirm("仍有阻断性质量问题。确认在知情情况下完成审核？")
    )
      return finalize(true);
    alert(error.message || "审核失败");
  }
}
async function createIncremental() {
  const form = new FormData();
  form.set("update_reason", updateReason.value);
  form.set("existing_material_ids", incrementalMaterialIds.value.join(","));
  if (sourceComparisonId.value)
    form.set("source_comparison_id", String(sourceComparisonId.value));
  for (const file of incrementalFiles.value?.files || [])
    form.append("files", file);
  try {
    const result = await api<any>(
      `/api/reports/${reportId}/incremental/tasks`,
      { method: "POST", body: form },
    );
    location.href = `/tasks/${result.task_id}`;
  } catch (error: any) {
    alert(error.message || "创建增量任务失败");
  }
}
async function reviewApplied() {
  await queryClient.invalidateQueries({ queryKey: ["report", reportId] });
  versionOpen.value = false;
}
async function createSnapshot() {
  try {
    await api(
      `/api/reports/${reportId}/versions`,
      jsonInit("POST", { change_summary: "手动生成版本快照" }),
    );
    await queryClient.invalidateQueries({ queryKey: ["report", reportId] });
  } catch (error: any) {
    alert(error.message || "生成版本快照失败");
  }
}
function sourceClass(sentence: Sentence) {
  const fact =
    sentence.sources?.length ||
    sentence.facts?.length ||
    sentence.fact_ids?.length ||
    sentence.source_level === "MATERIAL_FACT";
  const inference =
    sentence.inferences?.length ||
    sentence.inference_ids?.length ||
    ["MATERIAL_INFERENCE", "EXTERNAL_INFORMATION"].includes(
      sentence.source_level,
    );
  return fact && inference
    ? "mixed"
    : inference
      ? "inference"
      : fact
        ? "fact"
        : "";
}
function confidence(item: any) {
  return (
    { high: "高", medium: "中", low: "低" }[
      String(item.confidence_level || "").toLowerCase()
    ] || "需人工复核"
  );
}
</script>

<template>
  <div v-if="report.data.value" class="report-editor" :class="`mode-${mode}`">
    <header class="editor-toolbar">
      <div class="toolbar-left">
        <RouterLink
          v-if="report.data.value.task_id"
          :to="`/tasks/${report.data.value.task_id}`"
          class="btn tertiary"
          >返回任务</RouterLink
        >
        <div>
          <b>{{ report.data.value.title }}</b
          ><span>{{ saveState }}</span>
        </div>
      </div>
      <div class="toolbar-actions">
        <div class="segmented">
          <button :class="{ active: mode === 'edit' }" @click="mode = 'edit'">
            编辑</button
          ><button
            :class="{ active: mode === 'trace' }"
            @click="mode = 'trace'"
          >
            溯源
          </button>
        </div>
        <StatusBadge :stage="report.data.value.status" /><button
          class="btn"
          @click="versionOpen = true"
        >
          版本审阅</button
        ><button class="btn" @click="comparisonOpen = true">新增材料对比</button
        ><button class="btn" @click="incrementOpen = !incrementOpen">
          增量更新</button
        ><button class="btn primary" @click="finalize(false)">审核通过</button
        ><a class="btn" :href="`/api/reports/${reportId}/export`">导出 Word</a>
      </div>
    </header>

    <section v-if="incrementOpen" class="drawer-strip">
      <div>
        <h2>基于当前版本增量更新</h2>
        <p>可新增材料，也可仅说明本次修改目标；历史版本会自动保留。</p>
      </div>
      <select v-model="incrementalMaterialIds" multiple size="4">
        <option
          v-for="item in materials.data.value || []"
          :key="item.id"
          :value="item.id"
        >
          {{ item.filename }}
        </option></select
      ><input ref="incrementalFiles" type="file" multiple /><textarea
        v-model="updateReason"
        rows="3"
        placeholder="本次更新说明"
      ></textarea>
      <div class="button-row">
        <button class="btn primary" @click="createIncremental">
          创建增量轮次</button
        ><button class="btn tertiary" @click="incrementOpen = false">
          取消
        </button>
      </div>
    </section>

    <VersionReviewWorkspace
      v-if="versionOpen"
      :report-id="reportId"
      :report-title="report.data.value.title"
      :versions="report.data.value.versions || []"
      :initial-version="
        route.query.version ? Number(route.query.version) : null
      "
      @close="versionOpen = false"
      @applied="reviewApplied"
      @snapshot="createSnapshot"
    />
    <MaterialComparisonWorkspace
      v-if="comparisonOpen"
      :report-id="reportId"
      :versions="report.data.value.versions || []"
      @close="comparisonOpen = false"
      @handoff="comparisonHandoff"
    />

    <div class="editor-grid">
      <aside class="toc-panel">
        <h3>目录</h3>
        <a
          v-for="(section, index) in report.data.value.sections"
          :key="section.title"
          :href="`#section-${index}`"
          >{{ section.display_title || section.title }}</a
        >
        <div class="trace-key">
          <span class="fact"></span>事实依据<span class="inference"></span
          >分析推论<span class="mixed"></span>混合依据
        </div>
      </aside>
      <main class="paper">
        <h1
          contenteditable
          spellcheck="false"
          @click="discussTitle"
          @blur="saveTitle"
        >
          {{ report.data.value.title }}
        </h1>
        <template
          v-for="(section, sectionIndex) in report.data.value.sections"
          :key="section.title"
          ><h2
            :id="`section-${sectionIndex}`"
            contenteditable
            spellcheck="false"
            @click="discussSection(section)"
            @blur="saveSection(section, sectionIndex, $event)"
          >
            {{ section.display_title || section.title }}
          </h2>
          <template
            v-for="(paragraph, paragraphIndex) in section.paragraphs"
            :key="paragraphIndex"
            ><template
              v-for="sentence in paragraph.sentences"
              :key="sentence.id"
              ><h3
                v-if="sentence.source_level === 'SUBHEADING'"
                class="sentence subheading"
                :class="[
                  sourceClass(sentence),
                  { selected: selected?.id === sentence.id },
                ]"
                contenteditable
                spellcheck="false"
                @click="choose(sentence)"
                @blur="saveSentence(sentence, $event)"
              >
                {{ sentence.display_content || sentence.content }}
              </h3></template
            >
            <p
              v-if="
                paragraph.sentences.some(
                  (sentence) => sentence.source_level !== 'SUBHEADING',
                )
              "
            >
              <span
                v-for="sentence in paragraph.sentences.filter(
                  (sentence) => sentence.source_level !== 'SUBHEADING',
                )"
                :key="sentence.id"
                class="sentence"
                :class="[
                  sourceClass(sentence),
                  {
                    selected: selected?.id === sentence.id,
                    excluded: sentence.selected === false,
                  },
                ]"
                contenteditable
                spellcheck="false"
                @click="choose(sentence)"
                @blur="saveSentence(sentence, $event)"
                >{{ sentence.content }}</span
              >
            </p></template
          ></template
        >
      </main>
      <aside class="context-panel">
        <div class="tabs">
          <button
            class="tab"
            :class="{ active: sideTab === 'evidence' }"
            @click="sideTab = 'evidence'"
          >
            证据</button
          ><button
            class="tab"
            :class="{ active: sideTab === 'qa' }"
            @click="sideTab = 'qa'"
          >
            质检</button
          ><button
            class="tab"
            :class="{ active: sideTab === 'discuss' }"
            @click="sideTab = 'discuss'"
          >
            讨论
          </button>
        </div>
        <div v-if="sideTab === 'evidence'" class="panel-body">
          <template v-if="currentDetails"
            ><p class="selected-quote">{{ currentDetails.content }}</p>
            <section
              v-for="source in currentDetails.sources ||
              currentDetails.facts ||
              []"
              :key="source.fact_id || source.id"
              class="evidence-card"
            >
              <span class="badge running">材料事实</span
              ><b>{{ source.content }}</b>
              <blockquote
                v-for="evidence in source.evidence || []"
                :key="evidence.quote"
              >
                {{ evidence.source_file
                }}{{ evidence.page ? ` 第 ${evidence.page} 页` : "" }}<br />{{
                  evidence.quote
                }}
              </blockquote>
            </section>
            <section
              v-for="item in currentDetails.inferences || []"
              :key="item.inference_id || item.id"
              class="evidence-card inference-card"
            >
              <span class="badge success"
                >分析推论 · {{ confidence(item) }}</span
              ><b>{{ item.content }}</b>
              <p>依据事实：{{ item.based_fact_ids?.join("、") || "待核验" }}</p>
              <blockquote v-if="item.reasoning_chain">
                {{ item.reasoning_chain }}
              </blockquote>
            </section>
            <div
              v-if="
                !(
                  currentDetails.sources?.length ||
                  currentDetails.facts?.length ||
                  currentDetails.inferences?.length
                )
              "
              class="empty"
            >
              <div><strong>没有绑定依据</strong>该句可能是结构或过渡表达。</div>
            </div></template
          >
          <div v-else class="empty">
            <div>
              <strong>选择一句正文</strong
              >{{
                mode === "trace"
                  ? "查看对应事实、原文和分析推论。"
                  : "点击正文后可查看依据。"
              }}
            </div>
          </div>
        </div>
        <div v-else-if="sideTab === 'qa'" class="panel-body">
          <article
            v-for="(issue, index) in qaIssues"
            :key="index"
            class="qa-item"
          >
            <span class="badge warning">{{ issue.type || "质量问题" }}</span>
            <p>{{ issue.note || issue.quote || issue.message }}</p>
          </article>
          <div v-if="!qaIssues.length" class="empty">
            <div>
              <strong>暂无质量问题</strong>确定性修复与语义建议会在此分级呈现。
            </div>
          </div>
        </div>
        <div v-else class="panel-body">
          <ReviewCopilot
            :task-id="report.data.value.task_id"
            :report-id="reportId"
            :artifact-type="discussionTarget.artifactType"
            :object-id="discussionTarget.objectId"
            :current="discussionTarget.current"
            @applied="
              queryClient.invalidateQueries({ queryKey: ['report', reportId] })
            "
          />
        </div>
      </aside>
    </div>
  </div>
  <div v-else class="loading-line"></div>
</template>

<style scoped>
.report-editor {
  min-height: calc(100vh - var(--header-height));
  background: #eef0f3;
}
.editor-toolbar {
  position: sticky;
  top: var(--header-height);
  z-index: 15;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 58px;
  padding: 9px 20px;
  background: #fffffff5;
  border-bottom: 1px solid var(--color-border);
}
.toolbar-left,
.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.toolbar-left > div b,
.toolbar-left > div span {
  display: block;
}
.toolbar-left > div span {
  color: var(--color-muted);
  font-size: 11px;
}
.segmented {
  display: flex;
  padding: 2px;
  border: 1px solid var(--color-border-strong);
  border-radius: 6px;
}
.segmented button {
  padding: 5px 10px;
  border: 0;
  border-radius: 4px;
  background: transparent;
}
.segmented button.active {
  color: var(--color-primary);
  background: var(--color-primary-soft);
  font-weight: 650;
}
.drawer-strip {
  display: grid;
  grid-template-columns: minmax(250px, 1fr) 260px 220px minmax(260px, 1fr) auto;
  align-items: end;
  gap: 14px;
  padding: 20px 24px;
  background: #fff;
  border-bottom: 1px solid var(--color-border);
}
.drawer-strip h2,
.drawer-strip p {
  margin-bottom: 2px;
}
.editor-grid {
  display: grid;
  grid-template-columns: 220px minmax(640px, 900px) 340px;
  justify-content: center;
  gap: 22px;
  padding: 24px;
}
.toc-panel,
.context-panel {
  position: sticky;
  top: calc(var(--header-height) + 82px);
  align-self: start;
  max-height: calc(100vh - 166px);
  overflow: auto;
  background: #fff;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}
.toc-panel {
  padding: 18px 14px;
}
.toc-panel h3 {
  padding: 0 8px;
}
.toc-panel > a {
  display: block;
  padding: 7px 8px;
  color: var(--color-muted);
  font-size: 12px;
}
.toc-panel > a:hover {
  color: var(--color-primary);
  background: var(--color-primary-soft);
}
.trace-key {
  display: grid;
  grid-template-columns: 14px 1fr;
  gap: 8px;
  margin-top: 20px;
  padding: 14px 8px;
  border-top: 1px solid var(--color-border);
  color: var(--color-muted);
  font-size: 11px;
}
.trace-key span {
  width: 11px;
  height: 11px;
  margin-top: 3px;
  border: 1px solid transparent;
  border-radius: 2px;
}
.trace-key .fact {
  border-color: #7fa9ca;
  background: #dcecf9;
}
.trace-key .inference {
  border-color: #7cab88;
  background: #e1f1e5;
}
.trace-key .mixed {
  border-color: #809d9a;
  background: linear-gradient(90deg, #dcecf9 50%, #e1f1e5 50%);
}
.paper {
  min-height: 1120px;
  padding: 72px 78px 100px;
  background: #fff;
  border: 1px solid #dfe2e6;
  border-radius: 3px;
  box-shadow: var(--shadow-paper);
  font-family: var(--font-doc);
  color: #17191c;
}
.paper > h1 {
  margin-bottom: 44px;
  text-align: center;
  font-size: 25px;
  line-height: 1.5;
}
.paper > h2 {
  margin: 32px 0 18px;
  font-size: 19px;
  line-height: 1.6;
}
.paper h3 {
  margin: 22px 0 10px;
  font-size: 17px;
  line-height: 1.7;
}
.paper p {
  margin: 0 0 14px;
  text-align: justify;
  text-indent: 2em;
  font-size: 16px;
  line-height: 1.95;
}
.sentence {
  outline: none;
}
.sentence.excluded {
  opacity: 0.45;
  text-decoration: line-through;
}
.mode-trace .sentence {
  cursor: pointer;
  box-decoration-break: clone;
  -webkit-box-decoration-break: clone;
  transition:
    background var(--motion-fast),
    box-shadow var(--motion-fast);
}
.mode-trace .sentence.fact {
  background: #dcecf9;
  box-shadow: inset 0 -2px #6293ba;
}
.mode-trace .sentence.inference {
  background: #e1f1e5;
  box-shadow: inset 0 -2px #679c74;
}
.mode-trace .sentence.mixed {
  background: linear-gradient(90deg, #dcecf9 0 50%, #e1f1e5 50% 100%);
  box-shadow: inset 0 -2px #718f8a;
}
.mode-trace .sentence.selected {
  box-shadow:
    0 0 0 2px #245b9e,
    inset 0 -2px #245b9e;
}
.context-panel .tabs {
  padding: 0 16px;
}
.panel-body {
  padding: 16px;
}
.selected-quote {
  padding: 12px;
  background: var(--color-surface-soft);
  border-left: 2px solid var(--color-primary);
}
.evidence-card,
.qa-item {
  padding: 14px 0;
  border-bottom: 1px solid var(--color-border);
}
.evidence-card > b {
  display: block;
  margin: 9px 0;
}
.evidence-card blockquote {
  margin: 8px 0;
  padding: 10px;
  border-left: 2px solid #a9b8c7;
  background: var(--color-surface-soft);
  color: var(--color-muted);
  font-size: 12px;
}
.inference-card {
  border-left: 2px solid #679c74;
  padding-left: 10px;
}
.qa-item p {
  margin: 8px 0;
}
.subheading {
  text-indent: 0 !important;
}
@media (max-width: 1350px) {
  .editor-grid {
    grid-template-columns: 190px minmax(600px, 820px) 300px;
    gap: 14px;
  }
  .paper {
    padding-inline: 58px;
  }
  .toolbar-actions .badge {
    display: none;
  }
}
@media (max-width: 1100px) {
  .editor-grid {
    grid-template-columns: 190px minmax(0, 1fr);
  }
  .context-panel {
    display: none;
  }
  .drawer-strip {
    grid-template-columns: 1fr 1fr;
  }
  .toolbar-actions > .btn:not(.primary) {
    display: none;
  }
}
@media (max-width: 800px) {
  .toc-panel {
    display: none;
  }
  .editor-grid {
    grid-template-columns: 1fr;
    padding: 12px;
  }
  .paper {
    padding: 44px 30px;
  }
  .editor-toolbar {
    align-items: flex-start;
  }
  .toolbar-left > div {
    display: none;
  }
  .drawer-strip {
    grid-template-columns: 1fr;
  }
}
</style>
