<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { api, jsonInit } from "@/api/http";

type Decision = { id: number; change_key: string; decision: "keep_current" | "use_base"; scope: any; status: string };
type Scope = { level: "section" | "paragraph" | "sentence"; section: string; [key: string]: any };

const props = defineProps<{ reportId: number; reportTitle: string; versions: any[]; initialVersion?: number | null }>();
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

const changedSections = computed(() => (diff.value?.sections || []).filter((item: any) => item.change_type !== "unchanged"));
const visibleSections = computed(() => activeSection.value === "all" ? changedSections.value : changedSections.value.filter((item: any) => item.section === activeSection.value));
const versionMeta = computed(() => props.versions.find(item => Number(item.id) === activeVersion.value));
const changedSentences = computed(() => changedSections.value.flatMap((section: any) =>
  (section.paragraphs || []).flatMap((paragraph: any) =>
    (paragraph.sentences || []).filter((sentence: any) => sentence.change_type !== "unchanged")
      .map((sentence: any) => ({ ...sentence, section_key: section.change_key, paragraph_key: paragraph.change_key }))
  )
));
const explicitCount = computed(() => Object.keys(decisions.value).length);
const restoredCount = computed(() => Object.values(decisions.value).filter(item => item.decision === "use_base").length);
const reviewedCount = computed(() => changedSentences.value.filter((item: any) =>
  decisions.value[item.change_key] || decisions.value[item.paragraph_key] || decisions.value[item.section_key]
).length);
const lengths = computed(() => {
  const sections = diff.value?.sections || [];
  const oldLength = sections.reduce((total: number, section: any) => total + (section.paragraphs || []).reduce((sum: number, paragraph: any) => sum + String(paragraph.old_text || "").length, 0), 0);
  const newLength = sections.reduce((total: number, section: any) => total + (section.paragraphs || []).reduce((sum: number, paragraph: any) => sum + String(paragraph.new_text || "").length, 0), 0);
  return { oldLength, newLength, delta: newLength - oldLength };
});
const historicalSections = computed(() => {
  const rows = [...(historicalVersion.value?.sentence_snapshot || [])]
    .filter((row: any) => Number(row.selected ?? 1) !== 0)
    .sort((a: any, b: any) => Number(a.position || 0) - Number(b.position || 0) || Number(a.id || 0) - Number(b.id || 0));
  const sections = new Map<string, Map<number, string[]>>();
  rows.forEach((row: any) => {
    const title = String(row.section || "未命名章节");
    const paragraph = Number(row.paragraph || 1);
    if (!sections.has(title)) sections.set(title, new Map());
    const paragraphs = sections.get(title)!;
    if (!paragraphs.has(paragraph)) paragraphs.set(paragraph, []);
    paragraphs.get(paragraph)!.push(String(row.rendered_text || row.user_edit || row.content || ""));
  });
  return [...sections.entries()].map(([title, paragraphs]) => ({
    title,
    paragraphs: [...paragraphs.entries()].sort((a, b) => a[0] - b[0]).map(([, texts]) => texts.join("")),
  }));
});

watch(() => props.versions, versions => {
  if (!activeVersion.value && versions.length) activeVersion.value = Number(versions[0].id);
}, { immediate: true });

watch(activeVersion, async versionId => {
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
    diff.value = await api(`/api/report-versions/${versionId}/diff-current?granularity=sentence`);
    await loadDecisions();
  } catch (reason: any) {
    error.value = reason.message || "版本差异加载失败";
  } finally { loading.value = false; }
  if (workspaceMode.value === "history") await loadHistoricalVersion();
});

watch(workspaceMode, async mode => {
  if (mode === "history") await loadHistoricalVersion();
});

async function loadHistoricalVersion() {
  if (!activeVersion.value || Number(historicalVersion.value?.id) === activeVersion.value || historicalLoading.value) return;
  historicalLoading.value = true;
  historicalError.value = "";
  try {
    historicalVersion.value = await api(`/api/report-versions/${activeVersion.value}`);
  } catch (reason: any) {
    historicalError.value = reason.message || "历史原文加载失败";
  } finally { historicalLoading.value = false; }
}

