<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { useRouter } from "vue-router";
import { api, jsonInit } from "@/api/http";

const props = withDefaults(defineProps<{
  reportId: number; versions?: any[]; comparisonId?: number | null; embedded?: boolean;
}>(), { versions: () => [], comparisonId: null, embedded: false });
const emit = defineEmits<{ close: []; handoff: [payload: any] }>();
const router = useRouter();
const qc = useQueryClient();
const files = ref<HTMLInputElement>();
const focus = ref("");
const baseVersionId = ref<number | null>(props.versions?.[0]?.id || null);
const selectedId = ref<number | null>(props.comparisonId || null);
const selectedItemId = ref<number | null>(null);
const relationFilter = ref("all");
const contentMode = ref<"report" | "findings">("report");
const pending = ref(false);
const error = ref("");

watch(() => props.comparisonId, value => { if (value) selectedId.value = value; }, { immediate: true });
const runs = useQuery({
  queryKey: ["material-comparisons", props.reportId],
  queryFn: () => api<any[]>(`/api/reports/${props.reportId}/material-comparisons`),
  enabled: computed(() => !props.embedded), refetchInterval: 5000,
});
const detail = useQuery({
  queryKey: ["material-comparison", selectedId],
  queryFn: () => api<any>(`/api/material-comparisons/${selectedId.value}`),
  enabled: computed(() => Boolean(selectedId.value)),
  refetchInterval: q => ["ready", "failed"].includes(String(q.state.data?.status)) ? false : 4000,
});
const labels: Record<string, string> = {
  addition: "新增事实", corroboration: "新增佐证", refinement: "补充细节",
  update: "后续发展", conflict: "事实冲突", weakening: "证据削弱",
  related: "相关信息", irrelevant: "暂不相关", uncertain: "待核验",
};
const filterOrder = ["conflict", "update", "corroboration", "refinement", "addition", "weakening", "related", "uncertain"];
const items = computed<any[]>(() => detail.data.value?.items || []);
const visibleItems = computed(() => relationFilter.value === "all"
  ? items.value.filter(item => item.change_type !== "irrelevant")
  : items.value.filter(item => item.change_type === relationFilter.value));
const selectedItem = computed(() => items.value.find(item => item.id === selectedItemId.value) || null);
const relationCounts = computed(() => {
  const counts: Record<string, number> = {};
  for (const item of items.value) counts[item.change_type] = (counts[item.change_type] || 0) + 1;
  return counts;
});
const mappedItemIds = computed(() => new Set<number>(
  (detail.data.value?.document?.sentences || []).flatMap((sentence: any) =>
    (sentence.changes || []).map((change: any) => Number(change.item_id)),
  ),
));
const mappedItems = computed(() => items.value.filter(item => item.change_type !== "irrelevant" && mappedItemIds.value.has(Number(item.id))));
const independentItems = computed(() => items.value.filter(item => item.change_type !== "irrelevant" && !mappedItemIds.value.has(Number(item.id))));
const visibleIndependentItems = computed(() => relationFilter.value === "all"
  ? independentItems.value
  : independentItems.value.filter(item => item.change_type === relationFilter.value));
const affectedSentenceCount = computed(() => (detail.data.value?.document?.sentences || [])
  .filter((sentence: any) => (sentence.changes || []).some((change: any) => {
    const item = items.value.find(candidate => candidate.id === change.item_id);
    return item && item.change_type !== "irrelevant";
  })).length);
const documentSections = computed(() => {
  const sections: Array<{ title: string; paragraphs: Array<{ id: string; sentences: any[] }> }> = [];
  const sectionMap = new Map<string, Map<string, any[]>>();
  for (const sentence of detail.data.value?.document?.sentences || []) {
    const section = String(sentence.section || "正文");
    const paragraph = String(sentence.paragraph ?? 0);
    if (!sectionMap.has(section)) sectionMap.set(section, new Map());
    const paragraphs = sectionMap.get(section)!;
    if (!paragraphs.has(paragraph)) paragraphs.set(paragraph, []);
    paragraphs.get(paragraph)!.push(sentence);
  }
  for (const [title, paragraphs] of sectionMap) {
    sections.push({ title, paragraphs: [...paragraphs].map(([id, sentences]) => ({ id, sentences })) });
  }
  return sections;
});
watch(items, value => {
  if (value.length && !value.some(item => item.id === selectedItemId.value)) {
    selectedItemId.value = value.find(item => item.change_type !== "irrelevant")?.id || value[0].id;
  }
}, { immediate: true });

