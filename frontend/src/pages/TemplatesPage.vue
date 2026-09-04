<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { api, jsonInit, uploadForm } from "@/api/http";
import UiPageHeader from "@/components/ui/UiPageHeader.vue";
import UiPanel from "@/components/ui/UiPanel.vue";
import UiSectionHeader from "@/components/ui/UiSectionHeader.vue";
import UiTabs from "@/components/ui/UiTabs.vue";
import UiButton from "@/components/ui/UiButton.vue";

const qc = useQueryClient();
const upload = ref<HTMLInputElement>();
const selected = ref<any>(null);
const schema = ref<any>(null);
const tab = ref("document");
const error = ref("");
const editingName = ref("");
const uploading = ref(false);
const uploadPercent = ref(0);
const learningJob = ref<any>(null);
let pollTimer: number | undefined;
const variants = useQuery({
  queryKey: ["templates"],
  queryFn: () => api<any[]>("/api/style/variants"),
});
const action = useMutation({
  mutationFn: ({ id, op }: { id: number; op: string }) =>
    api(`/api/style/variants/${id}${op === "delete" ? "" : `/${op}`}`, {
      method: op === "delete" ? "DELETE" : "POST",
    }),
  onSuccess: () => qc.invalidateQueries({ queryKey: ["templates"] }),
});
async function inspect(item: any) {
  selected.value = await api(`/api/style/variants/${item.id}/profile`);
  editingName.value = selected.value.name || "";
  schema.value = null;
  try {
    schema.value = await api(`/api/style/variants/${item.id}/template-schema`);
  } catch {
    schema.value = { error: "该模板暂无结构化 Schema" };
  }
}
function remove(item: any) {
  if (confirm(`删除模板“${item.name || item.id}”？`))
    action.mutate({ id: item.id, op: "delete" });
}
function role(value: any, key: string) {
  return (
    value?.style?.roles?.[key] ||
    value?.roles?.[key] ||
    value?.styles?.[key] ||
    value?.[key] ||
    {}
  );
}
function known(value: any) {
  return value !== undefined &&
    value !== null &&
    value !== "" &&
    value !== "unknown"
    ? value
    : "";
}
function format(value: any) {
  if (!value || typeof value !== "object") return "尚未识别";
  const run = value.run || {};
  const font =
    known(run.font_east_asia) ||
    known(run.font_ascii) ||
    known(value.font?.name) ||
    known(value.font_name) ||
    (typeof value.font === "string" ? known(value.font) : "");
  const size =
    known(run.font_size_pt) ||
    known(value.font?.size_pt) ||
    known(value.size_pt) ||
    known(value.size);
  const rawAlign = known(value.paragraph?.alignment) || known(value.alignment);
  const align =
    (
      {
        center: "居中",
        justify: "两端对齐",
        left: "左对齐",
        right: "右对齐",
      } as any
    )[rawAlign] || rawAlign;
  const parts = [font, size ? `${size}pt` : "", align].filter(Boolean);
  return parts.length ? parts.join(" / ") : "尚未识别";
}
function confidence(key: string) {
  const value = selected.value?.profile_confidence?.[key];
  if (value === "unavailable") return "暂无可用结果";
  return ({ high: "高", medium: "中", low: "低" } as any)[value] || "未完成";
}
async function saveName() {
  const name = editingName.value.trim();
  if (!name) {
    error.value = "模板名称不能为空";
    return;
  }
  await api(
    `/api/style/variants/${selected.value.id}`,
    jsonInit("PATCH", { name }),
  );
  selected.value.name = name;
  await qc.invalidateQueries({ queryKey: ["templates"] });
}
const editorialRows = computed(() =>
  [
    ["语气", selected.value?.writing_style?.tone],
    ["句式", selected.value?.writing_style?.sentence_pattern],
    ["分析方式", selected.value?.writing_style?.analysis_style],
    ["信息推进", selected.value?.writing_patterns?.information_progression],
    ["段内组织", selected.value?.writing_patterns?.paragraph_architecture],
    ["衔接方式", selected.value?.writing_patterns?.transition_style],
    ["具体程度", selected.value?.writing_patterns?.specificity_preference],
  ].filter((item) => item[1]),
);
const realizationRows = computed(() =>
  [
    ["事实表达", selected.value?.writing_patterns?.fact_expression],
    ["判断表达", selected.value?.writing_patterns?.judgment_expression],
    ["事实到判断", selected.value?.writing_patterns?.fact_judgment_transition],
    ["信息取舍", selected.value?.writing_patterns?.information_compression],
    ["依据表述", selected.value?.writing_patterns?.attribution_style],
    ["分析框架", selected.value?.reasoning_profile?.analysis_framework],
    ["风险措辞", selected.value?.reasoning_profile?.risk_expression],
    ["建议表达", selected.value?.reasoning_profile?.suggestion_style],
    [
      "事实选择",
      selected.value?.writing_patterns?.material_realization?.fact_selection,
    ],
    [
      "细节保留",
      selected.value?.writing_patterns?.material_realization?.detail_retention,
    ],
    [
      "多来源综合",
      selected.value?.writing_patterns?.material_realization
        ?.multi_source_synthesis,
    ],
    [
      "证据到分析",
      selected.value?.writing_patterns?.material_realization?.fact_to_analysis,
    ],
    [
      "证据边界",
      selected.value?.writing_patterns?.material_realization
        ?.boundary_expression,
    ],
    [
      "结构选择",
      selected.value?.writing_patterns?.material_realization?.structure_choice,
    ],
  ].filter((item) => item[1]),
);
const documentRoles = computed(() =>
  [
    ["document_title", "主标题"],
    ["heading_1", "一级标题"],
    ["heading_2", "二级标题"],
    ["heading_3", "三级标题"],
    ["body", "正文"],
  ]
    .map(([key, label]) => ({ key, label, value: role(schema.value, key) }))
    .filter((item) => Object.keys(item.value || {}).length),
);
const numberingRows = computed(() => {
  const numbering = schema.value?.style?.numbering || {};
  const roles = numbering.role_levels || {};
  const labels: any = {
    heading_1: "一级标题",
    heading_2: "二级标题",
    heading_3: "三级标题",
  };
  const rows = Object.entries(roles).map(([key, value]: any) => ({
    label: labels[key] || key,
    value: `${value.level_text || "自动编号"} · ${value.number_format || "格式未标注"}`,
  }));
  if (rows.length) return rows;
  return (numbering.patterns || []).map((item: any, index: number) => ({
    label: `编号样式 ${index + 1}`,
    value: item.sample || item.format,
  }));
});
const hierarchyRows = computed(() => {
  const rows: any[] = [];
  const walk = (items: any[], depth = 0) => {
    (items || []).forEach((item) => {
      rows.push({
        depth,
        label: item.text_pattern || `第 ${item.level || depth + 1} 级标题`,
      });
      walk(item.children || [], depth + 1);
    });
  };
  walk(schema.value?.structure?.heading_tree || []);
  return rows.slice(0, 16);
});
const quality = computed(() => schema.value?.quality || {});
const learningBusy = computed(
  () =>
    uploading.value ||
    ["queued", "running"].includes(learningJob.value?.status),
);
const phaseLabel = computed(
  () =>
    (
      ({
        queued: "等待开始",
        parsing: "解析文件",
        classifying: "识别语言与结构",
        profiling: "生成模板画像",
        completed: "学习完成",
        failed: "学习失败",
      }) as any
    )[learningJob.value?.phase] || "准备上传",
);
async function startLearning() {
  const files = Array.from(upload.value?.files || []);
  if (!files.length) {
    error.value = "请先选择模板或成品报告";
    return;
  }
  error.value = "";
  uploading.value = true;
  uploadPercent.value = 0;
  learningJob.value = null;
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  try {
    learningJob.value = await uploadForm<any>(
      "/api/style/analyze-jobs",
      form,
      (loaded, total) =>
        (uploadPercent.value = total ? Math.round((loaded / total) * 100) : 0),
    );
    localStorage.setItem("active-style-learning-job", learningJob.value.id);
    uploadPercent.value = 100;
    await pollLearningJob(learningJob.value.id);
  } catch (e: any) {
    error.value = e.message || "上传失败";
  } finally {
    uploading.value = false;
  }
}
async function pollLearningJob(id: string) {
  window.clearTimeout(pollTimer);
  try {
    learningJob.value = await api(`/api/style/analyze-jobs/${id}`);
    if (learningJob.value.status === "completed") {
      localStorage.removeItem("active-style-learning-job");
      await qc.invalidateQueries({ queryKey: ["templates"] });
      return;
    }
    if (learningJob.value.status === "failed") {
      localStorage.removeItem("active-style-learning-job");
      error.value = learningJob.value.error || "模板学习失败";
      return;
    }
    pollTimer = window.setTimeout(() => pollLearningJob(id), 1000);
  } catch (e: any) {
    if (e?.status === 404) localStorage.removeItem("active-style-learning-job");
    error.value = e.message || "无法获取学习进度";
  }
}
onMounted(() => {
  const id = localStorage.getItem("active-style-learning-job");
  if (id) pollLearningJob(id);
});
onBeforeUnmount(() => window.clearTimeout(pollTimer));
</script>