async function loadDecisions() {
  if (!activeVersion.value || !diff.value?.candidate_hash) return;
  const result = await api<any>(`/api/report-versions/${activeVersion.value}/decisions?candidate_hash=${encodeURIComponent(diff.value.candidate_hash)}`);
  decisions.value = Object.fromEntries((result.decisions || []).filter((item: Decision) => item.status === "pending").map((item: Decision) => [item.change_key, item]));
}

function sectionScope(section: any): Scope { return { level: "section", section: section.section }; }
function paragraphScope(section: any, paragraph: any): Scope {
  return { level: "paragraph", section: section.section, paragraph: paragraph.old_paragraph ?? paragraph.paragraph, old_paragraph: paragraph.old_paragraph, new_paragraph: paragraph.new_paragraph };
}
function sentenceScope(section: any, paragraph: any, sentence: any): Scope {
  return { level: "sentence", section: section.section, paragraph: paragraph.new_paragraph ?? paragraph.old_paragraph, old_paragraph: paragraph.old_paragraph, new_paragraph: paragraph.new_paragraph, old_sentence_id: sentence.old_sentence_id, current_sentence_id: sentence.current_sentence_id };
}

async function decide(key: string, changeType: string, scope: Scope, decision: "keep_current" | "use_base") {
  if (!activeVersion.value || !diff.value) return;
  await api(`/api/report-versions/${activeVersion.value}/decisions`, jsonInit("POST", { candidate_hash: diff.value.candidate_hash, change_key: key, change_type: changeType, scope, decision }));
  await loadDecisions();
}

async function apply() {
  if (!activeVersion.value || !diff.value || applying.value) return;
  applying.value = true;
  try {
    await api(`/api/report-versions/${activeVersion.value}/decisions/apply`, jsonInit("POST", { candidate_hash: diff.value.candidate_hash }));
    emit("applied");
  } catch (reason: any) {
    alert(reason.message || "应用失败，候选稿可能已经变化，请重新加载。")
  } finally { applying.value = false; }
}

function decision(key: string) { return decisions.value[key]?.decision || ""; }
function decisionLabel(key: string) { return decision(key) === "use_base" ? "采用历史版本" : decision(key) === "keep_current" ? "保留当前版本" : "默认保留当前版本"; }
function paragraphKey(section: any, paragraph: any) { return `${section.section}:${paragraph.change_key}`; }
function toggleFine(section: any, paragraph: any) { const key = paragraphKey(section, paragraph); fineParagraph.value = fineParagraph.value === key ? "" : key; }
function changedLabel(type: string, unit = "") { return type === "added" ? `新增${unit}` : type === "removed" ? `删除${unit}` : `修改${unit}`; }

function reviewParagraphs(section: any) {
  const paragraphs = section.paragraphs || [];
  const changed = new Set<number>();
  paragraphs.forEach((paragraph: any, index: number) => { if (paragraph.change_type !== "unchanged" && (filter.value === "all" || paragraph.change_type === filter.value)) changed.add(index); });
  const visible = new Set<number>();
  changed.forEach(index => { visible.add(index - 1); visible.add(index); visible.add(index + 1); });
  return paragraphs.map((paragraph: any, index: number) => ({ ...paragraph, _context: !changed.has(index), _index: index })).filter((_: any, index: number) => visible.has(index));
}

function spans(sentence: any, side: "old" | "new") { return sentence.inline_diff?.[side] || [{ type: "unchanged", text: side === "old" ? sentence.old_text : sentence.new_text }]; }
function sourceImpact(sentence: any) {
  const source = sentence.new_sentence || sentence.old_sentence || {};
  const refs = source.source_refs || {};
  const facts = (refs.fact_ids || source.fact_ids || []).length;
  const inferences = (refs.inference_ids || source.inference_ids || []).length;
  return [facts ? `${facts} 条事实` : "", inferences ? `${inferences} 条推论` : ""].filter(Boolean).join(" · ") || "结构或过渡表达";
}
function previewSentence(sentence: any) {
  if (decision(sentence.change_key) === "use_base") return sentence.change_type === "added" ? "" : sentence.old_text || "";
  return sentence.change_type === "removed" ? "" : sentence.new_text || "";
}
function previewParagraph(section: any, paragraph: any) {
  if (decision(section.change_key) === "use_base" || decision(paragraph.change_key) === "use_base") return paragraph.old_text || "";
  return (paragraph.sentences || []).map(previewSentence).join("");
}
function previewSection(section: any) { return (section.paragraphs || []).map((paragraph: any) => previewParagraph(section, paragraph)).filter(Boolean); }
</script>