function changesFor(sentence: any) {
  const changes = (sentence.changes || []).filter((item: any) => item.change_type !== "irrelevant");
  return relationFilter.value === "all" ? changes : changes.filter((item: any) => item.change_type === relationFilter.value);
}
function selectSentence(sentence: any) { const changes = changesFor(sentence); if (changes.length) selectedItemId.value = changes[0].item_id; }
function selectItem(item: any) {
  selectedItemId.value = item.id;
  const sentence = (detail.data.value?.document?.sentences || []).find((entry: any) =>
    (entry.changes || []).some((change: any) => change.item_id === item.id),
  );
  contentMode.value = sentence ? "report" : "findings";
  requestAnimationFrame(() => {
    const selector = sentence ? `[data-sentence-id="${sentence.id}"]` : `[data-finding-id="${item.id}"]`;
    document.querySelector(selector)?.scrollIntoView({ block: "center", behavior: "smooth" });
  });
}
function evidenceLabel(item: any) { return [item.source_file, item.page ? `第 ${item.page} 页` : "", item.paragraph ? `第 ${item.paragraph} 段` : ""].filter(Boolean).join(" · "); }
async function create() {
  if (!files.value?.files?.length) { error.value = "请选择新增材料"; return; }
  pending.value = true; error.value = "";
  try {
    const form = new FormData(); form.set("focus", focus.value);
    if (baseVersionId.value) form.set("base_version_id", String(baseVersionId.value));
    for (const file of files.value.files) form.append("files", file);
    const result = await api<any>(`/api/reports/${props.reportId}/material-comparisons`, { method: "POST", body: form });
    await qc.invalidateQueries({ queryKey: ["material-comparisons", props.reportId] });
    await router.push(`/tasks/${result.task_id}?tab=comparison`);
  } catch (e: any) { error.value = e.message || "创建失败"; }
  finally { pending.value = false; }
}
async function review(item: any, status: string) {
  await api(`/api/material-comparisons/${selectedId.value}/items/${item.id}`, jsonInit("PATCH", { status }));
  await qc.invalidateQueries({ queryKey: ["material-comparison", selectedId] });
}
async function handoff() {
  try { emit("handoff", await api(`/api/material-comparisons/${selectedId.value}/update-handoff`, { method: "POST" })); }
  catch (e: any) { error.value = e.message || "请先确认需要进入报告更新的变化"; }
}
</script>

