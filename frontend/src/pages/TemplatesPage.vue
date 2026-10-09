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
const selectedFiles = ref<File[]>([]);
const roleSelections = ref<string[][]>([]);
const selected = ref<any>(null);
const schema = ref<any>(null);
const tab = ref("document");
const error = ref("");
const editingName = ref("");
const uploading = ref(false);
const uploadPercent = ref(0);
const learningJob = ref<any>(null);
const roleOptions = [
  { value: "editorial", label: "文体参考" },
  { value: "structure", label: "结构参考" },
  { value: "layout", label: "Word 版式母版" },
];
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
function onFilesChanged(event: Event) {
  const input = event.target as HTMLInputElement;
  selectedFiles.value = Array.from(input.files || []);
  roleSelections.value = selectedFiles.value.map(() => ["editorial"]);
  error.value = "";
}
function rolesFor(_file: File, index: number) {
  return roleSelections.value[index] || [];
}
function roleLabels(roles: string[]) {
  return roles.map(
    (item) => roleOptions.find((option) => option.value === item)?.label || item,
  ).join("、");
}
function roleDisabled(file: File, index: number, roleName: string) {
  if (roleName === "layout") {
    const anotherMasterSelected = selectedFiles.value.some(
      (candidate, candidateIndex) => candidateIndex !== index && rolesFor(candidate, candidateIndex).includes("layout"),
    );
    return !file.name.toLowerCase().endsWith(".docx") || anotherMasterSelected;
  }
  return false;
}
function toggleRole(file: File, index: number, roleName: string, event: Event) {
  const checked = (event.target as HTMLInputElement).checked;
  const current = rolesFor(file, index);
  const next = checked
    ? [...new Set([...current, roleName])]
    : current.filter((item) => item !== roleName);
  if (checked && roleName === "layout" && !file.name.toLowerCase().endsWith(".docx")) {
    error.value = "Word 版式母版必须是 DOCX 文件";
    return;
  }
  if (checked && roleName === "layout" && selectedFiles.value.some(
    (candidate, candidateIndex) => candidateIndex !== index && rolesFor(candidate, candidateIndex).includes("layout"),
  )) {
    error.value = "一次模板学习只能指定一个 Word 版式母版";
    return;
  }
  if (!next.length) {
    error.value = "每份来源至少选择一种学习用途";
    return;
  }
  roleSelections.value = roleSelections.value.map((roles, roleIndex) => roleIndex === index ? next : roles);
  error.value = "";
}
const learningEvidence = computed(
  () => selected.value?.learning_evidence || selected.value?.structure?.learning_evidence || {},
);
const promptSectionRows = computed(() => {
  const labels: Record<string, string> = {
    instruction: "学习规则",
    editorial_reference_samples: "文体样本",
    structural_reference_headings: "结构标题",
    approved_editorial_exemplars: "成文范例",
  };
  const sections = learningEvidence.value.profile_prompt?.sections || {};
  return Object.entries(sections).map(([key, value]: [string, any]) => ({
    label: labels[key] || key,
    selected: value.selected_items || 0,
    candidate: value.candidate_items || 0,
    omitted: value.omitted_items || 0,
  }));
});
function capabilitySummary(roles: any) {
  if (roles?.role_assignment_known === false) return "来源用途未记录";
  const capabilities = [
    roles?.editorial_reference && "文体",
    roles?.structural_reference && "结构",
    roles?.layout_master_available && "Word 版式",
  ].filter(Boolean);
  return capabilities.length ? capabilities.join(" / ") : "能力待学习";
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
const documentShape = computed(() => selected.value?.structure?.document_shape || {});
const documentShapeLabel = computed(() => {
  const labels: Record<string, string> = {
    structured_report: "正式报告",
    research_review: "研究综述",
    article_sections: "分节文章",
    continuous_article: "连续文章",
    message_push: "推送文章",
    news_release: "新闻稿",
  };
  return labels[documentShape.value.kind] || "待任务确定";
});
const headingPolicyLabel = computed(() => {
  const labels: Record<string, string> = { numbered: "带编号标题", plain: "普通标题", none: "不显示标题" };
  return labels[documentShape.value.heading_policy] || "由任务决定";
});
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
        learning: "读取版式母版",
        classifying: "识别语言与结构",
        profiling: "生成模板画像",
        completed: "学习完成",
        failed: "学习失败",
      }) as any
    )[learningJob.value?.phase] || "准备上传",
);
async function startLearning() {
  const files = selectedFiles.value;
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
  form.append(
    "asset_roles_json",
    JSON.stringify(files.map((file, index) => rolesFor(file, index))),
  );
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
          <p>一批来源生成一个模板画像；逐份指定它能提供的学习内容。</p>
        </div>
      </div>
      <div class="upload-controls">
        <label class="upload-field">
          <span>选择参考文件</span>
          <input
            ref="upload"
            type="file"
            accept=".pdf,.docx,.doc,.pptx,.ppt,.xlsx,.xls,.csv,.html,.htm,.md,.txt,.epub"
            multiple
            @change="onFilesChanged"
          />
        </label>
        <p class="role-explainer">
          文体学习与结构参考可多选；Word 版式母版仅限一份 DOCX。默认仅作为文体参考。
        </p>
      </div>
      <div v-if="selectedFiles.length" class="source-role-list">
        <div v-for="(file, index) in selectedFiles" :key="`${file.name}:${file.size}:${index}`" class="source-role-row">
          <strong :title="file.name">{{ file.name }}</strong>
          <label v-for="option in roleOptions" :key="option.value" class="source-role-option">
            <input
              type="checkbox"
              :checked="rolesFor(file, index).includes(option.value)"
              :disabled="roleDisabled(file, index, option.value)"
              @change="toggleRole(file, index, option.value, $event)"
            />
            <span>{{ option.label }}</span>
          </label>
        </div>
      </div>
      <UiButton
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
                >{{ item.source_reports?.length || 0 }} 份来源 ·
                {{ item.document_shape?.kind === 'continuous_article' ? '连续文章' : item.document_shape?.kind === 'message_push' ? '推送文章' : item.document_shape?.kind === 'news_release' ? '新闻稿' : item.document_shape?.kind === 'article_sections' ? '分节文章' : item.document_shape?.kind === 'research_review' ? '研究综述' : '正式报告' }} ·
                {{ capabilitySummary(item.asset_roles) }}</small
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
              <small>模板画像</small>
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
            <div class="profile-block asset-capabilities">
              <h3>模板用途</h3>
              <dl>
                <dt>文体学习</dt><dd>{{ selected.structure?.asset_roles?.role_assignment_known === false ? '来源用途未记录' : selected.structure?.asset_roles?.editorial_reference ? '可用' : '未选择' }}</dd>
                <dt>结构参考</dt><dd>{{ selected.structure?.asset_roles?.role_assignment_known === false ? '来源用途未记录' : !selected.structure?.asset_roles?.structural_reference ? '未选择' : selected.structure?.asset_roles?.structural_reference_available ? `已识别 ${learningEvidence.structure?.observed_headings || 0} 个标题` : '已选择，未识别标题层级' }}</dd>
                <dt>Word 版式母版</dt><dd>{{ selected.structure?.asset_roles?.layout_master_available ? (selected.structure?.asset_roles?.role_assignment_known === false ? '存在，但用途未记录' : '可用') : '未提供，将按任务使用默认版式' }}</dd>
              </dl>
              <p class="schema-note">{{ selected.structure?.asset_roles?.note || '文体、结构与导出版式由系统分别处理。' }}</p>
            </div>
            <div class="profile-block learning-evidence">
              <h3>学习依据</h3>
              <div v-if="learningEvidence.source_count !== undefined">
                <dl>
                <dt>来源文件</dt><dd>{{ learningEvidence.source_count || 0 }} 份</dd>
                <dt>文体自然段</dt><dd>{{ learningEvidence.editorial?.candidate_paragraphs || 0 }} 个候选，保留 {{ learningEvidence.editorial?.stored_examples || 0 }} 个范例</dd>
                <dt>结构标题</dt><dd>{{ learningEvidence.structure?.observed_headings || 0 }} 个</dd>
                <dt>类型识别调用</dt>
                <dd>
                  {{ learningEvidence.topic_probe?.attempts || 0 }} 次，
                  {{ learningEvidence.topic_probe?.actual_tokens || 0 }} /
                  {{ learningEvidence.topic_probe?.budget_tokens || 0 }} tokens
                  <span v-if="learningEvidence.topic_probe?.truncated_sources" class="evidence-warning">
                    {{ learningEvidence.topic_probe.truncated_sources }} 份输入有裁剪
                  </span>
                </dd>
                <dt>画像输入</dt>
                <dd>
                  <template v-if="learningEvidence.profile_prompt?.called">
                    {{ learningEvidence.profile_prompt?.actual_tokens || 0 }} /
                    {{ learningEvidence.profile_prompt?.budget_tokens || 0 }} tokens
                    <span v-if="learningEvidence.profile_prompt?.truncated" class="evidence-warning">存在预算裁剪</span>
                    <span v-else>未发生输入裁剪</span>
                  </template>
                  <span v-else>未调用语义模型，仅提取 DOCX 版式</span>
                </dd>
                </dl>
                <div v-if="promptSectionRows.length" class="prompt-section-audit">
                  <div v-for="item in promptSectionRows" :key="item.label">
                    <span>{{ item.label }}</span>
                    <strong>{{ item.selected }} / {{ item.candidate }}</strong>
                    <small v-if="item.omitted">未纳入 {{ item.omitted }}</small>
                  </div>
                </div>
                <div v-if="learningEvidence.sources?.length" class="learning-source-list">
                  <div v-for="source in learningEvidence.sources" :key="source.filename">
                    <strong>{{ source.filename }}</strong>
                    <span>{{ roleLabels(source.roles || []) }}</span>
                    <small v-if="source.heading_count">识别标题 {{ source.heading_count }} 个</small>
                    <small v-if="source.editorial_sample_count">文体采样 {{ source.editorial_sample_count }} 处</small>
                  </div>
                </div>
              </div>
              <p v-else class="schema-note">
                该历史画像没有保存逐文件用途与模型输入覆盖记录，因此不推测哪些材料参与了哪类学习。
              </p>
            </div>
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
            <div class="profile-block document-shape">
              <h3>文档形态</h3>
              <dl>
                <dt>推荐形态</dt><dd>{{ documentShapeLabel }}</dd>
                <dt>标题呈现</dt><dd>{{ headingPolicyLabel }}</dd>
                <dt>使用原则</dt><dd>最终结构由任务目标与材料决定；该画像只提供文体与呈现建议。</dd>
              </dl>
            </div>
            <div v-if="editorialRows.length" class="profile-block">
              <h3>
                表达与组织 <span>{{ learningEvidence.editorial?.source_count || 0 }} 份文体来源</span>
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
            <div class="examples-summary">
              <div class="examples-summary-title">
                <span>可检索成文范例</span>
                <b>{{ selected.exemplar_bank?.length || 0 }} 条</b>
              </div>
              <p>
                Writer 只会选取与当前章节目的匹配的范例，匹配不足时不强行注入；范例只用于组织和表达，不复制业务事实。
              </p>
            </div>
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
  grid-template-columns: minmax(0, 1fr) minmax(280px, 380px) auto;
  min-width: 0;
  align-items: center;
  gap: 16px;
  padding: 20px 24px;
}
.upload-controls {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(128px, 0.55fr);
  gap: 8px;
}
.asset-role-select {
  display: grid;
  gap: 4px;
  font-size: 12px;
  color: var(--color-muted);
}
.asset-role-select select {
  width: 100%;
}
.upload-bar h2,
.upload-bar p {
  margin: 0;
}
.template-layout {
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr);
  min-width: 0;
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
.examples-summary {
  display: grid;
  gap: 8px;
  min-width: 0;
  padding: 12px 0 14px;
  border-bottom: 1px solid var(--color-border);
}
.examples-summary-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}
.examples-summary-title span {
  min-width: 0;
  overflow-wrap: anywhere;
}
.examples-summary-title b {
  flex: 0 0 auto;
}
.examples-summary p {
  margin: 0;
  color: var(--color-muted);
  font-size: 12px;
  line-height: 1.65;
  overflow-wrap: anywhere;
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
  min-width: 0;
  border-top: 1px solid var(--color-border);
}
.example-list article {
  min-width: 0;
  padding: 12px 0;
  border-bottom: 1px solid var(--color-border);
}
.example-list article > div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}
.example-list article > div b {
  flex: 0 0 auto;
}
.example-list span,
.example-list small {
  color: var(--color-muted);
  font-size: 11px;
}
.example-list article > div span {
  min-width: 0;
  overflow: hidden;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.example-list p {
  display: -webkit-box;
  min-width: 0;
  overflow: hidden;
  margin: 7px 0 0;
  color: var(--color-text);
  font-size: 13px;
  line-height: 1.7;
  overflow-wrap: anywhere;
  word-break: break-word;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}
.example-list small {
  display: block;
  margin-top: 5px;
  color: var(--color-warning);
  overflow-wrap: anywhere;
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
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--muted-foreground);
  background: var(--muted);
  font-size: 12px;
  font-weight: 650;
}
.upload-bar {
  min-height: 112px;
  grid-template-columns: minmax(0, 1fr) minmax(220px, 360px) auto;
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
  border: 1px solid var(--document-paper-border);
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
.upload-bar {
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
}
.upload-copy,
.upload-controls,
.source-role-list {
  grid-column: 1 / -1;
}
.upload-controls {
  grid-template-columns: minmax(240px, 0.8fr) minmax(0, 1.2fr);
  align-items: center;
}
.role-explainer {
  margin: 0;
  color: var(--muted-foreground);
  font-size: 12px;
  line-height: 1.6;
}
.source-role-list {
  display: grid;
  gap: 6px;
  min-width: 0;
  max-height: 260px;
  overflow: auto;
  padding: 8px 0;
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
}
.source-role-row {
  display: grid;
  grid-template-columns: minmax(140px, 1fr) repeat(3, minmax(100px, auto));
  align-items: center;
  gap: 12px;
  min-width: 0;
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--surface-raised);
}
.source-role-row > strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 550;
}
.source-role-option {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  color: var(--foreground);
  font-size: 12px;
  white-space: nowrap;
}
.source-role-option input {
  accent-color: var(--primary);
}
.learning-evidence dl {
  grid-template-columns: 104px minmax(0, 1fr);
}
.learning-source-list {
  display: grid;
  gap: 6px;
  margin-top: 12px;
}
.prompt-section-audit {
  display: grid;
  gap: 4px;
  margin-top: 10px;
}
.prompt-section-audit > div {
  display: grid;
  grid-template-columns: minmax(100px, 1fr) auto auto;
  gap: 10px;
  align-items: baseline;
  padding: 5px 8px;
  border-radius: 5px;
  background: var(--surface-raised);
  font-size: 11px;
}
.prompt-section-audit span,
.prompt-section-audit small {
  color: var(--muted-foreground);
}
.prompt-section-audit strong {
  font-weight: 600;
}
.learning-source-list > div {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 12px;
  min-width: 0;
  align-items: baseline;
  padding: 7px 9px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.learning-source-list strong {
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 12px;
  font-weight: 550;
}
.learning-source-list span,
.learning-source-list small {
  color: var(--muted-foreground);
  font-size: 11px;
}
.evidence-warning {
  margin-left: 8px;
  color: var(--warning-foreground, #e5b567);
}
@media (max-width: 800px) {
  .source-role-row {
    grid-template-columns: minmax(120px, 1fr) repeat(2, minmax(0, auto));
  }
  .source-role-option:last-child {
    grid-column: 2 / -1;
  }
  .learning-source-list > div {
    grid-template-columns: minmax(0, 1fr) auto;
  }
  .learning-source-list small {
    grid-column: 2;
  }
}
@media (max-width: 650px) {
  .upload-bar {
    grid-template-columns: minmax(0, 1fr);
  }
  .upload-controls {
    grid-template-columns: minmax(0, 1fr);
  }
  .source-role-row {
    grid-template-columns: minmax(0, 1fr) minmax(0, auto);
  }
  .source-role-row > strong {
    grid-column: 1 / -1;
  }
  .source-role-option:last-child {
    grid-column: auto;
  }
}
</style>