<template>
  <div class="page-stack template-page">
    <UiPageHeader title="模板中心" />
    <UiPanel class="upload-bar" :padded="false">
      <div class="upload-copy">
        <span class="upload-mark">DOCX</span>
        <div>
          <h2>学习新模板</h2>
          <p>上传 Word 模板或成熟报告，系统会提取可执行的文档与表达画像。</p>
        </div>
      </div>
      <label class="upload-field"
        ><span>选择 DOCX 文件</span
        ><input ref="upload" type="file" accept=".docx" multiple /></label
      ><UiButton
        :loading="learningBusy"
        @click="startLearning"
      >
        {{ learningBusy ? "正在学习…" : "上传并学习" }}
      </UiButton>
    </UiPanel>
    <UiPanel
      v-if="uploading || learningJob"
      class="learning-progress"
      :padded="false"
      aria-live="polite"
    >
      <div class="progress-heading">
        <div>
          <strong>{{ uploading ? "正在上传文件" : phaseLabel }}</strong
          ><span>{{
            uploading
              ? `已上传 ${uploadPercent}%`
              : learningJob?.message || "正在处理"
          }}</span>
        </div>
        <b>{{
          uploading
            ? `${uploadPercent}%`
            : `${learningJob?.progress_percent || 0}%`
        }}</b>
      </div>
      <div class="progress-track">
        <span
          :style="{
            width: `${uploading ? uploadPercent : learningJob?.progress_percent || 0}%`,
          }"
        ></span>
      </div>
      <div v-if="!uploading && learningJob" class="progress-detail">
        <span
          >文件 {{ learningJob.processed_files || 0 }} /
          {{ learningJob.total_files || 0 }}</span
        ><span v-if="learningJob.current_file"
          >当前：{{ learningJob.current_file }}</span
        ><span v-if="learningJob.failed_files" class="error-text"
          >{{ learningJob.failed_files }} 份解析失败</span
        >
      </div>
    </UiPanel>
    <p v-if="error" class="error-text">{{ error }}</p>
    <section class="template-layout">
      <UiPanel class="templates" :padded="false">
        <UiSectionHeader
          title="模板与画像"
          description="选择一个资产查看分层学习结果"
        />
        <div v-if="variants.data.value?.length" class="template-list">
          <article
            v-for="item in variants.data.value"
            :key="item.id"
            class="template-item"
            :class="{ selected: selected?.id === item.id }"
            @click="inspect(item)"
          >
            <div class="paper-preview">
              <span></span><span></span><span></span><span></span>
            </div>
            <div class="template-meta">
              <strong>{{ item.name || `模板 ${item.id}` }}</strong
              ><small
                >{{ item.source_reports?.length || 0 }} 份来源报告 · 画像 v{{
                  item.profile_version || 1
                }}
                · {{ item.exemplar_count || 0 }} 条成文样例</small
              >
            </div>
            <div class="item-actions">
              <span v-if="item.status === 'locked'" class="badge success"
                >默认</span
              ><span v-else-if="item.status === 'confirmed'" class="badge"
                >可用</span
              ><button
                v-if="item.status === 'draft'"
                class="btn tertiary"
                @click.stop="action.mutate({ id: item.id, op: 'confirm' })"
              >
                确认</button
              ><button
                v-if="item.status !== 'locked'"
                class="btn tertiary"
                @click.stop="action.mutate({ id: item.id, op: 'lock' })"
              >
                设为默认</button
              ><button class="btn tertiary danger" @click.stop="remove(item)">
                删除
              </button>
            </div>
          </article>
        </div>
        <div v-else class="empty">
          <div>
            <strong>暂无模板资产</strong>上传 DOCX
            后生成文档Schema与初始报告画像。
          </div>
        </div>
      </UiPanel>
      <UiPanel class="profile-panel" :padded="false">
        <template v-if="selected"
          ><div class="profile-title">
            <div>
              <small>分层报告画像</small>
              <div class="name-editor">
                <input
                  v-model="editingName"
                  maxlength="80"
                  aria-label="模板名称"
                  @keyup.enter="saveName"
                /><button
                  class="btn tertiary"
                  :disabled="
                    !editingName.trim() || editingName.trim() === selected.name
                  "
                  @click="saveName"
                >
                  保存名称
                </button>
              </div>
            </div>
            <span class="badge">v{{ selected.profile_version }}</span>
          </div>
          <UiTabs
            v-model="tab"
            aria-label="模板画像内容"
            :items="[
              { value: 'document', label: '文档版式' },
              { value: 'editorial', label: '语言与成文' },
              { value: 'examples', label: '表达范例' },
            ]"
          />
          <div v-if="tab === 'document'" class="profile-body">
            <div class="confidence-line">
              <span>导出可用性</span
              ><b>{{
                quality.render_ready
                  ? "可直接使用"
                  : `${Math.round((quality.completeness || 0) * 100)}% 已识别`
              }}</b>
            </div>
            <div class="schema-list">
              <div v-for="item in documentRoles" :key="item.key">
                <span>{{ item.label }}</span
                ><b>{{ format(item.value) }}</b>
              </div>
            </div>
            <div v-if="numberingRows.length" class="profile-block">
              <h3>标题编号</h3>
              <dl>
                <template v-for="item in numberingRows" :key="item.label"
                  ><dt>{{ item.label }}</dt>
                  <dd>{{ item.value }}</dd></template
                >
              </dl>
            </div>
            <div v-if="hierarchyRows.length" class="profile-block">
              <h3>识别出的标题层级</h3>
              <div class="hierarchy-list">
                <div
                  v-for="(item, index) in hierarchyRows"
                  :key="index"
                  :style="{ paddingLeft: `${item.depth * 18}px` }"
                >
                  <span>{{ item.depth + 1 }}级</span><b>{{ item.label }}</b>
                </div>
              </div>
            </div>
            <div v-if="quality.unknown_items?.length" class="notice warning">
              <strong>仍有 {{ quality.unknown_items.length }} 项待确认</strong
              ><span>{{ quality.unknown_items.slice(0, 6).join("、") }}</span>
            </div>
            <p class="schema-note">
              导出时优先在原始 DOCX
              的标题与正文锚点中填充，页面、页眉页脚、样式和前置版式继续由原模板保留。
            </p>
          </div>
          <div v-else-if="tab === 'editorial'" class="profile-body">
            <div v-if="editorialRows.length" class="profile-block">
              <h3>
                表达与组织 <span>{{ confidence("editorial_style") }}</span>
              </h3>
              <dl>
                <template v-for="item in editorialRows" :key="item[0]"
                  ><dt>{{ item[0] }}</dt>
                  <dd>{{ item[1] }}</dd></template
                >
              </dl>
            </div>
            <div v-else class="notice warning">
              <strong>成文画像未生成成功</strong
              ><span
                >文档版式和成品统计已经提取，但本次语言归纳没有形成有效结果；空画像不会用于写作。</span
              >
            </div>
            <div v-if="realizationRows.length" class="profile-block">
              <h3>事实与判断的表达</h3>
              <dl>
                <template v-for="item in realizationRows" :key="item[0]"
                  ><dt>{{ item[0] }}</dt>
                  <dd>{{ item[1] }}</dd></template
                >
              </dl>
            </div>
            <div class="profile-block">
              <h3>
                成品统计
                <span
                  >{{
                    selected.writing_patterns?.observed_metrics?.sample_count ||
                    0
                  }}
                  个自然段</span
                >
              </h3>
              <dl>
                <dt>段落长度</dt>
                <dd>
                  中位
                  {{
                    selected.writing_patterns?.observed_metrics?.paragraph_chars
                      ?.p50 || "--"
                  }}
                  字 · 主要区间
                  {{
                    selected.writing_patterns?.observed_metrics?.paragraph_chars
                      ?.p25 || "--"
                  }}–{{
                    selected.writing_patterns?.observed_metrics?.paragraph_chars
                      ?.p75 || "--"
                  }}
                  字
                </dd>
                <dt>每段句数</dt>
                <dd>
                  中位
                  {{
                    selected.writing_patterns?.observed_metrics
                      ?.sentences_per_paragraph?.p50 || "--"
                  }}
                  句
                </dd>
                <dt>句子长度</dt>
                <dd>
                  中位
                  {{
                    selected.writing_patterns?.observed_metrics?.sentence_chars
                      ?.p50 || "--"
                  }}
                  字
                </dd>
                <dt>来源覆盖</dt>
                <dd>
                  {{
                    selected.writing_patterns?.observed_metrics
                      ?.source_report_count ||
                    selected.source_reports?.length ||
                    0
                  }}
                  份成品报告
                </dd>
              </dl>
            </div>
          </div>
          <div v-else class="profile-body">
            <div class="confidence-line">
              <span>可检索成文范例</span
              ><b>{{ selected.exemplar_bank?.length || 0 }} 条</b>
            </div>
            <p class="muted">
              Writer
              只会选取与当前章节目的匹配的范例，匹配不足时不强行注入；范例只用于组织和表达，不复制业务事实。
            </p>
            <div class="example-list">
              <article
                v-for="item in (selected.exemplar_bank || []).slice(0, 12)"
                :key="item.exemplar_id"
              >
                <div>
                  <b>{{ item.section || item.chapter_type || "正文范例" }}</b
                  ><span
                    >{{ item.purpose || item.sample_type || "语言范例" }} ·
                    {{ item.source_report }}</span
                  >
                </div>
                <p>{{ item.content }}</p>
                <small v-if="item.realization_mode"
                  >材料运用：{{ item.realization_mode }}</small
                >
              </article>
            </div>
          </div>
        </template>
        <div v-else class="empty">
          <div><strong>选择一个模板</strong>查看文档版式、语言与成文画像。</div>
        </div>
      </UiPanel>
    </section>
  </div>