<template>
  <section class="comparison-workspace" :class="{ embedded }">
    <header v-if="!embedded"><div><small>独立审阅任务</small><h2>新增材料对比</h2><p>以既有报告为基线，核验新材料带来的事实变化，不自动修改正文。</p></div><button class="btn tertiary" @click="$emit('close')">关闭</button></header>
    <div class="comparison-shell" :class="{ 'with-runs': !embedded }">
      <aside v-if="!embedded" class="run-panel">
        <div class="create-box"><label>对比基线<select v-model="baseVersionId"><option v-for="version in versions" :key="version.id" :value="version.id">版本 {{ version.version_label || version.version_no }}</option></select></label><label>新增材料<input ref="files" type="file" multiple></label><label>关注问题<textarea v-model="focus" rows="2" placeholder="可选；默认检查全部事实变化"></textarea></label><button class="btn primary" :disabled="pending" @click="create">{{ pending ? "正在创建…" : "创建材料对比任务" }}</button></div>
        <button v-for="run in runs.data.value || []" :key="run.id" class="run-item" :class="{ active: selectedId === run.id }" @click="selectedId = run.id"><b>{{ run.created_at }}</b><span>{{ run.status === "ready" ? "等待审阅" : run.status === "failed" ? "失败" : "运行中" }} · {{ run.summary?.new_fact_count || 0 }} 条新事实</span></button>
      </aside>
      <main v-if="detail.data.value" class="result-workspace">
        <div class="result-head"><div><small>对比基线 · 版本 {{ detail.data.value.baseline?.version_label || detail.data.value.base_version_id }}</small><h2>{{ detail.data.value.baseline?.title || "报告材料变化" }}</h2></div><div class="summary-strip"><div><strong>{{ items.filter(x => x.change_type !== 'irrelevant').length }}</strong><span>需审阅变化</span></div><div><strong>{{ mappedItems.length }}</strong><span>关联正文</span></div><div><strong>{{ affectedSentenceCount }}</strong><span>受影响句子</span></div><div><strong>{{ independentItems.length }}</strong><span>独立发现</span></div></div></div>
        <div v-if="detail.data.value.status !== 'ready'" class="notice" :class="{ warning: detail.data.value.status === 'failed' }">{{ detail.data.value.status === "failed" ? detail.data.value.error : "正在解析新增材料并建立事实关联，已有结果会保留。" }}</div>
        <div v-else class="review-layout">
          <aside class="relation-panel">
            <h3>变化类型</h3><button :class="{ active: relationFilter === 'all' }" @click="relationFilter = 'all'"><span>全部需审阅</span><b>{{ items.filter(x => x.change_type !== 'irrelevant').length }}</b></button>
            <button v-for="type in filterOrder.filter(x => relationCounts[x])" :key="type" :class="[type, { active: relationFilter === type }]" @click="relationFilter = type"><span>{{ labels[type] }}</span><b>{{ relationCounts[type] }}</b></button>
            <details v-if="relationCounts.irrelevant"><summary>暂不相关 {{ relationCounts.irrelevant }}</summary><button v-for="item in items.filter(x => x.change_type === 'irrelevant')" :key="item.id" class="minor-item" @click="selectItem(item)">{{ item.title }}</button></details>
            <div class="change-index"><small>变化条目</small><button v-for="item in visibleItems" :key="item.id" :class="{ active: selectedItemId === item.id }" @click="selectItem(item)"><i :class="item.change_type"></i><span>{{ item.title }}</span></button></div>
          </aside>
          <article class="content-workspace">
            <div class="content-switch"><button :class="{ active: contentMode === 'report' }" @click="contentMode = 'report'">报告正文 <b>{{ mappedItems.length }}</b></button><button :class="{ active: contentMode === 'findings' }" @click="contentMode = 'findings'">独立发现 <b>{{ independentItems.length }}</b></button></div>
            <div v-if="contentMode === 'report'" class="baseline-document">
              <div class="document-note"><span>基线报告正文</span><small>{{ mappedItems.length }} 条变化精确关联到 {{ affectedSentenceCount }} 个句子；句后数字表示该句关联的变化数量。</small></div>
              <section v-for="section in documentSections" :key="section.title"><h2>{{ section.title }}</h2><p v-for="paragraph in section.paragraphs" :key="paragraph.id"><span v-for="sentence in paragraph.sentences" :key="sentence.id" class="report-sentence" :class="[changesFor(sentence)[0]?.change_type, { affected: changesFor(sentence).length, selected: changesFor(sentence).some((x:any) => x.item_id === selectedItemId) }]" :data-sentence-id="sentence.id" @click="selectSentence(sentence)">{{ sentence.text }}<sup v-if="changesFor(sentence).length">{{ changesFor(sentence).length }}</sup></span></p></section>
              <div v-if="!documentSections.length" class="empty"><div><strong>基线版本尚无可定位正文</strong>变化仍可在“独立发现”中按事实逐条审阅。</div></div>
            </div>
            <div v-else class="finding-document">
              <div class="document-note"><span>新材料独立发现</span><small>这些事实与任务有关，但没有足够依据绑定到某个既有报告句子，因此不会在正文中强行高亮。</small></div>
              <button v-for="item in visibleIndependentItems" :key="item.id" class="finding-row" :class="{ selected: selectedItemId === item.id }" :data-finding-id="item.id" @click="selectItem(item)"><span class="relation-badge" :class="item.change_type">{{ labels[item.change_type] }}</span><div><b>{{ item.title }}</b><p>{{ item.evidence?.new_fact?.content }}</p><small>{{ (item.evidence?.new_fact?.evidence || []).map((x:any) => evidenceLabel(x)).filter(Boolean).join('；') || '来源定位待核验' }}</small></div></button>
              <div v-if="!visibleIndependentItems.length" class="empty"><div><strong>当前筛选下没有独立发现</strong>切换变化类型或查看报告正文。</div></div>
            </div>
          </article>
          <aside class="evidence-panel">
            <template v-if="selectedItem"><div class="evidence-head"><span class="relation-badge" :class="selectedItem.change_type">{{ labels[selectedItem.change_type] || selectedItem.change_type }}</span><small>{{ selectedItem.confidence === "high" ? "高" : selectedItem.confidence === "low" ? "低" : "中" }}置信度</small></div><h3>{{ selectedItem.title }}</h3><p class="rationale">{{ selectedItem.rationale }}</p><div class="fact-block baseline"><small>报告原有事实</small><p>{{ selectedItem.evidence?.baseline_fact?.content || "报告中没有对应事实，这是独立新增信息。" }}</p></div><div class="fact-block current"><small>新材料事实</small><p>{{ selectedItem.evidence?.new_fact?.content }}</p></div><div class="source-list"><h4>新材料依据</h4><blockquote v-for="source in selectedItem.evidence?.new_fact?.evidence || []" :key="source.evidence_id || source.unit_id"><b>{{ evidenceLabel(source) }}</b><p>{{ source.quote }}</p><small>原始单元 {{ source.unit_id }}</small></blockquote><p v-if="!selectedItem.evidence?.new_fact?.evidence?.length" class="muted">该历史对比项尚未取得可展示的原文定位。</p></div><div class="review-actions"><button class="btn" :class="{ primary: selectedItem.status === 'accepted' }" @click="review(selectedItem, 'accepted')">确认变化</button><button class="btn" :class="{ primary: selectedItem.status === 'needs_verification' }" @click="review(selectedItem, 'needs_verification')">待核验</button><button class="btn tertiary" @click="review(selectedItem, 'ignored')">忽略</button></div></template>
            <div v-else class="empty"><div><strong>选择一条变化</strong>查看它与报告句子、事实和原始材料的关系。</div></div>
          </aside>
        </div>
        <button v-if="!embedded && detail.data.value.status === 'ready'" class="btn handoff" @click="handoff">将已确认变化用于报告更新</button>
      </main>
      <div v-else class="empty"><div><strong>{{ embedded ? "正在读取对比结果" : "选择一次对比" }}</strong>{{ embedded ? "完成分析后将在这里逐句展示材料变化。" : "查看新材料相对于报告基线带来的变化。" }}</div></div>
    </div><p v-if="error" class="error-text">{{ error }}</p>
  </section>
