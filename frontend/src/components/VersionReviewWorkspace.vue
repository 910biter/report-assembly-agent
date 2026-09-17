<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { api, jsonInit } from "@/api/http";

type Decision = {
  id: number;
  change_key: string;
  decision: "keep_current" | "use_base";
  scope: any;
  status: string;
};
type Scope = {
  level: "section" | "paragraph" | "sentence";
  section: string;
  base_section?: string;
  current_section?: string;
  [key: string]: any;
};

const props = defineProps<{
  reportId: number;
  reportTitle: string;
  versions: any[];
  initialVersion?: number | null;
}>();
const emit = defineEmits<{ close: []; applied: []; snapshot: [] }>();
const activeVersion = ref<number | null>(props.initialVersion || null);
const diff = ref<any>(null);
const decisions = ref<Record<string, Decision>>({});
const loading = ref(false);
const error = ref("");
const filter = ref("all");
const activeSection = ref("all");
const fineParagraph = ref("");
const workspaceMode = ref<"review" | "preview" | "history">("review");
const applying = ref(false);
const historicalVersion = ref<any>(null);
const historicalLoading = ref(false);
const historicalError = ref("");
const comparisonMode = ref<"current" | "before" | "after">("current");

const orderedVersions = computed(() =>
  [...props.versions].sort(
    (a: any, b: any) => Number(b.version_no || 0) - Number(a.version_no || 0),
  ),
);
const isAutomaticSnapshot = (version: any) => version?.status === "snapshot";
const formalVersions = computed(() =>
  orderedVersions.value.filter((version: any) => !isAutomaticSnapshot(version)),
);
const chronologicalFormalVersions = computed(() =>
  [...formalVersions.value].sort(
    (a: any, b: any) => Number(a.version_no || 0) - Number(b.version_no || 0),
  ),
);
const snapshotGroups = computed(() => {
  const groups = new Map<string, any[]>();
  orderedVersions.value
    .filter(isAutomaticSnapshot)
    .forEach((version: any) => {
      const hash = String(version.metadata?.snapshot_hash || version.snapshot_hash || "");
      const key = hash || `version-${version.id}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(version);
    });
  return [...groups.values()].map((items) => ({
    representative: items[0],
    items,
    duplicateCount: items.length,
  }));
});
const snapshotCount = computed(() =>
  orderedVersions.value.filter(isAutomaticSnapshot).length,
);
const versionStatusLabel = (version: any) => {
  const labels: Record<string, string> = {
    draft: "草稿",
    accepted: "已采用",
    final: "已确认",
    snapshot: "自动快照",
  };
  return labels[String(version?.status || "")] || "版本";
};
const selectedFormalIndex = computed(() =>
  chronologicalFormalVersions.value.findIndex(
    (version: any) => Number(version.id) === activeVersion.value,
  ),
);
const previousVersion = computed(() => {
  const index = selectedFormalIndex.value;
  return index > 0 ? chronologicalFormalVersions.value[index - 1] : null;
});
const nextVersion = computed(() => {
  const index = selectedFormalIndex.value;
  return index >= 0 && index < chronologicalFormalVersions.value.length - 1
    ? chronologicalFormalVersions.value[index + 1]
    : null;
});
const comparisonLabel = computed(() => {
  if (comparisonMode.value === "before") {
    return previousVersion.value
      ? `v${previousVersion.value.version_label || previousVersion.value.version_no} → 当前版本`
      : "上一正式版本对比";
  }
  if (comparisonMode.value === "after") {
    return nextVersion.value
      ? `当前版本 → v${nextVersion.value.version_label || nextVersion.value.version_no}`
      : "下一正式版本对比";
  }
  return "当前工作稿差异";
});
const comparisonReadOnly = computed(() => comparisonMode.value !== "current");

const changedSections = computed(() =>
  (diff.value?.sections || []).filter(
    (item: any) => item.change_type !== "unchanged",
  ),
);
const visibleSections = computed(() => {
  const sections = activeSection.value === "all"
    ? changedSections.value
    : changedSections.value.filter(
        (item: any) =>
          (item.current_section || item.new_title || item.section) === activeSection.value,
      );
  return sections;
});
const versionMeta = computed(() =>
  props.versions.find((item) => Number(item.id) === activeVersion.value),
);
const changedSentences = computed(() =>
  changedSections.value.flatMap((section: any) =>
    (section.paragraphs || []).flatMap((paragraph: any) =>
      (paragraph.sentences || [])
        .filter((sentence: any) => sentence.change_type !== "unchanged")
        .map((sentence: any) => ({
          ...sentence,
          section_key: section.change_key,
          paragraph_key: paragraph.change_key,
        })),
    ),
  ),
);
const explicitCount = computed(() => Object.keys(decisions.value).length);
const restoredCount = computed(
  () =>
    Object.values(decisions.value).filter(
      (item) => item.decision === "use_base",
    ).length,
);
const reviewedCount = computed(
  () =>
    changedSentences.value.filter(
      (item: any) =>
        decisions.value[item.change_key] ||
        decisions.value[item.paragraph_key] ||
        decisions.value[item.section_key],
    ).length,
);
const lengths = computed(() => {
  const sections = diff.value?.sections || [];
  const oldLength = sections.reduce(
    (total: number, section: any) =>
      total +
      (section.paragraphs || []).reduce(
        (sum: number, paragraph: any) =>
          sum + String(paragraph.old_text || "").length,
        0,
      ),
    0,
  );
  const newLength = sections.reduce(
    (total: number, section: any) =>
      total +
      (section.paragraphs || []).reduce(
        (sum: number, paragraph: any) =>
          sum + String(paragraph.new_text || "").length,
        0,
      ),
    0,
  );
  return { oldLength, newLength, delta: newLength - oldLength };
});
const historicalSections = computed(() => {
  const rows = [...(historicalVersion.value?.sentence_snapshot || [])]
    .filter((row: any) => Number(row.selected ?? 1) !== 0)
    .sort(
      (a: any, b: any) =>
        Number(a.position || 0) - Number(b.position || 0) ||
        Number(a.id || 0) - Number(b.id || 0),
    );
  const sections = new Map<string, Map<number, string[]>>();
  rows.forEach((row: any) => {
    const title = String(row.section || "未命名章节");
    const paragraph = Number(row.paragraph || 1);
    if (!sections.has(title)) sections.set(title, new Map());
    const paragraphs = sections.get(title)!;
    if (!paragraphs.has(paragraph)) paragraphs.set(paragraph, []);
    paragraphs
      .get(paragraph)!
      .push(String(row.rendered_text || row.user_edit || row.content || ""));
  });
  return [...sections.entries()].map(([title, paragraphs]) => ({
    title,
    paragraphs: [...paragraphs.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([, texts]) => texts.join("")),
  }));
});

watch(
  () => props.versions,
  (versions) => {
    const available = versions.some((version: any) => Number(version.id) === activeVersion.value);
    if (!activeVersion.value || !available) {
      activeVersion.value = Number(
        formalVersions.value[0]?.id || versions[0]?.id || 0,
      ) || null;
    }
  },
  { immediate: true },
);

watch([activeVersion, comparisonMode], async ([versionId, mode]) => {
  diff.value = null;
  decisions.value = {};
  activeSection.value = "all";
  fineParagraph.value = "";
  historicalVersion.value = null;
  historicalError.value = "";
  error.value = "";
  if (!versionId) return;
  loading.value = true;
  try {
    if (mode === "before" && previousVersion.value) {
      diff.value = await api(
        `/api/report-versions/${previousVersion.value.id}/diff/${versionId}`,
      );
    } else if (mode === "after" && nextVersion.value) {
      diff.value = await api(
        `/api/report-versions/${versionId}/diff/${nextVersion.value.id}`,
      );
    } else {
      comparisonMode.value = "current";
      diff.value = await api(
        `/api/report-versions/${versionId}/diff-current?granularity=sentence`,
      );
      await loadDecisions();
    }
  } catch (reason: any) {
    error.value = reason.message || "版本差异加载失败";
  } finally {
    loading.value = false;
  }
  if (workspaceMode.value === "history") await loadHistoricalVersion();
});

function openComparison(mode: "before" | "after" | "current") {
  comparisonMode.value = mode;
  workspaceMode.value = "review";
}

watch(workspaceMode, async (mode) => {
  if (mode === "history") await loadHistoricalVersion();
});

async function loadHistoricalVersion() {
  if (
    !activeVersion.value ||
    Number(historicalVersion.value?.id) === activeVersion.value ||
    historicalLoading.value
  )
    return;
  historicalLoading.value = true;
  historicalError.value = "";
  try {
    historicalVersion.value = await api(
      `/api/report-versions/${activeVersion.value}`,
    );
  } catch (reason: any) {
    historicalError.value = reason.message || "历史原文加载失败";
  } finally {
    historicalLoading.value = false;
  }
}

async function loadDecisions() {
  if (!activeVersion.value || !diff.value?.candidate_hash) return;
  const result = await api<any>(
    `/api/report-versions/${activeVersion.value}/decisions?candidate_hash=${encodeURIComponent(diff.value.candidate_hash)}`,
  );
  decisions.value = Object.fromEntries(
    (result.decisions || [])
      .filter((item: Decision) => item.status === "pending")
      .map((item: Decision) => [item.change_key, item]),
  );
}

function sectionScope(section: any): Scope {
  return {
    level: "section",
    section: section.base_section || section.section,
    base_section: section.base_section || section.section,
    current_section: section.current_section || section.new_title || section.section,
  };
}
function paragraphScope(section: any, paragraph: any): Scope {
  return {
    level: "paragraph",
    section: section.base_section || section.section,
    base_section: section.base_section || section.section,
    current_section: section.current_section || section.new_title || section.section,
    paragraph: paragraph.old_paragraph ?? paragraph.paragraph,
    old_paragraph: paragraph.old_paragraph,
    new_paragraph: paragraph.new_paragraph,
  };
}
function sentenceScope(section: any, paragraph: any, sentence: any): Scope {
  return {
    level: "sentence",
    section: section.base_section || section.section,
    base_section: section.base_section || section.section,
    current_section: section.current_section || section.new_title || section.section,
    paragraph: paragraph.new_paragraph ?? paragraph.old_paragraph,
    old_paragraph: paragraph.old_paragraph,
    new_paragraph: paragraph.new_paragraph,
    old_sentence_id: sentence.old_sentence_id,
    current_sentence_id: sentence.current_sentence_id,
  };
}

async function decide(
  key: string,
  changeType: string,
  scope: Scope,
  decision: "keep_current" | "use_base",
) {
  if (!activeVersion.value || !diff.value) return;
  await api(
    `/api/report-versions/${activeVersion.value}/decisions`,
    jsonInit("POST", {
      candidate_hash: diff.value.candidate_hash,
      change_key: key,
      change_type: changeType,
      scope,
      decision,
    }),
  );
  await loadDecisions();
}

async function apply() {
  if (!activeVersion.value || !diff.value || applying.value) return;
  applying.value = true;
  try {
    await api(
      `/api/report-versions/${activeVersion.value}/decisions/apply`,
      jsonInit("POST", { candidate_hash: diff.value.candidate_hash }),
    );
    emit("applied");
  } catch (reason: any) {
    alert(reason.message || "应用失败，候选稿可能已经变化，请重新加载。");
  } finally {
    applying.value = false;
  }
}

function decision(key: string) {
  return decisions.value[key]?.decision || "";
}
function decisionLabel(key: string) {
  return decision(key) === "use_base"
    ? "采用历史版本"
    : decision(key) === "keep_current"
      ? "保留当前版本"
      : "默认保留当前版本";
}
function paragraphKey(section: any, paragraph: any) {
  return (section.change_key || section.section) + ":" + paragraph.change_key;
}
function toggleFine(section: any, paragraph: any) {
  const key = paragraphKey(section, paragraph);
  fineParagraph.value = fineParagraph.value === key ? "" : key;
}
function changedLabel(type: string, unit = "") {
  const labels: Record<string, string> = {
    added: "新增",
    removed: "删除",
    renamed: "改名",
    rewritten: "重写",
    reordered: "重排",
    provenance_changed: "溯源变化",
    modified: "修改",
  };
  return (labels[type] || "变化") + unit;
}

function matchConfidenceLabel(section: any) {
  if (section.match_confidence === "low") return "\u6309\u4f4d\u7f6e\u914d\u5bf9\uff0c\u8bf7\u6838\u5bf9";
  if (section.match_confidence === "medium") return "\u6309\u5185\u5bb9\u914d\u5bf9";
  return "";
}

function reviewParagraphs(section: any) {
  const paragraphs = section.paragraphs || [];
  const changed = new Set<number>();
  paragraphs.forEach((paragraph: any, index: number) => {
    const structural = filter.value === "renamed" || filter.value === "reordered";
    const sectionRewrite =
      filter.value === "rewritten" && section.change_type === "rewritten";
    if (
      paragraph.change_type !== "unchanged" &&
      (filter.value === "all" ||
        paragraph.change_type === filter.value ||
        sectionRewrite)
    ) {
      changed.add(index);
    } else if (structural && section.change_type === filter.value) {
      changed.add(index);
    } else if (
      filter.value === "all" &&
      (section.change_type === "renamed" || section.change_type === "reordered")
    ) {
      changed.add(index);
    }
  });
  const visible = new Set<number>();
  changed.forEach((index) => {
    visible.add(index - 1);
    visible.add(index);
    visible.add(index + 1);
  });
  return paragraphs
    .map((paragraph: any, index: number) => ({
      ...paragraph,
      _context: !changed.has(index),
      _index: index,
    }))
    .filter((_: any, index: number) => visible.has(index));
}

function spans(sentence: any, side: "old" | "new") {
  return (
    sentence.inline_diff?.[side] || [
      {
        type: "unchanged",
        text: side === "old" ? sentence.old_text : sentence.new_text,
      },
    ]
  );
}
function sourceImpact(sentence: any) {
  const source = sentence.new_sentence || sentence.old_sentence || {};
  const refs = source.source_refs || {};
  const facts = (refs.fact_ids || source.fact_ids || []).length;
  const inferences = (refs.inference_ids || source.inference_ids || []).length;
  return (
    [facts ? `${facts} 条事实` : "", inferences ? `${inferences} 条推论` : ""]
      .filter(Boolean)
      .join(" · ") || "结构或过渡表达"
  );
}
function previewSentence(sentence: any) {
  if (decision(sentence.change_key) === "use_base")
    return sentence.change_type === "added" ? "" : sentence.old_text || "";
  return sentence.change_type === "removed" ? "" : sentence.new_text || "";
}
function previewParagraph(section: any, paragraph: any) {
  if (
    decision(section.change_key) === "use_base" ||
    decision(paragraph.change_key) === "use_base"
  )
    return paragraph.old_text || "";
  return (paragraph.sentences || []).map(previewSentence).join("");
}
function previewSection(section: any) {
  return (section.paragraphs || [])
    .map((paragraph: any) => previewParagraph(section, paragraph))
    .filter(Boolean);
}
</script>

<template>
  <section class="review-workspace">
    <header class="review-header">
      <div>
        <span class="eyebrow">VERSION REVIEW</span>
        <h2>版本审阅</h2>
        <p>先理解段落变化，需要时再逐句调整。</p>
      </div>
      <div v-if="diff" class="header-stats">
        <span
          >章节 <b>{{ diff.summary?.sections_changed || 0 }}</b></span
        ><span
          >段落 <b>{{ diff.summary?.paragraphs_changed || 0 }}</b></span
        ><span
          >篇幅
          <b :class="lengths.delta >= 0 ? 'positive' : 'negative'"
            >{{ lengths.delta >= 0 ? "+" : "" }}{{ lengths.delta }}</b
          ></span
        >
      </div>
      <div class="header-actions">
        <button
          :class="{ active: workspaceMode === 'review' }"
          @click="workspaceMode = 'review'"
        >
          审阅差异</button
        ><button
          :class="{ active: workspaceMode === 'history' }"
          @click="workspaceMode = 'history'"
        >
          历史原文</button
        ><button
          :class="{ active: workspaceMode === 'preview' }"
          @click="workspaceMode = 'preview'"
        >
          合并预览</button
        ><button @click="emit('close')">关闭</button>
      </div>
    </header>

    <aside class="review-navigation">
      <section>
        <div class="navigation-heading">
          <div>
            <h3>版本查看与对比</h3>
            <small>{{ formalVersions.length }} 个正式版本</small>
          </div>
        </div>
        <label class="version-select-label" for="version-anchor">当前查看版本</label>
        <select id="version-anchor" v-model.number="activeVersion" class="version-select">
          <option
            v-for="version in formalVersions"
            :key="version.id"
            :value="Number(version.id)"
          >
            v{{ version.version_label || version.version_no }} · {{ versionStatusLabel(version) }}
          </option>
        </select>
        <div v-if="versionMeta" class="version-focus">
          <div>
            <b>v{{ versionMeta.version_label || versionMeta.version_no }}</b>
            <span>{{ versionStatusLabel(versionMeta) }}</span>
          </div>
          <small>{{ versionMeta.change_summary || "版本快照" }}</small>
          <time>{{ versionMeta.created_at }}</time>
        </div>
        <div class="version-neighbor-actions">
          <button
            :disabled="!previousVersion"
            @click="openComparison('before')"
          >
            ← 与上一版
          </button>
          <button @click="workspaceMode = 'history'">查看正文</button>
          <button
            :disabled="!nextVersion"
            @click="openComparison('after')"
          >
            与下一版 →
          </button>
        </div>
        <div class="comparison-mode-note">
          <span>{{ comparisonLabel }}<i v-if="comparisonReadOnly"> · 只读</i></span>
          <button
            v-if="comparisonMode !== 'current'"
            @click="openComparison('current')"
          >
            当前工作稿
          </button>
        </div>
        <button class="snapshot-button" @click="emit('snapshot')">
          生成当前快照
        </button>
        <details v-if="snapshotGroups.length" class="automatic-snapshots">
          <summary>自动快照 <span>{{ snapshotCount }}</span></summary>
          <div
            v-for="group in snapshotGroups"
            :key="group.representative.id"
            class="snapshot-group"
          >
            <button
              class="snapshot-group-button"
              :class="{ active: activeVersion === Number(group.representative.id) }"
              @click="activeVersion = Number(group.representative.id); workspaceMode = 'history'"
            >
              <span>
                <b>v{{ group.representative.version_label || group.representative.version_no }}</b>
                <small>{{ group.representative.change_summary || "自动生成的内部快照" }}</small>
              </span>
              <em v-if="group.duplicateCount > 1">{{ group.duplicateCount }} 份相同内容</em>
            </button>
            <div v-if="group.items.length > 1" class="snapshot-group-items">
              <span v-for="item in group.items" :key="item.id">
                v{{ item.version_label || item.version_no }} · {{ item.created_at }}
              </span>
            </div>
          </div>
        </details>
      </section>
      <section v-if="diff">
        <h3>变化章节</h3>
        <button
          class="chapter-option"
          :class="{ active: activeSection === 'all' }"
          @click="activeSection = 'all'"
        >
          全部变化 <span>{{ changedSections.length }}</span></button
        ><button
          v-for="section in changedSections"
          :key="section.change_key || section.section"
          class="chapter-option"
          :class="{
            active:
              activeSection ===
              (section.current_section || section.new_title || section.section),
          }"
          @click="
            activeSection =
              section.current_section || section.new_title || section.section
          "
        >
          {{ section.new_title || section.current_section || section.section
          }}<span>{{
            (section.paragraphs || []).filter(
              (paragraph: any) => paragraph.change_type !== "unchanged",
            ).length
          }}</span>
        </button>
      </section>
    </aside>

    <main class="review-main">
      <div
        v-if="workspaceMode === 'history' && historicalLoading"
        class="review-state"
      >
        <span class="spinner"></span>
        <p>正在加载历史原文…</p>
      </div>
      <div
        v-else-if="workspaceMode === 'history' && historicalError"
        class="review-state"
      >
        <b>无法加载历史原文</b>
        <p>{{ historicalError }}</p>
      </div>
      <div
        v-else-if="workspaceMode === 'history' && historicalVersion"
        class="historical-preview"
      >
        <header>
          <span
            >历史版本 v{{
              versionMeta?.version_label || versionMeta?.version_no
            }}</span
          >
          <h1>{{ historicalVersion.title || reportTitle }}</h1>
          <p>
            {{ versionMeta?.created_at }} ·
            {{ versionMeta?.change_summary || "版本快照" }}
          </p>
        </header>
        <section v-for="section in historicalSections" :key="section.title">
          <h2>{{ section.title }}</h2>
          <p v-for="(paragraph, index) in section.paragraphs" :key="index">
            {{ paragraph }}
          </p>
        </section>
        <div v-if="!historicalSections.length" class="review-state">
          <b>该版本没有正文</b>
          <p>版本元数据仍然保留。</p>
        </div>
      </div>
      <div v-else-if="loading" class="review-state">
        <span class="spinner"></span>
        <p>正在建立版本差异…</p>
      </div>
      <div v-else-if="error" class="review-state">
        <b>无法加载差异</b>
        <p>{{ error }}</p>
      </div>
      <template v-else-if="diff">
        <div v-if="workspaceMode === 'review'" class="review-document">
          <div class="filterbar">
            <div class="tabs">
              <button
                v-for="item in [
                  ['all', '全部'],
                  ['modified', '修改'],
                  ['rewritten', '重写'],
                  ['renamed', '改名'],
                  ['provenance_changed', '溯源'],
                  ['added', '新增'],
                  ['removed', '删除'],
                ]"
                :key="item[0]"
                :class="{ active: filter === item[0] }"
                @click="filter = item[0]"
              >
                {{ item[1] }}
              </button>
            </div>
            <span>未处理内容默认保留当前候选稿</span>
          </div>
          <section
            v-for="section in visibleSections"
            :key="section.change_key || section.section"
            class="review-section"
          >
            <header>
              <div>
                <span :class="['change-kind', section.change_type]">{{
                  changedLabel(section.change_type, "章节")
                }}</span>
                <h2>{{ section.new_title || section.current_section || section.section }}</h2>
                <small v-if="section.title_changed" class="title-transition">
                  {{ section.old_title }} -> {{ section.new_title }}
                </small>
                <small v-if="matchConfidenceLabel(section)" class="pair-confidence">
                  {{ matchConfidenceLabel(section) }}
                </small>
              </div>
              <div class="scope-actions">
                <small>{{ decisionLabel(section.change_key) }}</small
                ><button
                  :class="{
                    chosen: decision(section.change_key) === 'keep_current',
                  }"
                  @click="
                    decide(
                      section.change_key,
                      section.change_type,
                      sectionScope(section),
                      'keep_current',
                    )
                  "
                >
                  保留本章新版</button
                ><button
                  :class="{
                    chosen: decision(section.change_key) === 'use_base',
                  }"
                  @click="
                    decide(
                      section.change_key,
                      section.change_type,
                      sectionScope(section),
                      'use_base',
                    )
                  "
                >
                  采用本章旧版
                </button>
              </div>
            </header>
            <article
              v-for="paragraph in reviewParagraphs(section)"
              :key="paragraph.change_key"
              :class="[
                'review-paragraph',
                paragraph.change_type,
                { context: paragraph._context },
              ]"
            >
              <template v-if="paragraph._context"
                ><span class="context-label">上下文</span>
                <p>{{ paragraph.new_text || paragraph.old_text }}</p></template
              >
              <template v-else>
                <div class="paragraph-head">
                  <div>
                    <span :class="['change-kind', paragraph.change_type]">{{
                      changedLabel(paragraph.change_type)
                    }}</span
                    ><b
                      >第
                      {{
                        paragraph.new_paragraph || paragraph.old_paragraph
                      }}
                      段</b
                    ><small>{{ decisionLabel(paragraph.change_key) }}</small>
                  </div>
                  <div class="scope-actions compact">
                    <button
                      :class="{
                        chosen:
                          decision(paragraph.change_key) === 'keep_current',
                      }"
                      @click="
                        decide(
                          paragraph.change_key,
                          paragraph.change_type,
                          paragraphScope(section, paragraph),
                          'keep_current',
                        )
                      "
                    >
                      采用当前段落</button
                    ><button
                      :class="{
                        chosen: decision(paragraph.change_key) === 'use_base',
                      }"
                      @click="
                        decide(
                          paragraph.change_key,
                          paragraph.change_type,
                          paragraphScope(section, paragraph),
                          'use_base',
                        )
                      "
                    >
                      恢复历史段落</button
                    ><button @click="toggleFine(section, paragraph)">
                      {{
                        fineParagraph === paragraphKey(section, paragraph)
                          ? "收起精调"
                          : "逐句调整"
                      }}
                    </button>
                  </div>
                </div>
                <div class="paragraph-compare">
                  <div>
                    <label>历史版本</label>
                    <p>{{ paragraph.old_text || "（本段不存在）" }}</p>
                  </div>
                  <div>
                    <label>当前候选稿</label>
                    <p>{{ paragraph.new_text || "（本段已删除）" }}</p>
                  </div>
                </div>
                <div
                  v-if="fineParagraph === paragraphKey(section, paragraph)"
                  class="sentence-review"
                >
                  <p class="fine-note">
                    逐句决定会替代本段整体决定；每句话仍保留原有事实和推论绑定。
                  </p>
                  <article
                    v-for="sentence in (paragraph.sentences || []).filter(
                      (item: any) => item.change_type !== 'unchanged',
                    )"
                    :key="sentence.change_key"
                    :class="['sentence-change', sentence.change_type]"
                  >
                    <header>
                      <span>{{ changedLabel(sentence.change_type, "句") }}</span
                      ><small>{{ sourceImpact(sentence) }}</small
                      ><b>{{ decisionLabel(sentence.change_key) }}</b>
                    </header>
                    <div class="inline-compare">
                      <p>
                        <label>历史</label
                        ><span
                          v-for="(part, index) in spans(sentence, 'old')"
                          :key="index"
                          :class="`inline-${part.type}`"
                          >{{ part.text }}</span
                        ><i v-if="!sentence.old_text">（无）</i>
                      </p>
                      <p>
                        <label>当前</label
                        ><span
                          v-for="(part, index) in spans(sentence, 'new')"
                          :key="index"
                          :class="`inline-${part.type}`"
                          >{{ part.text }}</span
                        ><i v-if="!sentence.new_text">（无）</i>
                      </p>
                    </div>
                    <div class="sentence-actions">
                      <button
                        :class="{
                          chosen:
                            decision(sentence.change_key) === 'keep_current',
                        }"
                        @click="
                          decide(
                            sentence.change_key,
                            sentence.change_type,
                            sentenceScope(section, paragraph, sentence),
                            'keep_current',
                          )
                        "
                      >
                        {{
                          sentence.change_type === "removed"
                            ? "保持删除"
                            : "保留当前"
                        }}</button
                      ><button
                        :class="{
                          chosen: decision(sentence.change_key) === 'use_base',
                        }"
                        @click="
                          decide(
                            sentence.change_key,
                            sentence.change_type,
                            sentenceScope(section, paragraph, sentence),
                            'use_base',
                          )
                        "
                      >
                        {{
                          sentence.change_type === "added"
                            ? "删除新增"
                            : "恢复原句"
                        }}
                      </button>
                    </div>
                  </article>
                </div>
              </template>
            </article>
          </section>
          <div v-if="!visibleSections.length" class="review-state">
            <b>没有符合条件的差异</b>
            <p>调整筛选条件或选择其他版本。</p>
          </div>
        </div>
        <div v-else class="merged-preview">
          <header>
            <span>应用后预览</span>
            <h1>{{ reportTitle }}</h1>
            <p>根据当前暂存决定实时组装，不会立即修改报告。</p>
          </header>
          <section
            v-for="section in diff.sections || []"
            :key="section.change_key || section.section"
          >
            <h2>{{ section.new_title || section.current_section || section.section }}</h2>
            <p
              v-for="(paragraph, index) in previewSection(section)"
              :key="index"
            >
              {{ paragraph }}
            </p>
          </section>
        </div>
      </template>
      <div v-else class="review-state">
        <b>选择一个历史版本</b>
        <p>系统将以段落为主展示它与当前候选稿的差异。</p>
      </div>
    </main>

    <aside class="review-summary">
      <h3>审阅概览</h3>
      <div class="baseline-card">
        <span>历史基线</span
        ><b
          >v{{
            versionMeta?.version_label || versionMeta?.version_no || "—"
          }}</b
        ><small>{{ versionMeta?.change_summary || "版本快照" }}</small>
      </div>
      <dl v-if="diff">
        <div>
          <dt>历史篇幅</dt>
          <dd>{{ lengths.oldLength }}</dd>
        </div>
        <div>
          <dt>候选篇幅</dt>
          <dd>{{ lengths.newLength }}</dd>
        </div>
        <div>
          <dt>明确决定</dt>
          <dd>{{ explicitCount }}</dd>
        </div>
        <div>
          <dt>恢复旧版</dt>
          <dd>{{ restoredCount }}</dd>
        </div>
      </dl>
      <div v-if="diff" class="review-progress">
        <div>
          <span>逐句决定</span
          ><b>{{ reviewedCount }} / {{ changedSentences.length }}</b>
        </div>
        <progress
          :value="reviewedCount"
          :max="Math.max(changedSentences.length, 1)"
        ></progress>
        <p>章节或段落决定会覆盖内部句子，不要求逐句点击。</p>
      </div>
      <div class="principle">
        <b>应用规则</b>
        <p>
          默认保留当前稿；只有选择“采用历史版本”的范围会被恢复。应用前自动保存候选稿快照。
        </p>
      </div>
      <button
        class="apply-button"
        :disabled="!diff || applying || diff.can_apply === false"
        @click="apply"
      >
        {{
          diff?.can_apply === false
            ? "历史版本对比（只读）"
            : applying
              ? "正在生成版本…"
              : "完成审阅并生成版本"
        }}
      </button>
    </aside>
  </section>
</template>

<style scoped>
.review-workspace {
  position: fixed;
  inset: var(--header-height) 0 0 var(--nav-width);
  z-index: 80;
  display: grid;
  grid-template: 88px minmax(0, 1fr) / 250px minmax(560px, 1fr) 292px;
  background: var(--background);
  color: var(--foreground);
  min-width: 0;
  overflow: hidden;
}
.review-workspace > * {
  min-width: 0;
}
button {
  font: inherit;
}
.review-header {
  grid-column: 1/-1;
  display: grid;
  grid-template-columns: 250px 1fr auto;
  align-items: center;
  gap: 20px;
  min-height: 88px;
  padding: 10px 22px;
  background: color-mix(in srgb, var(--card) 94%, var(--primary-soft));
  border-bottom: 1px solid var(--color-border);
}
.review-header > div:first-child {
  align-self: center;
  min-width: 0;
}
.review-header h2 {
  margin: 1px 0;
  font-family: var(--font-ui);
  font-size: 23px;
  font-weight: 720;
  letter-spacing: -0.03em;
  line-height: 1.25;
}
.review-header p {
  margin: 2px 0 0;
  color: var(--color-muted);
  font-size: 12px;
  line-height: 1.35;
  white-space: nowrap;
}
.eyebrow {
  display: block;
  color: var(--color-primary);
  font-size: 9px;
  font-weight: 700;
  line-height: 1.2;
  letter-spacing: 0.14em;
}
.header-stats {
  display: flex;
  gap: 0;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--card);
  color: var(--color-muted);
  font-size: 12px;
}
.header-stats b {
  margin-left: 5px;
  color: var(--color-text);
  font-size: 16px;
}
.header-stats span {
  min-width: 80px;
  padding: 6px 12px;
  border-right: 1px solid var(--border);
}
.header-stats span:last-child {
  border-right: 0;
}
.header-stats .positive {
  color: var(--color-success);
}
.header-stats .negative {
  color: var(--color-danger);
}
.header-actions {
  display: flex;
  gap: 7px;
  padding: 3px;
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--card);
}
.header-actions button {
  padding: 7px 11px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: transparent;
}
.header-actions button.active {
  color: var(--color-primary);
  border-color: transparent;
  background: var(--color-primary-soft);
}
.review-navigation,
.review-summary {
  min-height: 0;
  padding: 18px;
  background: color-mix(in srgb, var(--card) 78%, var(--background));
  overflow: auto;
}
.review-navigation {
  border-right: 1px solid var(--color-border);
}
.review-summary {
  border-left: 1px solid var(--color-border);
}
.review-navigation section + section {
  margin-top: 24px;
}
.review-navigation h3,
.review-summary h3 {
  margin: 0 0 10px;
  font-size: 12px;
}
.navigation-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}
.navigation-heading h3 {
  margin: 0;
}
.navigation-heading small,
.version-select-label {
  color: var(--color-muted);
  font-size: 10px;
}
.version-select-label {
  display: block;
  margin-bottom: 5px;
}
.version-select {
  width: 100%;
  min-height: 34px;
  padding: 6px 9px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--surface-raised);
  color: var(--color-text);
  font: inherit;
  font-size: 12px;
}
.version-select:focus {
  outline: 2px solid color-mix(in srgb, var(--primary) 38%, transparent);
  outline-offset: 1px;
}
.version-focus {
  display: grid;
  gap: 4px;
  margin-top: 9px;
  padding: 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--surface-raised);
}
.version-focus > div {
  display: flex;
  align-items: center;
  gap: 7px;
}
.version-focus b {
  font-size: 14px;
}
.version-focus span {
  color: var(--color-primary);
  font-size: 10px;
}
.version-focus small,
.version-focus time {
  color: var(--color-muted);
  font-size: 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.version-neighbor-actions {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 4px;
  margin-top: 8px;
}
.version-neighbor-actions button,
.comparison-mode-note button {
  min-width: 0;
  padding: 6px 4px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-muted);
  font-size: 10px;
  white-space: nowrap;
}
.version-neighbor-actions button:hover,
.comparison-mode-note button:hover {
  color: var(--color-primary);
  border-color: color-mix(in srgb, var(--primary) 40%, var(--border));
  background: var(--color-primary-soft);
}
.version-neighbor-actions button:disabled {
  cursor: not-allowed;
  opacity: 0.42;
}
.comparison-mode-note {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 5px;
  min-height: 27px;
  margin: 8px 0;
  color: var(--color-muted);
  font-size: 10px;
}
.comparison-mode-note button {
  flex: 0 0 auto;
  padding: 4px 6px;
  color: var(--color-primary);
}
.automatic-snapshots {
  margin-top: 10px;
  border-top: 1px solid var(--color-border);
}
.automatic-snapshots summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 9px 0 7px;
  color: var(--color-muted);
  cursor: pointer;
  font-size: 11px;
}
.automatic-snapshots summary span {
  min-width: 18px;
  padding: 2px 5px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  color: var(--color-faint);
  font-size: 9px;
  text-align: center;
}
.snapshot-group + .snapshot-group {
  margin-top: 4px;
}
.snapshot-group-button {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-muted);
  text-align: left;
}
.snapshot-group-button:hover,
.snapshot-group-button.active {
  border-color: color-mix(in srgb, var(--primary) 38%, var(--border));
  background: var(--color-primary-soft);
}
.snapshot-group-button span,
.snapshot-group-button small {
  display: block;
}
.snapshot-group-button b {
  color: var(--color-text);
  font-size: 11px;
}
.snapshot-group-button small {
  max-width: 145px;
  margin-top: 3px;
  color: var(--color-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.snapshot-group-button em {
  flex: 0 0 auto;
  color: var(--color-faint);
  font-size: 9px;
  font-style: normal;
}
.snapshot-group-items {
  display: grid;
  gap: 2px;
  padding: 5px 8px 2px;
  color: var(--color-faint);
  font-size: 9px;
}
.version-option,
.chapter-option {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px;
  border: 0;
  border-bottom: 1px solid var(--color-border);
  background: transparent;
  text-align: left;
}
.version-option:hover,
.chapter-option:hover {
  background: var(--surface-hover);
}
.version-option.active,
.chapter-option.active {
  color: var(--color-primary);
  background: var(--color-primary-soft);
}
.version-option span,
.version-option small {
  display: block;
}
.version-option small {
  max-width: 118px;
  margin-top: 3px;
  color: var(--color-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.version-option time {
  color: var(--color-faint);
  font-size: 9px;
}
.chapter-option {
  font-size: 12px;
}
.chapter-option span {
  color: var(--color-faint);
}
.snapshot-button {
  width: 100%;
  margin-bottom: 8px;
  padding: 7px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--surface-raised);
  color: var(--color-primary);
  font-size: 11px;
}
.snapshot-button:hover {
  border-color: color-mix(in srgb, var(--primary) 40%, var(--border));
  background: var(--color-primary-soft);
}
.review-main {
  min-width: 0;
  overflow: auto;
  padding: 22px;
}
.review-document,
.merged-preview,
.historical-preview {
  width: min(100%, 960px);
  margin: 0 auto;
}
.filterbar {
  position: sticky;
  top: -22px;
  z-index: 4;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 0;
  background: color-mix(in srgb, var(--background) 91%, transparent);
  backdrop-filter: blur(10px);
}
.filterbar .tabs {
  display: flex;
}
.filterbar button {
  padding: 6px 11px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
}
.filterbar button.active {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
}
.filterbar > span {
  color: var(--color-muted);
  font-size: 11px;
}
.review-section {
  margin-bottom: 24px;
  padding: 24px 28px;
  background: var(--card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-panel);
  box-shadow: var(--shadow-panel);
}
.review-section > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  padding-bottom: 17px;
  border-bottom: 1px solid var(--color-border);
}
.review-section h2 {
  margin: 4px 0 0;
  font-family: var(--font-ui);
  font-size: 20px;
}
.change-kind {
  font-size: 10px;
  font-weight: 700;
}
.change-kind.modified {
  color: var(--warning);
}
.change-kind.added {
  color: var(--success);
}
.change-kind.removed {
  color: var(--destructive);
}
.change-kind.renamed,
.change-kind.reordered,
.change-kind.provenance_changed {
  color: var(--color-primary);
}
.title-transition {
  display: block;
  margin-top: 4px;
  color: var(--color-muted);
  font-size: 11px;
  font-weight: 400;
}
.pair-confidence {
  display: inline-block;
  margin-top: 5px;
  color: var(--warning);
  font-size: 11px;
  font-weight: 500;
}
.scope-actions,
.sentence-actions {
  display: flex;
  align-items: center;
  gap: 5px;
}
.scope-actions small {
  margin-right: 4px;
  color: var(--color-faint);
}
.scope-actions button,
.sentence-actions button {
  padding: 5px 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--surface-raised);
  color: var(--color-muted);
  font-size: 10px;
}
.scope-actions button:hover,
.scope-actions button.chosen,
.sentence-actions button:hover,
.sentence-actions button.chosen {
  color: var(--color-primary);
  border-color: color-mix(in srgb, var(--primary) 40%, var(--border));
  background: var(--color-primary-soft);
}
.scope-actions.compact {
  flex-wrap: wrap;
  justify-content: flex-end;
}
.review-paragraph {
  margin-top: 18px;
  overflow: hidden;
  border-left: 3px solid var(--warning);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}
.review-paragraph.added {
  border-left-color: var(--success);
}
.review-paragraph.removed {
  border-left-color: var(--destructive);
}
.review-paragraph.context {
  display: flex;
  gap: 12px;
  margin: 12px 0 12px 3px;
  padding: 8px 12px;
  border-left: 1px solid var(--color-border);
  background: var(--surface-hover);
  color: var(--color-muted);
}
.review-paragraph.context p {
  margin: 0;
  font-family: var(--font-doc);
  font-size: 13px;
  line-height: 1.75;
}
.context-label {
  flex: 0 0 auto;
  color: var(--color-faint);
  font-size: 9px;
}
.paragraph-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 12px;
  background: color-mix(in srgb, var(--muted) 58%, var(--card));
  border-bottom: 1px solid var(--color-border);
}
.paragraph-head > div:first-child {
  display: flex;
  align-items: center;
  gap: 8px;
}
.paragraph-head small {
  color: var(--color-faint);
}
.paragraph-compare {
  display: grid;
  grid-template-columns: 1fr 1fr;
}
.paragraph-compare > div {
  padding: 15px 18px;
}
.paragraph-compare > div:first-child {
  border-right: 1px solid var(--color-border);
  background: var(--surface-raised);
}
.paragraph-compare label,
.inline-compare label {
  display: block;
  margin-bottom: 7px;
  color: var(--color-faint);
  font: 10px var(--font-ui);
}
.paragraph-compare p {
  margin: 0;
  font-family: var(--font-doc);
  font-size: 14px;
  line-height: 1.9;
  text-align: justify;
  text-indent: 2em;
}
.sentence-review {
  padding: 14px;
  background: var(--muted);
  border-top: 1px solid var(--color-border);
}
.fine-note {
  margin: 0 0 10px;
  color: var(--color-muted);
  font-size: 11px;
}
.sentence-change {
  margin: 9px 0;
  overflow: hidden;
  border-radius: var(--radius-sm);
  background: var(--surface-raised);
  border: 1px solid var(--color-border);
}
.sentence-change > header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 10px;
  border-bottom: 1px solid var(--color-border);
}
.sentence-change > header span {
  font-size: 10px;
  font-weight: 700;
}
.sentence-change > header small {
  color: var(--color-faint);
}
.sentence-change > header b {
  margin-left: auto;
  color: var(--color-muted);
  font-size: 10px;
}
.inline-compare {
  display: grid;
  grid-template-columns: 1fr 1fr;
}
.inline-compare p {
  margin: 0;
  padding: 11px 13px;
  font-family: var(--font-doc);
  font-size: 13px;
  line-height: 1.8;
}
.inline-compare p:first-child {
  border-right: 1px solid var(--color-border);
}
.inline-removed {
  color: var(--destructive);
  background: var(--destructive-soft);
  text-decoration: line-through;
}
.inline-added {
  color: var(--success);
  background: var(--success-soft);
}
.inline-compare i {
  color: var(--color-faint);
  font-style: normal;
}
.sentence-actions {
  justify-content: flex-end;
  padding: 7px 10px;
  border-top: 1px solid var(--color-border);
}
.merged-preview,
.historical-preview {
  min-height: 100%;
  padding: 64px 76px 90px;
  background: var(--document-paper);
  border: 1px solid var(--document-paper-border);
  box-shadow: var(--shadow-paper);
  font-family: var(--font-doc);
}
.merged-preview > header,
.historical-preview > header {
  text-align: center;
}
.merged-preview > header > span,
.historical-preview > header > span {
  color: var(--color-primary);
  font: 10px var(--font-ui);
  letter-spacing: 0.12em;
}
.merged-preview h1,
.historical-preview h1 {
  margin: 8px 0 5px;
  font-size: 25px;
}
.merged-preview > header p,
.historical-preview > header p {
  color: var(--color-muted);
  font: 11px var(--font-ui);
}
.merged-preview h2,
.historical-preview h2 {
  margin: 32px 0 14px;
  font-size: 19px;
}
.merged-preview section p,
.historical-preview section p {
  margin: 0 0 14px;
  font-size: 16px;
  line-height: 1.95;
  text-align: justify;
  text-indent: 2em;
}
.baseline-card {
  padding: 13px;
  border-radius: var(--radius-control);
  background: color-mix(in srgb, var(--card) 88%, var(--primary-soft));
  border: 1px solid var(--color-border);
}
.baseline-card span,
.baseline-card b,
.baseline-card small {
  display: block;
}
.baseline-card span,
.baseline-card small {
  color: var(--color-muted);
  font-size: 10px;
}
.baseline-card b {
  margin: 4px 0;
  font-size: 20px;
}
.review-summary dl {
  display: grid;
  grid-template-columns: 1fr 1fr;
  margin: 18px 0;
  border-top: 1px solid var(--color-border);
  border-left: 1px solid var(--color-border);
}
.review-summary dl div {
  padding: 10px;
  background: var(--card);
  border-right: 1px solid var(--color-border);
  border-bottom: 1px solid var(--color-border);
}
.review-summary dt {
  color: var(--color-faint);
  font-size: 9px;
}
.review-summary dd {
  margin: 3px 0 0;
  font-size: 17px;
}
.review-progress > div {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
}
.review-progress progress {
  width: 100%;
  height: 5px;
  margin: 8px 0;
  accent-color: var(--color-primary);
}
.review-progress p,
.principle p {
  color: var(--color-muted);
  font-size: 10px;
  line-height: 1.6;
}
.principle {
  margin: 18px 0;
  padding: 12px;
  border-left: 2px solid var(--color-primary);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  background: var(--primary-soft);
}
.principle b {
  font-size: 11px;
}
.apply-button {
  width: 100%;
  padding: 9px;
  border: 1px solid var(--color-primary);
  border-radius: var(--radius-sm);
  background: var(--primary-gradient);
  color: var(--primary-foreground);
}
.apply-button:disabled {
  opacity: 0.55;
}
.review-state {
  display: grid;
  place-items: center;
  min-height: 240px;
  color: var(--color-muted);
  text-align: center;
}
.review-state b {
  color: var(--color-text);
}
.review-state p {
  margin: 5px;
}
.spinner {
  width: 22px;
  height: 22px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
@media (max-width: 1350px) {
  .review-workspace {
    grid-template-columns: 220px minmax(500px, 1fr) 250px;
  }
  .review-header {
    grid-template-columns: 220px 1fr auto;
  }
  .header-stats {
    gap: 12px;
  }
}
@media (max-width: 1100px) {
  .review-workspace {
    inset: var(--header-height) 0 0;
    grid-template-columns: 210px 1fr;
  }
  .review-summary {
    display: none;
  }
  .review-header {
    grid-template-columns: 210px 1fr;
  }
  .header-stats {
    display: none;
  }
}
@media (max-width: 800px) {
  .review-workspace {
    grid-template: 64px 150px minmax(0, 1fr) / 1fr;
  }
  .review-header {
    grid-column: 1;
    grid-template-columns: 1fr auto;
  }
  .review-header p,
  .header-actions button:not(:last-child) {
    display: none;
  }
  .review-navigation {
    display: flex;
    gap: 12px;
    border-right: 0;
    border-bottom: 1px solid var(--color-border);
  }
  .review-navigation section {
    min-width: 220px;
  }
  .review-navigation section + section {
    margin-top: 0;
  }
  .review-main {
    padding: 12px;
  }
  .paragraph-compare,
  .inline-compare {
    grid-template-columns: 1fr;
  }
  .paragraph-compare > div:first-child,
  .inline-compare p:first-child {
    border-right: 0;
    border-bottom: 1px solid var(--color-border);
  }
  .merged-preview {
    padding: 40px 28px;
  }
}

</style>