</template>

<style scoped>
.upload-bar {
  display: grid;
  grid-template-columns: minmax(300px, 1fr) minmax(260px, 420px) auto;
  align-items: center;
  gap: 16px;
  padding: 20px 24px;
}
.upload-bar h2,
.upload-bar p {
  margin: 0;
}
.template-layout {
  display: grid;
  grid-template-columns: minmax(420px, 0.9fr) minmax(520px, 1.1fr);
  gap: 24px;
}
.templates,
.profile-panel {
  padding: 24px;
}
.template-list {
  border-top: 1px solid var(--color-border);
}
.template-item {
  display: grid;
  grid-template-columns: 58px minmax(0, 1fr) auto;
  gap: 14px;
  align-items: center;
  padding: 14px 8px;
  border-bottom: 1px solid var(--color-border);
  cursor: pointer;
}
.template-item:hover,
.template-item.selected {
  background: var(--color-surface-soft);
}
.template-item.selected {
  box-shadow: inset 3px 0 var(--color-primary);
}
.paper-preview {
  width: 48px;
  height: 64px;
  padding: 10px 7px;
  background: var(--document-paper);
  border: 1px solid var(--border-strong);
}
.paper-preview span {
  display: block;
  height: 2px;
  margin-bottom: 6px;
  background: var(--subtle-foreground);
}
.paper-preview span:first-child {
  width: 70%;
  height: 3px;
  margin-inline: auto;
  background: var(--document-qa-low);
}
.template-meta {
  min-width: 0;
}
.template-meta strong,
.template-meta small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.template-meta small {
  margin: 3px 0;
  color: var(--color-muted);
  font-size: 12px;
}
.item-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}
.item-actions .btn {
  min-height: 28px;
  padding: 2px 6px;
  font-size: 11px;
}
.profile-title {
  display: flex;
  justify-content: space-between;
  gap: 16px;
}
.profile-title > div {
  min-width: 0;
  flex: 1;
}
.profile-title small {
  color: var(--color-primary);
}
.name-editor {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 5px;
}
.name-editor input {
  min-width: 0;
  max-width: 420px;
  font-size: 18px;
  font-weight: 600;
}
.name-editor .btn {
  flex: 0 0 auto;
}
.profile-panel :deep(.ui-tabs) {
  margin-top: 16px;
}
.profile-body {
  display: grid;
  gap: 16px;
  padding-top: 20px;
}
.profile-body > label,
.case-add label {
  display: grid;
  gap: 5px;
  color: var(--color-muted);
  font-size: 12px;
}
.confidence-line {
  display: flex;
  justify-content: space-between;
  padding: 12px 0;
  border-bottom: 1px solid var(--color-border);
}
.schema-list {
  border-top: 1px solid var(--color-border);
}
.schema-list > div {
  display: grid;
  grid-template-columns: 80px 1fr;
  gap: 12px;
  padding: 11px 0;
  border-bottom: 1px solid var(--color-border);
}
.schema-list span {
  color: var(--color-muted);
}
.schema-list b {
  font-weight: 500;
}
.profile-block {
  padding-top: 12px;
  border-top: 1px solid var(--color-border);
}
.profile-block h3 {
  display: flex;
  justify-content: space-between;
}
.profile-block h3 span {
  color: var(--color-muted);
  font-size: 12px;
}
.profile-block dl {
  display: grid;
  grid-template-columns: 90px 1fr;
}
.profile-block dt,
.profile-block dd {
  padding: 8px 0;
  border-bottom: 1px solid var(--color-border);
}
.profile-block dt {
  color: var(--color-muted);
}
.rule-list article {
  display: grid;
  gap: 3px;
  padding: 12px 0;
  border-bottom: 1px solid var(--color-border);
}
.rule-list span {
  color: var(--color-muted);
  font-size: 12px;
}
.case-add {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: end;
  gap: 8px;
}
.compact {
  min-height: 120px;
}
.profile-panel pre {
  max-height: 300px;
  overflow: auto;
  white-space: pre-wrap;
  font-size: 11px;
}
@media (max-width: 1100px) {
  .template-layout {
    grid-template-columns: 1fr;
  }
  .upload-bar {
    grid-template-columns: 1fr auto;
  }
  .upload-bar > div {
    grid-column: 1/-1;
  }
}
@media (max-width: 650px) {
  .upload-bar {
    grid-template-columns: 1fr;
  }
  .template-item {
    grid-template-columns: 48px 1fr;
  }
  .item-actions {
    grid-column: 2;
  }
  .profile-panel {
    padding: 16px;
  }
  .name-editor {
    align-items: stretch;
    flex-direction: column;
  }
}
.example-list {
  border-top: 1px solid var(--color-border);
}
.example-list article {
  padding: 12px 0;
  border-bottom: 1px solid var(--color-border);
}
.example-list article > div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
.example-list span,
.example-list small {
  color: var(--color-muted);
  font-size: 11px;
}
.example-list p {
  display: -webkit-box;
  overflow: hidden;
  margin: 7px 0 0;
  color: var(--color-text);
  font-size: 13px;
  line-height: 1.7;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}
