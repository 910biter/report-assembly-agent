<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { api } from "@/api/http";

const props = defineProps<{
  taskId: string;
  reportId?: number;
  runRevision?: number;
}>();
const artifactType = ref("task_brief");
const search = ref("");
const offset = ref(0);
const selected = ref<any>(null);
const pageSize = 40;
const workspace = useQuery({
  queryKey: ["review-workspace", props.taskId, artifactType, search, offset],
  queryFn: () =>
    api<any>(
      `/api/tasks/${props.taskId}/review-workspace?artifact_type=${encodeURIComponent(artifactType.value)}&q=${encodeURIComponent(search.value)}&offset=${offset.value}&limit=${pageSize}`,
    ),
  staleTime: 5000,
});
const items = computed(() => workspace.data.value?.items || []);
watch(
  items,
  (value) => {
    if (!value.length || (selected.value && !value.some(
      (item: any) => item.object_id === selected.value.object_id,
    ))) selected.value = null;
  },
  { immediate: true },
);
watch([artifactType, search], () => {
  offset.value = 0;
  selected.value = null;
});

function chooseType(value: string) {
  artifactType.value = value;
}
function locateQualityIssue(item: any) {
  if (!props.reportId) return;
  const params = new URLSearchParams({ panel: "qa" });
  if (item?.current?.sentence_id) params.set("qa_sentence", String(item.current.sentence_id));
  else if (item?.current?.section) params.set("qa_section", String(item.current.section));
  location.href = `/reports/${props.reportId}?${params.toString()}`;
}
function discussWithAssistant(item: any) {
  window.dispatchEvent(new CustomEvent("ira:assistant-focus", {
    detail: { taskId: props.taskId, artifact: item },
  }));
}
function toggleItem(item: any) {
  selected.value = selected.value?.object_id === item.object_id ? null : item;
}
const fieldLabels: Record<string, string> = {
  theme: "报告主题", requirements: "报告要求", task_intent: "任务目标",
  content: "内容", title: "标题", summary: "摘要", objective: "本章目的",
  core_question: "核心问题", core_message: "核心观点", narrative_logic: "组织逻辑",
  chapters: "章节安排", chapter_plans: "章节安排", target_words: "目标篇幅",
  material_role: "材料角色", claim_support: "能够证明", allowed_usage: "适合用途",
  forbidden_usage: "使用边界", missing_information: "缺失信息", dimension: "分析维度",
  fact_type: "事实类型", confidence_level: "可信程度", reasoning_chain: "判断依据",
  based_fact_ids: "依据事实", section: "所在章节", quote: "问题原文", note: "问题说明",
  message: "问题说明", severity: "影响程度", problem_type: "问题类型",
};
const detailFields: Record<string, string[]> = {
  task_brief: ["theme", "requirements", "task_intent"],
  material_role: ["summary", "material_role", "claim_support", "allowed_usage", "forbidden_usage", "missing_information"],
  analysis_plan: ["objective", "core_question", "dimensions", "required_dimensions", "narrative_logic"],
  fact: ["content", "dimension", "fact_type"],
  inference: ["content", "confidence_level", "reasoning_chain", "based_fact_ids"],
  final_plan: ["objective", "core_message", "narrative_logic", "chapters", "chapter_plans", "target_words"],
  narrative_plan: ["section", "core_message", "objective", "narrative_logic", "subsections", "paragraphs"],
  qa_issue: ["problem_type", "severity", "section", "quote", "note", "message"],
  comparison_item: ["content", "summary", "change_type", "relation_type", "section"],
};
function readable(value: any): string {
  if (value == null || value === "") return "";
  if (Array.isArray(value)) {
    return value.map((item: any, index: number) => {
      if (item == null) return "";
      if (typeof item !== "object") return String(item);
      const title = item.display_title || item.title || item.name || item.content || item.summary;
      return title ? `${index + 1}. ${title}` : "";
    }).filter(Boolean).join("\n");
  }
  if (typeof value === "object") {
    return ["title", "content", "summary", "objective", "core_message"]
      .map((key) => value[key]).filter(Boolean).join("\n");
  }
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
}
function detailRows(item: any) {
  const current = item?.current || {};
  const preferred = detailFields[item?.artifact_type] || ["title", "content", "summary", "section", "note"];
  return preferred
    .map((key) => ({ key, label: fieldLabels[key] || "相关内容", value: readable(current[key]) }))
    .filter((row) => row.value);
}
</script>