<template>
  <section class="review-workspace">
    <header class="review-header">
      <div><span class="eyebrow">VERSION REVIEW</span><h2>版本审阅</h2><p>先理解段落变化，需要时再逐句调整。</p></div>
      <div v-if="diff" class="header-stats"><span>章节 <b>{{diff.summary?.sections_changed||0}}</b></span><span>段落 <b>{{diff.summary?.paragraphs_changed||0}}</b></span><span>篇幅 <b :class="lengths.delta>=0?'positive':'negative'">{{lengths.delta>=0?'+':''}}{{lengths.delta}}</b></span></div>
      <div class="header-actions"><button :class="{active:workspaceMode==='review'}" @click="workspaceMode='review'">审阅差异</button><button :class="{active:workspaceMode==='history'}" @click="workspaceMode='history'">历史原文</button><button :class="{active:workspaceMode==='preview'}" @click="workspaceMode='preview'">合并预览</button><button @click="emit('close')">关闭</button></div>
    </header>

    <aside class="review-navigation">
      <section><h3>对比基线</h3><button class="snapshot-button" @click="emit('snapshot')">生成当前快照</button><button v-for="version in versions" :key="version.id" class="version-option" :class="{active:activeVersion===Number(version.id)}" @click="activeVersion=Number(version.id)"><span><b>v{{version.version_label||version.version_no}}</b><small>{{version.change_summary||'版本快照'}}</small></span><time>{{version.created_at}}</time></button></section>
      <section v-if="diff"><h3>变化章节</h3><button class="chapter-option" :class="{active:activeSection==='all'}" @click="activeSection='all'">全部变化 <span>{{changedSections.length}}</span></button><button v-for="section in changedSections" :key="section.section" class="chapter-option" :class="{active:activeSection===section.section}" @click="activeSection=section.section">{{section.section}}<span>{{(section.paragraphs||[]).filter((paragraph:any)=>paragraph.change_type!=='unchanged').length}}</span></button></section>
    </aside>

    <main class="review-main">
      <div v-if="workspaceMode==='history' && historicalLoading" class="review-state"><span class="spinner"></span><p>正在加载历史原文…</p></div>
      <div v-else-if="workspaceMode==='history' && historicalError" class="review-state"><b>无法加载历史原文</b><p>{{historicalError}}</p></div>
      <div v-else-if="workspaceMode==='history' && historicalVersion" class="historical-preview">
        <header><span>历史版本 v{{versionMeta?.version_label||versionMeta?.version_no}}</span><h1>{{historicalVersion.title||reportTitle}}</h1><p>{{versionMeta?.created_at}} · {{versionMeta?.change_summary||'版本快照'}}</p></header>
        <section v-for="section in historicalSections" :key="section.title"><h2>{{section.title}}</h2><p v-for="(paragraph,index) in section.paragraphs" :key="index">{{paragraph}}</p></section>
        <div v-if="!historicalSections.length" class="review-state"><b>该版本没有正文</b><p>版本元数据仍然保留。</p></div>
      </div>
      <div v-else-if="loading" class="review-state"><span class="spinner"></span><p>正在建立版本差异…</p></div>
      <div v-else-if="error" class="review-state"><b>无法加载差异</b><p>{{error}}</p></div>
      <template v-else-if="diff">
        <div v-if="workspaceMode==='review'" class="review-document">
          <div class="filterbar"><div class="tabs"><button v-for="item in [['all','全部'],['modified','修改'],['added','新增'],['removed','删除']]" :key="item[0]" :class="{active:filter===item[0]}" @click="filter=item[0]">{{item[1]}}</button></div><span>未处理内容默认保留当前候选稿</span></div>
          <section v-for="section in visibleSections" :key="section.section" class="review-section">
            <header><div><span :class="['change-kind',section.change_type]">{{changedLabel(section.change_type,'章节')}}</span><h2>{{section.section}}</h2></div><div class="scope-actions"><small>{{decisionLabel(section.change_key)}}</small><button :class="{chosen:decision(section.change_key)==='keep_current'}" @click="decide(section.change_key,section.change_type,sectionScope(section),'keep_current')">保留本章新版</button><button :class="{chosen:decision(section.change_key)==='use_base'}" @click="decide(section.change_key,section.change_type,sectionScope(section),'use_base')">采用本章旧版</button></div></header>
            <article v-for="paragraph in reviewParagraphs(section)" :key="paragraph.change_key" :class="['review-paragraph',paragraph.change_type,{context:paragraph._context}]">
              <template v-if="paragraph._context"><span class="context-label">上下文</span><p>{{paragraph.new_text||paragraph.old_text}}</p></template>
              <template v-else>
                <div class="paragraph-head"><div><span :class="['change-kind',paragraph.change_type]">{{changedLabel(paragraph.change_type)}}</span><b>第 {{paragraph.new_paragraph||paragraph.old_paragraph}} 段</b><small>{{decisionLabel(paragraph.change_key)}}</small></div><div class="scope-actions compact"><button :class="{chosen:decision(paragraph.change_key)==='keep_current'}" @click="decide(paragraph.change_key,paragraph.change_type,paragraphScope(section,paragraph),'keep_current')">采用当前段落</button><button :class="{chosen:decision(paragraph.change_key)==='use_base'}" @click="decide(paragraph.change_key,paragraph.change_type,paragraphScope(section,paragraph),'use_base')">恢复历史段落</button><button @click="toggleFine(section,paragraph)">{{fineParagraph===paragraphKey(section,paragraph)?'收起精调':'逐句调整'}}</button></div></div>
                <div class="paragraph-compare"><div><label>历史版本</label><p>{{paragraph.old_text||'（本段不存在）'}}</p></div><div><label>当前候选稿</label><p>{{paragraph.new_text||'（本段已删除）'}}</p></div></div>
                <div v-if="fineParagraph===paragraphKey(section,paragraph)" class="sentence-review">
                  <p class="fine-note">逐句决定会替代本段整体决定；每句话仍保留原有事实和推论绑定。</p>
                  <article v-for="sentence in (paragraph.sentences||[]).filter((item:any)=>item.change_type!=='unchanged')" :key="sentence.change_key" :class="['sentence-change',sentence.change_type]">
                    <header><span>{{changedLabel(sentence.change_type,'句')}}</span><small>{{sourceImpact(sentence)}}</small><b>{{decisionLabel(sentence.change_key)}}</b></header>
                    <div class="inline-compare"><p><label>历史</label><span v-for="(part,index) in spans(sentence,'old')" :key="index" :class="`inline-${part.type}`">{{part.text}}</span><i v-if="!sentence.old_text">（无）</i></p><p><label>当前</label><span v-for="(part,index) in spans(sentence,'new')" :key="index" :class="`inline-${part.type}`">{{part.text}}</span><i v-if="!sentence.new_text">（无）</i></p></div>
                    <div class="sentence-actions"><button :class="{chosen:decision(sentence.change_key)==='keep_current'}" @click="decide(sentence.change_key,sentence.change_type,sentenceScope(section,paragraph,sentence),'keep_current')">{{sentence.change_type==='removed'?'保持删除':'保留当前'}}</button><button :class="{chosen:decision(sentence.change_key)==='use_base'}" @click="decide(sentence.change_key,sentence.change_type,sentenceScope(section,paragraph,sentence),'use_base')">{{sentence.change_type==='added'?'删除新增':'恢复原句'}}</button></div>
                  </article>
                </div>
              </template>
            </article>
          </section>
          <div v-if="!visibleSections.length" class="review-state"><b>没有符合条件的差异</b><p>调整筛选条件或选择其他版本。</p></div>
        </div>
        <div v-else class="merged-preview"><header><span>应用后预览</span><h1>{{reportTitle}}</h1><p>根据当前暂存决定实时组装，不会立即修改报告。</p></header><section v-for="section in (diff.sections||[])" :key="section.section"><h2>{{section.section}}</h2><p v-for="(paragraph,index) in previewSection(section)" :key="index">{{paragraph}}</p></section></div>
      </template>
      <div v-else class="review-state"><b>选择一个历史版本</b><p>系统将以段落为主展示它与当前候选稿的差异。</p></div>
    </main>

    <aside class="review-summary"><h3>审阅概览</h3><div class="baseline-card"><span>历史基线</span><b>v{{versionMeta?.version_label||versionMeta?.version_no||'—'}}</b><small>{{versionMeta?.change_summary||'版本快照'}}</small></div><dl v-if="diff"><div><dt>历史篇幅</dt><dd>{{lengths.oldLength}}</dd></div><div><dt>候选篇幅</dt><dd>{{lengths.newLength}}</dd></div><div><dt>明确决定</dt><dd>{{explicitCount}}</dd></div><div><dt>恢复旧版</dt><dd>{{restoredCount}}</dd></div></dl><div v-if="diff" class="review-progress"><div><span>逐句决定</span><b>{{reviewedCount}} / {{changedSentences.length}}</b></div><progress :value="reviewedCount" :max="Math.max(changedSentences.length,1)"></progress><p>章节或段落决定会覆盖内部句子，不要求逐句点击。</p></div><div class="principle"><b>应用规则</b><p>默认保留当前稿；只有选择“采用历史版本”的范围会被恢复。应用前自动保存候选稿快照。</p></div><button class="apply-button" :disabled="!diff||applying" @click="apply">{{applying?'正在生成版本…':'完成审阅并生成版本'}}</button></aside>
  </section>