</template>

<style scoped>
.comparison-workspace{position:fixed;inset:calc(var(--header-height) + 16px) 20px 20px;z-index:40;display:grid;grid-template-rows:auto 1fr;overflow:hidden;background:#f5f6f8;border:1px solid var(--color-border-strong);box-shadow:var(--shadow-drawer)}.comparison-workspace.embedded{position:relative;inset:auto;z-index:auto;display:block;min-height:680px;border:0;box-shadow:none}.comparison-workspace>header{display:flex;justify-content:space-between;padding:18px 24px;background:#fff;border-bottom:1px solid var(--color-border)}.comparison-workspace h2,.comparison-workspace p{margin-top:0}.comparison-workspace header small{color:var(--color-primary);font-weight:650}.comparison-shell{display:grid;min-height:0;height:100%}.comparison-shell.with-runs{grid-template-columns:280px 1fr}.run-panel{overflow:auto;padding:16px;background:#fff;border-right:1px solid var(--color-border)}.create-box{display:grid;gap:10px;padding-bottom:18px;border-bottom:1px solid var(--color-border)}.create-box label{display:grid;gap:4px;color:var(--color-muted);font-size:12px}.run-item{display:grid;width:100%;padding:12px 8px;text-align:left;border:0;border-bottom:1px solid var(--color-border);background:transparent}.run-item.active{color:var(--color-primary);background:var(--color-primary-soft)}.run-item span{color:var(--color-muted);font-size:12px}.result-workspace{position:relative;min-width:0;overflow:hidden}.result-head{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:14px 22px;background:#fff;border-bottom:1px solid var(--color-border)}.result-head h2{margin:2px 0;font-size:18px}.result-head small{color:var(--color-muted)}.summary-strip{display:flex;border-left:1px solid var(--color-border)}.summary-strip>div{min-width:78px;padding:3px 14px;border-right:1px solid var(--color-border)}.summary-strip strong,.summary-strip span{display:block}.summary-strip strong{font-size:18px}.summary-strip span{color:var(--color-muted);font-size:11px}.review-layout{display:grid;grid-template-columns:190px minmax(480px,1fr) 340px;height:calc(100% - 74px);min-height:610px}.relation-panel,.baseline-document,.evidence-panel{min-height:0;overflow:auto}.relation-panel{padding:16px 12px;background:#fff;border-right:1px solid var(--color-border)}.relation-panel h3{margin:0 8px 10px;font-size:14px}.relation-panel>button{display:flex;justify-content:space-between;width:100%;padding:9px 10px;border:0;border-left:2px solid transparent;background:transparent;text-align:left}.relation-panel>button.active{border-left-color:var(--color-primary);background:var(--color-primary-soft);color:var(--color-primary)}.relation-panel details{margin:8px 4px;color:var(--color-muted);font-size:12px}.minor-item{display:block;width:100%;padding:7px;border:0;background:transparent;text-align:left}.change-index{margin-top:18px;padding-top:14px;border-top:1px solid var(--color-border)}.change-index>small{display:block;padding:0 8px 6px;color:var(--color-faint)}.change-index button{display:flex;gap:8px;width:100%;padding:8px;border:0;background:transparent;text-align:left}.change-index button.active{background:#eef3f8}.change-index i{flex:0 0 7px;width:7px;height:7px;margin-top:5px;border-radius:50%;background:#71889f}.change-index span{display:-webkit-box;overflow:hidden;-webkit-line-clamp:2;-webkit-box-orient:vertical;font-size:12px}.baseline-document{padding:30px clamp(28px,5vw,72px);background:#eef0f3}.baseline-document>section{max-width:840px;margin:0 auto;padding:42px 56px;background:#fff;border-inline:1px solid var(--color-border)}.baseline-document>section:first-of-type{padding-top:54px;border-top:1px solid var(--color-border)}.baseline-document>section:last-of-type{padding-bottom:70px;border-bottom:1px solid var(--color-border)}.baseline-document h2{margin:0 0 20px;font-size:20px}.baseline-document p{margin:0 0 14px;line-height:1.9;text-align:justify}.document-note{display:flex;justify-content:space-between;max-width:840px;margin:0 auto 10px;color:var(--color-muted)}.document-note small{font-size:11px}.report-sentence{padding:2px 1px;border-bottom:2px solid transparent;cursor:default;transition:background var(--motion-fast)}.report-sentence.affected{cursor:pointer;background:#edf5fb;border-bottom-color:#7399b8}.report-sentence.conflict,.report-sentence.weakening{background:#faece9;border-bottom-color:#b7685a}.report-sentence.update{background:#fff3dd;border-bottom-color:#b7833b}.report-sentence.corroboration{background:#eaf5ee;border-bottom-color:#5e9873}.report-sentence.selected{outline:2px solid rgba(36,91,158,.28);outline-offset:1px}.report-sentence sup{margin-left:2px;color:var(--color-primary);font-size:9px}.evidence-panel{padding:20px;background:#fff;border-left:1px solid var(--color-border)}.evidence-head{display:flex;align-items:center;justify-content:space-between}.evidence-head small{color:var(--color-muted)}.relation-badge{padding:3px 7px;border-radius:3px;background:#e9f1f8;color:#315f88;font-size:12px}.relation-badge.conflict,.relation-badge.weakening{background:#f8e7e4;color:#94483d}.relation-badge.update{background:#fff0d5;color:#855d20}.relation-badge.corroboration{background:#e5f3ea;color:#39714e}.evidence-panel h3{margin:14px 0 8px;font-size:16px;line-height:1.5}.rationale{color:var(--color-muted);line-height:1.6}.fact-block{margin-top:14px;padding:12px;border-left:2px solid #8b9aaa;background:#f7f8fa}.fact-block.current{border-left-color:#4779a7;background:#f1f6fa}.fact-block small{color:var(--color-muted)}.fact-block p{margin:5px 0 0;line-height:1.6}.source-list{margin-top:20px}.source-list h4{margin:0 0 8px}.source-list blockquote{margin:0 0 10px;padding:11px 12px;border:1px solid var(--color-border);background:#fbfcfd}.source-list blockquote b,.source-list blockquote small{font-size:11px;color:var(--color-muted)}.source-list blockquote p{margin:7px 0;line-height:1.55}.review-actions{position:sticky;bottom:-20px;display:flex;gap:6px;margin:20px -20px -20px;padding:14px 20px;background:#fff;border-top:1px solid var(--color-border)}.notice{margin:20px;padding:18px;background:#fff;border:1px solid var(--color-border)}.handoff{position:absolute;right:24px;bottom:18px}.error-text{position:absolute;right:24px;bottom:12px}@media(max-width:1180px){.review-layout{grid-template-columns:160px minmax(420px,1fr) 300px}.baseline-document{padding-inline:20px}.baseline-document>section{padding-inline:38px}}@media(max-width:900px){.comparison-workspace{inset:var(--header-height) 0 0}.comparison-shell.with-runs{grid-template-columns:1fr}.run-panel{display:none}.review-layout{grid-template-columns:1fr}.relation-panel{display:none}.evidence-panel{position:fixed;inset:auto 0 0;z-index:3;max-height:48vh;box-shadow:var(--shadow-drawer)}.summary-strip{display:none}}
.comparison-workspace.embedded{height:min(900px,calc(100vh - 210px));min-height:640px}.comparison-workspace.embedded .comparison-shell,.comparison-workspace.embedded .result-workspace{height:100%;min-height:0}.result-workspace{display:grid;grid-template-rows:auto minmax(0,1fr)}.review-layout{height:auto;min-height:0}.content-workspace{display:grid;grid-template-rows:45px minmax(0,1fr);min-width:0;min-height:0;overflow:hidden;background:#eef0f3}.content-switch{display:flex;align-items:end;gap:4px;padding:0 18px;background:#fff;border-bottom:1px solid var(--color-border)}.content-switch button{height:45px;padding:0 14px;border:0;border-bottom:2px solid transparent;background:transparent;color:var(--color-muted)}.content-switch button.active{border-bottom-color:var(--color-primary);color:var(--color-primary);font-weight:650}.content-switch b{margin-left:5px;font-size:11px}.content-workspace>.baseline-document,.finding-document{min-height:0;overflow-y:auto}.finding-document{padding:28px clamp(24px,4vw,60px);background:#eef0f3}.finding-row{display:grid;grid-template-columns:auto minmax(0,1fr);gap:14px;width:min(860px,100%);margin:0 auto 10px;padding:16px 18px;border:1px solid var(--color-border);background:#fff;text-align:left}.finding-row:hover,.finding-row.selected{border-color:#9db4c8;background:#f8fbfd}.finding-row>span{align-self:start}.finding-row b{line-height:1.5}.finding-row p{margin:6px 0;color:var(--color-text);line-height:1.65}.finding-row small{color:var(--color-muted)}.relation-panel,.evidence-panel{overscroll-behavior:contain}.baseline-document,.finding-document,.relation-panel,.evidence-panel{scrollbar-gutter:stable}
</style>