.example-list small {
  display: block;
  margin-top: 5px;
  color: var(--color-warning);
}
.learning-progress {
  display: grid;
  gap: 10px;
  padding: 16px 20px;
}
.progress-heading,
.progress-detail {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.progress-heading > div {
  display: grid;
  gap: 2px;
}
.progress-heading span,
.progress-detail {
  color: var(--color-muted);
  font-size: 12px;
}
.progress-heading > b {
  font-size: 18px;
  font-weight: 600;
}
.progress-track {
  height: 6px;
  overflow: hidden;
  background: var(--color-surface-soft);
  border-radius: 3px;
}
.progress-track span {
  display: block;
  height: 100%;
  background: var(--color-primary);
  transition: width 180ms ease;
}
.progress-detail {
  justify-content: flex-start;
  flex-wrap: wrap;
}
.progress-detail span + span {
  padding-left: 16px;
  border-left: 1px solid var(--color-border);
}
.notice {
  display: grid;
  gap: 5px;
  padding: 14px;
  border-left: 3px solid var(--color-warning);
  background: var(--color-warning-soft);
  color: var(--color-muted);
}
.notice strong {
  color: var(--color-text);
}
.hierarchy-list {
  border-top: 1px solid var(--color-border);
}
.hierarchy-list > div {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-top: 8px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--color-border);
}
.hierarchy-list span {
  min-width: 32px;
  color: var(--color-muted);
  font-size: 11px;
}
.hierarchy-list b {
  font-weight: 500;
}