<template>
  <section class="collaboration-shell">
    <header class="collaboration-head">
      <div>
        <h2>协作审阅</h2>
        <p>
          随时讨论阶段产物。系统继续后台运行，批准的语义修改会生成候选版本供你比较。
        </p>
      </div>
      <span class="badge">阶段产物</span>
    </header>
    <div class="collaboration-grid">
      <aside class="artifact-groups">
        <button
          v-for="group in workspace.data.value?.groups || []"
          :key="group.artifact_type"
          :disabled="!group.available"
          :class="{ active: artifactType === group.artifact_type }"
          @click="chooseType(group.artifact_type)"
        >
          <span>{{ group.label }}</span
          ><b>{{ group.count }}</b>
        </button>
      </aside>
      <section class="artifact-browser">
        <div class="browser-tools">
          <input v-model="search" placeholder="搜索当前阶段产物" /><span
            >{{ workspace.data.value?.total || 0 }} 项</span
          >
        </div>
        <div v-if="items.length" class="artifact-list">
          <article
            v-for="item in items"
            :key="`${item.artifact_type}:${item.object_id}`"
            :class="{ active: selected?.object_id === item.object_id }"
          >
            <button type="button" @click="toggleItem(item)">
              <span class="artifact-heading"><b>{{ item.title }}</b><i>{{ selected?.object_id === item.object_id ? "收起" : "查看" }}</i></span>
              <span v-if="selected?.object_id !== item.object_id">{{ item.summary || "打开查看详情" }}</span>
            </button>
            <div v-if="selected?.object_id === item.object_id" class="inline-review">
              <dl v-if="detailRows(item).length">
                <template v-for="row in detailRows(item)" :key="row.key">
                  <dt>{{ row.label }}</dt><dd>{{ row.value }}</dd>
                </template>
              </dl>
              <p v-else>该产物已形成，可交给助手结合任务上下文解释。</p>
              <div class="inline-actions">
                <button
                  v-if="item.artifact_type === 'qa_issue' && reportId && (item.current?.sentence_id || item.current?.section)"
                  class="btn"
                  type="button"
                  @click="locateQualityIssue(item)"
                >定位到报告</button>
                <button class="btn primary" type="button" @click="discussWithAssistant(item)">引用并讨论</button>
              </div>
            </div>
          </article>
        </div>
        <div v-else class="empty">
          <div>
            <strong>该阶段尚无产物</strong>系统完成相应阶段后会自动出现在这里。
          </div>
        </div>
        <div v-if="(workspace.data.value?.total || 0) > pageSize" class="pager">
          <button
            class="btn"
            :disabled="offset === 0"
            @click="offset = Math.max(0, offset - pageSize)"
          >
            上一页</button
          ><span
            >{{ offset + 1 }}–{{
              Math.min(offset + pageSize, workspace.data.value.total)
            }}</span
          ><button
            class="btn"
            :disabled="offset + pageSize >= workspace.data.value.total"
            @click="offset += pageSize"
          >
            下一页
          </button>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.collaboration-shell {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  height: min(780px, calc(100vh - 190px));
  min-height: 600px;
  overflow: hidden;
  background: #fff;
  border: 1px solid var(--color-border);
}
.collaboration-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 20px 24px;
  border-bottom: 1px solid var(--color-border);
}
.collaboration-head h2,
.collaboration-head p {
  margin: 0;
}
.collaboration-head p {
  margin-top: 4px;
  color: var(--color-muted);
}
.collaboration-grid {
  display: grid;
  grid-template-columns: 190px minmax(0, 1fr);
  min-height: 0;
  overflow: hidden;
}
.artifact-groups {
  border-right: 1px solid var(--color-border);
}
.artifact-groups {
  padding: 12px;
  overflow: auto;
}
.artifact-groups button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 10px 12px;
  text-align: left;
  border: 0;
  border-left: 2px solid transparent;
  background: transparent;
  color: var(--color-muted);
}
.artifact-groups button.active {
  border-left-color: var(--color-primary);
  background: var(--color-primary-soft);
  color: var(--color-primary);
}
.artifact-groups button:disabled {
  opacity: 0.4;
}
.artifact-groups b {
  font-size: 12px;
}
.artifact-browser {
  display: grid;
  grid-template-rows: auto 1fr auto;
  min-height: 0;
  min-width: 0;
}
.browser-tools {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  border-bottom: 1px solid var(--color-border);
}
.browser-tools input {
  min-width: 0;
  flex: 1;
}
.browser-tools span {
  color: var(--color-muted);
  font-size: 12px;
}
.artifact-list {
  overflow: auto;
}
.artifact-list button {
  display: grid;
  width: 100%;
  gap: 4px;
  padding: 13px 14px;
  text-align: left;
  border: 0;
  border-bottom: 1px solid var(--color-border);
  background: #fff;
}
.artifact-heading { display:flex; align-items:center; justify-content:space-between; gap:16px; }
.artifact-heading i { color:var(--color-primary); font-size:12px; font-style:normal; font-weight:500; }
.artifact-list article.active {
  background: #f1f6fb;
  box-shadow: inset 2px 0 var(--color-primary);
}
.artifact-list article.active button { background: transparent; }
.artifact-list span {
  display: -webkit-box;
  overflow: hidden;
  color: var(--color-muted);
  font-size: 12px;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.inline-review {
  display:grid;
  gap:14px;
  padding:2px 18px 18px;
  border-bottom:1px solid var(--color-border);
  background:#f8fafc;
}
.inline-review dl {
  display: grid;
  grid-template-columns: 110px minmax(0, 1fr);
  margin: 0;
  padding:12px 0 0;
  font-size:13px;
}
.inline-review dt,
.inline-review dd {
  margin: 0;
  padding: 5px 0;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.inline-review dt { color: var(--color-faint); }
.inline-review dd { color: var(--color-text); }
.inline-review p { margin:0; color:var(--color-muted); }
.inline-actions { display:flex; justify-content:flex-end; gap:8px; }
.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 10px;
  border-top: 1px solid var(--color-border);
}
.pager span {
  color: var(--color-muted);
  font-size: 12px;
}
@media (max-width: 1100px) {
  .collaboration-shell { height: auto; max-height: none; overflow: visible; }
  .collaboration-grid {
    grid-template-columns: 160px minmax(0, 1fr);
    min-height: 640px;
    overflow: visible;
  }
}
</style>