</template>

<style scoped>
.review-workspace{position:fixed;inset:var(--header-height) 0 0 var(--nav-width);z-index:80;display:grid;grid-template:88px minmax(0,1fr)/250px minmax(560px,1fr) 292px;background:#f3f4f6;color:var(--color-text)}button{font:inherit}.review-header{grid-column:1/-1;display:grid;grid-template-columns:250px 1fr auto;align-items:center;gap:20px;min-height:88px;padding:10px 22px;background:#fff;border-bottom:1px solid var(--color-border)}.review-header>div:first-child{align-self:center;min-width:0}.review-header h2{margin:1px 0;font-family:Georgia,"Noto Serif SC",serif;font-size:21px;line-height:1.25}.review-header p{margin:2px 0 0;color:var(--color-muted);font-size:12px;line-height:1.35;white-space:nowrap}.eyebrow{display:block;color:var(--color-primary);font-size:9px;font-weight:700;line-height:1.2;letter-spacing:.14em}.header-stats{display:flex;gap:24px;color:var(--color-muted);font-size:12px}.header-stats b{margin-left:5px;color:var(--color-text);font-size:16px}.header-stats .positive{color:var(--color-success)}.header-stats .negative{color:var(--color-danger)}.header-actions{display:flex;gap:7px}.header-actions button{padding:7px 11px;border:1px solid var(--color-border);border-radius:5px;background:#fff}.header-actions button.active{color:var(--color-primary);border-color:#9bb7d5;background:var(--color-primary-soft)}
.review-navigation,.review-summary{min-height:0;padding:18px;background:#fafbfc;overflow:auto}.review-navigation{border-right:1px solid var(--color-border)}.review-summary{border-left:1px solid var(--color-border)}.review-navigation section+section{margin-top:24px}.review-navigation h3,.review-summary h3{margin:0 0 10px;font-size:12px}.version-option,.chapter-option{display:flex;width:100%;align-items:center;justify-content:space-between;gap:8px;padding:10px;border:0;border-bottom:1px solid var(--color-border);background:transparent;text-align:left}.version-option:hover,.chapter-option:hover{background:#f0f2f5}.version-option.active,.chapter-option.active{color:var(--color-primary);background:var(--color-primary-soft)}.version-option span,.version-option small{display:block}.version-option small{max-width:118px;margin-top:3px;color:var(--color-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.version-option time{color:var(--color-faint);font-size:9px}.chapter-option{font-size:12px}.chapter-option span{color:var(--color-faint)}
.snapshot-button{width:100%;margin-bottom:8px;padding:7px;border:1px solid var(--color-border);border-radius:5px;background:#fff;color:var(--color-primary);font-size:11px}.snapshot-button:hover{border-color:#9bb7d5;background:var(--color-primary-soft)}
.review-main{min-width:0;overflow:auto;padding:22px}.review-document,.merged-preview,.historical-preview{width:min(100%,960px);margin:0 auto}.filterbar{position:sticky;top:-22px;z-index:4;display:flex;align-items:center;justify-content:space-between;padding:10px 0;background:#f3f4f6ef}.filterbar .tabs{display:flex}.filterbar button{padding:6px 11px;border:0;border-bottom:2px solid transparent;background:transparent}.filterbar button.active{color:var(--color-primary);border-bottom-color:var(--color-primary)}.filterbar>span{color:var(--color-muted);font-size:11px}.review-section{margin-bottom:24px;padding:24px 28px;background:#fff;border:1px solid var(--color-border);box-shadow:0 1px 2px #11182708}.review-section>header{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;padding-bottom:17px;border-bottom:1px solid var(--color-border)}.review-section h2{margin:4px 0 0;font-family:Georgia,"Noto Serif SC",serif;font-size:20px}.change-kind{font-size:10px;font-weight:700}.change-kind.modified{color:#9a6a25}.change-kind.added{color:#397b50}.change-kind.removed{color:#aa4747}.scope-actions,.sentence-actions{display:flex;align-items:center;gap:5px}.scope-actions small{margin-right:4px;color:var(--color-faint)}.scope-actions button,.sentence-actions button{padding:5px 8px;border:1px solid var(--color-border);border-radius:4px;background:#fff;color:var(--color-muted);font-size:10px}.scope-actions button:hover,.scope-actions button.chosen,.sentence-actions button:hover,.sentence-actions button.chosen{color:var(--color-primary);border-color:#9bb7d5;background:var(--color-primary-soft)}.scope-actions.compact{flex-wrap:wrap;justify-content:flex-end}
.review-paragraph{margin-top:18px;border-left:3px solid #c18a3d}.review-paragraph.added{border-left-color:#579269}.review-paragraph.removed{border-left-color:#c95d5d}.review-paragraph.context{display:flex;gap:12px;margin:12px 0 12px 3px;padding:8px 12px;border-left:1px solid var(--color-border);background:#fafbfc;color:var(--color-muted)}.review-paragraph.context p{margin:0;font-family:var(--font-doc);font-size:13px;line-height:1.75}.context-label{flex:0 0 auto;color:var(--color-faint);font-size:9px}.paragraph-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:9px 12px;background:#fafbfc;border-bottom:1px solid var(--color-border)}.paragraph-head>div:first-child{display:flex;align-items:center;gap:8px}.paragraph-head small{color:var(--color-faint)}.paragraph-compare{display:grid;grid-template-columns:1fr 1fr}.paragraph-compare>div{padding:15px 18px}.paragraph-compare>div:first-child{border-right:1px solid var(--color-border);background:#fcfcfd}.paragraph-compare label,.inline-compare label{display:block;margin-bottom:7px;color:var(--color-faint);font:10px var(--font-ui)}.paragraph-compare p{margin:0;font-family:var(--font-doc);font-size:14px;line-height:1.9;text-align:justify;text-indent:2em}
.sentence-review{padding:14px;background:#f6f7f8;border-top:1px solid var(--color-border)}.fine-note{margin:0 0 10px;color:var(--color-muted);font-size:11px}.sentence-change{margin:9px 0;background:#fff;border:1px solid var(--color-border)}.sentence-change>header{display:flex;align-items:center;gap:10px;padding:7px 10px;border-bottom:1px solid var(--color-border)}.sentence-change>header span{font-size:10px;font-weight:700}.sentence-change>header small{color:var(--color-faint)}.sentence-change>header b{margin-left:auto;color:var(--color-muted);font-size:10px}.inline-compare{display:grid;grid-template-columns:1fr 1fr}.inline-compare p{margin:0;padding:11px 13px;font-family:var(--font-doc);font-size:13px;line-height:1.8}.inline-compare p:first-child{border-right:1px solid var(--color-border)}.inline-removed{color:#8b3232;background:#fce8e8;text-decoration:line-through}.inline-added{color:#245f39;background:#e5f4e9}.inline-compare i{color:var(--color-faint);font-style:normal}.sentence-actions{justify-content:flex-end;padding:7px 10px;border-top:1px solid var(--color-border)}
.merged-preview,.historical-preview{min-height:100%;padding:64px 76px 90px;background:#fff;border:1px solid #dfe2e6;box-shadow:var(--shadow-paper);font-family:var(--font-doc)}.merged-preview>header,.historical-preview>header{text-align:center}.merged-preview>header>span,.historical-preview>header>span{color:var(--color-primary);font:10px var(--font-ui);letter-spacing:.12em}.merged-preview h1,.historical-preview h1{margin:8px 0 5px;font-size:25px}.merged-preview>header p,.historical-preview>header p{color:var(--color-muted);font:11px var(--font-ui)}.merged-preview h2,.historical-preview h2{margin:32px 0 14px;font-size:19px}.merged-preview section p,.historical-preview section p{margin:0 0 14px;font-size:16px;line-height:1.95;text-align:justify;text-indent:2em}.baseline-card{padding:13px;background:#fff;border:1px solid var(--color-border)}.baseline-card span,.baseline-card b,.baseline-card small{display:block}.baseline-card span,.baseline-card small{color:var(--color-muted);font-size:10px}.baseline-card b{margin:4px 0;font-size:20px}.review-summary dl{display:grid;grid-template-columns:1fr 1fr;margin:18px 0;border-top:1px solid var(--color-border);border-left:1px solid var(--color-border)}.review-summary dl div{padding:10px;background:#fff;border-right:1px solid var(--color-border);border-bottom:1px solid var(--color-border)}.review-summary dt{color:var(--color-faint);font-size:9px}.review-summary dd{margin:3px 0 0;font-size:17px}.review-progress>div{display:flex;justify-content:space-between;font-size:11px}.review-progress progress{width:100%;height:5px;margin:8px 0;accent-color:var(--color-primary)}.review-progress p,.principle p{color:var(--color-muted);font-size:10px;line-height:1.6}.principle{margin:18px 0;padding:12px;border-left:2px solid var(--color-primary);background:#fff}.principle b{font-size:11px}.apply-button{width:100%;padding:9px;border:1px solid var(--color-primary);border-radius:5px;background:var(--color-primary);color:#fff}.apply-button:disabled{opacity:.55}.review-state{display:grid;place-items:center;min-height:240px;color:var(--color-muted);text-align:center}.review-state b{color:var(--color-text)}.review-state p{margin:5px}.spinner{width:22px;height:22px;border:2px solid var(--color-border);border-top-color:var(--color-primary);border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:1350px){.review-workspace{grid-template-columns:220px minmax(500px,1fr) 250px}.review-header{grid-template-columns:220px 1fr auto}.header-stats{gap:12px}}@media(max-width:1100px){.review-workspace{inset:var(--header-height) 0 0;grid-template-columns:210px 1fr}.review-summary{display:none}.review-header{grid-template-columns:210px 1fr}.header-stats{display:none}}@media(max-width:800px){.review-workspace{grid-template:64px 150px minmax(0,1fr)/1fr}.review-header{grid-column:1;grid-template-columns:1fr auto}.review-header p,.header-actions button:not(:last-child){display:none}.review-navigation{display:flex;gap:12px;border-right:0;border-bottom:1px solid var(--color-border)}.review-navigation section{min-width:220px}.review-navigation section+section{margin-top:0}.review-main{padding:12px}.paragraph-compare,.inline-compare{grid-template-columns:1fr}.paragraph-compare>div:first-child,.inline-compare p:first-child{border-right:0;border-bottom:1px solid var(--color-border)}.merged-preview{padding:40px 28px}}
</style>