.page-kicker {
  display: block;
  margin-bottom: 4px;
  color: var(--primary);
  font-size: 10px;
  font-weight: 750;
  letter-spacing: 0.13em;
}
.asset-count {
  padding: 5px 10px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 999px;
  color: var(--nav-foreground);
  background: rgba(255, 255, 255, 0.08);
  font-size: 12px;
  font-weight: 650;
}
.upload-bar {
  min-height: 112px;
  grid-template-columns: minmax(340px, 1fr) minmax(220px, 360px) auto;
  border-color: var(--border-strong);
  background: var(--card);
}
.upload-copy {
  display: flex;
  align-items: center;
  gap: 14px;
}
.upload-copy h2 {
  font-size: 17px;
  letter-spacing: -0.015em;
}
.upload-copy p {
  margin-top: 3px;
  color: var(--muted-foreground);
  font-size: 13px;
}
.upload-mark {
  display: grid;
  place-items: center;
  flex: 0 0 46px;
  width: 46px;
  height: 52px;
  border: 1px solid color-mix(in srgb, var(--primary) 26%, var(--border));
  border-radius: 8px;
  color: var(--primary);
  background: var(--card);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.06em;
  box-shadow: none;
}
.upload-field {
  display: grid;
  gap: 5px;
  padding: 9px 11px;
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-control);
  background: color-mix(in srgb, var(--card) 84%, transparent);
  color: var(--muted-foreground);
  font-size: 11px;
}
.upload-field:focus-within {
  border-color: var(--primary);
  box-shadow: var(--focus-ring);
}
.upload-field input {
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  font-size: 12px;
}
.templates,
.profile-panel {
  border-color: var(--border);
}
.templates {
  padding: 22px 22px 8px;
}
.profile-panel {
  padding: 22px;
  background: var(--card);
}
.template-item {
  margin: 0 -8px;
  padding: 14px 10px;
  border-radius: 8px;
  transition:
    background var(--motion-fast),
    box-shadow var(--motion-fast);
}
.template-item:hover {
  background: color-mix(in srgb, var(--muted) 70%, var(--card));
}
.template-item.selected {
  background: var(--primary-soft);
  box-shadow: inset 3px 0 var(--primary);
}
.paper-preview {
  position: relative;
  overflow: hidden;
  border-radius: 3px;
  box-shadow: var(--shadow-paper);
}
.paper-preview::after {
  content: "";
  position: absolute;
  inset: 0;
  border: 1px solid rgba(255, 255, 255, 0.65);
  pointer-events: none;
}
.profile-body {
  padding-top: 18px;
}
.profile-block {
  padding-top: 16px;
}
.schema-note {
  padding: 12px 14px;
  border-left: 2px solid var(--primary);
  border-radius: 0 7px 7px 0;
  color: var(--muted-foreground);
  background: var(--surface-accent);
  font-size: 12px;
  line-height: 1.7;
}
@media (max-width: 1100px) {
  .upload-bar {
    grid-template-columns: 1fr auto;
  }
  .upload-copy {
    grid-column: 1/-1;
  }
  .upload-field {
    min-width: 0;
  }
}
@media (max-width: 650px) {
  .asset-count {
    display: none;
  }
  .upload-bar {
    grid-template-columns: 1fr;
  }
  .upload-copy {
    grid-column: auto;
  }
  .upload-bar .btn {
    width: 100%;
  }
}
.asset-count {
  border-color: var(--border-strong);
  color: var(--primary);
  background: var(--surface-raised);
}
.upload-bar {
  border-color: var(--border-strong);
  background: var(--card);
}
.asset-count {
  display: none;
}
</style>
