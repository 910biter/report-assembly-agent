<script setup lang="ts">
import { ref, toRefs } from "vue";
import { api } from "@/api/http";
import UiButton from "@/components/ui/UiButton.vue";
type SourcedText = { material_id: number; filename: string; text: string };
type SourceItem = {
  unit_id: number; material_id: number; filename: string; page?: number | null;
  paragraph?: number | null; excerpt: string; kind: string;
};
type Brief = {
  focus?: string;
  topic_candidates?: string[];
  findings?: SourcedText[];
  open_questions?: SourcedText[];
  coverage?: {
    selected?: number;
    understood?: number;
    not_understood?: number;
    limited_context?: number;
    unknown_context?: number;
  };
};
type SourceState = { open: boolean; loading: boolean; error?: string; items?: SourceItem[] };
const props = withDefaults(defineProps<{ brief: Brief; taskId: string; embedded?: boolean; browse?: boolean }>(), {
  embedded: false,
  browse: false,
});
const { brief, embedded, browse } = toRefs(props);
defineEmits<{ discuss: []; browse: [] }>();
const sourceStates = ref<Record<string, SourceState>>({});

function sourceKey(item: SourcedText, kind: string, index: number) {
  return `${kind}:${item.material_id}:${index}`;
}

async function toggleSources(item: SourcedText, kind: string, index: number) {
  const key = sourceKey(item, kind, index);
  const existing = sourceStates.value[key];
  if (existing?.open) {
    sourceStates.value = { ...sourceStates.value, [key]: { ...existing, open: false } };
    return;
  }
  if (existing?.items) {
    sourceStates.value = { ...sourceStates.value, [key]: { ...existing, open: true } };
    return;
  }
  sourceStates.value = { ...sourceStates.value, [key]: { open: true, loading: true } };
  const params = new URLSearchParams({ query: item.text, material_id: String(item.material_id), limit: "5" });
  try {
    const result = await api<{ items: SourceItem[]; scope?: string }>(
      `/api/tasks/${encodeURIComponent(props.taskId)}/research-brief/sources?${params}`,
    );
    sourceStates.value = { ...sourceStates.value, [key]: { open: true, loading: false, items: result.items || [] } };
  } catch (error) {
    sourceStates.value = { ...sourceStates.value, [key]: {
      open: true, loading: false, error: error instanceof Error ? error.message : "原文检索失败",
    } };
  }
}
</script>

<template>
  <section class="research-brief" :class="{ embedded }" aria-label="材料研究概览">
    <header class="brief-header">
      <div>
        <span class="brief-eyebrow">材料阅读</span>
        <h2>研究概览</h2>
      </div>
      <span class="brief-coverage">
        已理解 {{ brief.coverage?.understood || 0 }} / {{ brief.coverage?.selected || 0 }} 份
      </span>
    </header>
    <div class="brief-body">
      <div class="brief-main">
        <div class="brief-focus">
          <span>当前研究焦点</span>
          <h3>{{ brief.focus || brief.topic_candidates?.[0] || "尚未确定，先从材料内容讨论" }}</h3>
          <p v-if="!brief.focus && brief.topic_candidates?.length">依据已理解材料的主题形成候选方向，尚未确定报告主题。</p>
        </div>
        <div v-if="brief.findings?.length" class="brief-findings">
          <h3>材料理解线索</h3>
          <ol>
            <li v-for="(item, index) in brief.findings.slice(0, 5)" :key="`${item.material_id}-${index}`">
              <span class="brief-index">{{ String(index + 1).padStart(2, "0") }}</span>
              <div class="brief-item-body">
                <p>{{ item.text }}</p><small>{{ item.filename }}</small>
                <button class="source-trigger" type="button" @click="toggleSources(item, 'finding', index)">
                  {{ sourceStates[sourceKey(item, 'finding', index)]?.open ? "收起原文" : "定位原文" }}
                </button>
                <div v-if="sourceStates[sourceKey(item, 'finding', index)]?.open" class="source-results">
                  <p class="source-disclaimer">检索到的候选原文片段，仅供回到材料核对，不代表线索已被证实。</p>
                  <p v-if="sourceStates[sourceKey(item, 'finding', index)]?.loading">正在查找材料原文…</p>
                  <p v-else-if="sourceStates[sourceKey(item, 'finding', index)]?.error">{{ sourceStates[sourceKey(item, 'finding', index)]?.error }}</p>
                  <p v-else-if="!sourceStates[sourceKey(item, 'finding', index)]?.items?.length">当前材料中没有找到匹配片段；可与助手讨论后换一种表述检索。</p>
                  <article v-for="source in sourceStates[sourceKey(item, 'finding', index)]?.items || []" :key="source.unit_id">
                    <small>{{ source.filename }}<template v-if="source.page"> · 第 {{ source.page }} 页</template><template v-if="source.paragraph"> · 第 {{ source.paragraph }} 段</template> · 单元 {{ source.unit_id }}</small>
                    <p>{{ source.excerpt }}</p>
                  </article>
                </div>
              </div>
            </li>
          </ol>
        </div>
        <p v-else class="brief-empty">材料理解结果尚未形成。</p>
      </div>
      <aside class="brief-side">
        <h3>下一步值得核查</h3>
        <ul v-if="brief.open_questions?.length">
          <li v-for="(item, index) in brief.open_questions.slice(0, 4)" :key="`${item.material_id}-${index}`">
            <span>{{ item.text }}</span><small>{{ item.filename }}</small>
            <button class="source-trigger" type="button" @click="toggleSources(item, 'question', index)">
              {{ sourceStates[sourceKey(item, 'question', index)]?.open ? "收起原文" : "定位原文" }}
            </button>
            <div v-if="sourceStates[sourceKey(item, 'question', index)]?.open" class="source-results">
              <p class="source-disclaimer">以下是可供核对的候选原文，不是对缺失信息的确定回答。</p>
              <p v-if="sourceStates[sourceKey(item, 'question', index)]?.loading">正在查找材料原文…</p>
              <p v-else-if="sourceStates[sourceKey(item, 'question', index)]?.error">{{ sourceStates[sourceKey(item, 'question', index)]?.error }}</p>
              <p v-else-if="!sourceStates[sourceKey(item, 'question', index)]?.items?.length">当前材料中没有找到匹配片段；可与助手讨论后换一种表述检索。</p>
              <article v-for="source in sourceStates[sourceKey(item, 'question', index)]?.items || []" :key="source.unit_id">
                <small>{{ source.filename }}<template v-if="source.page"> · 第 {{ source.page }} 页</template><template v-if="source.paragraph"> · 第 {{ source.paragraph }} 段</template> · 单元 {{ source.unit_id }}</small>
                <p>{{ source.excerpt }}</p>
              </article>
            </div>
          </li>
        </ul>
        <p v-else>可以与助手讨论重点，再按问题读取原文。</p>
        <div class="brief-boundary">
          <strong>阅读边界</strong>
          <p v-if="brief.coverage?.not_understood">{{ brief.coverage.not_understood }} 份材料尚无理解结果。</p>
          <p v-if="brief.coverage?.limited_context">{{ brief.coverage.limited_context }} 份材料的理解输入未覆盖全部单元。</p>
          <p v-if="brief.coverage?.unknown_context">{{ brief.coverage.unknown_context }} 份历史结果未记录阅读范围。</p>
          <p>以上是理解线索，不代表事实已逐条核验；具体结论请查原文与证据。</p>
        </div>
        <div class="brief-actions">
          <UiButton variant="outline" @click="$emit('discuss')">与助手讨论</UiButton>
          <UiButton v-if="browse" variant="outline" @click="$emit('browse')">查看材料</UiButton>
        </div>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.research-brief { border: 1px solid var(--border); border-radius: var(--radius-panel, 8px); background: var(--card); overflow: hidden; }
.research-brief.embedded { margin-top: 20px; border-radius: 0; border-inline: 0; background: transparent; }
.brief-header { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 19px 22px 17px; border-bottom: 1px solid var(--border); }
.brief-eyebrow { color: var(--subtle-foreground); font-size: 11px; letter-spacing: .08em; }
.brief-header h2 { margin: 3px 0 0; color: var(--foreground); font-size: 17px; font-weight: 600; }
.brief-coverage { color: var(--muted-foreground); font-size: 12px; white-space: nowrap; }
.brief-body { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(220px, .85fr); }
.brief-main { min-width: 0; padding: 22px; }
.brief-side { min-width: 0; padding: 22px; border-left: 1px solid var(--border); background: var(--muted); }
.brief-focus span { color: var(--muted-foreground); font-size: 12px; }
.brief-focus h3 { margin: 7px 0 0; color: var(--foreground); font-size: 20px; line-height: 1.4; font-weight: 600; overflow-wrap: anywhere; }
.brief-focus p, .brief-empty, .brief-side > p { margin: 9px 0 0; color: var(--muted-foreground); font-size: 13px; line-height: 1.6; }
.brief-findings { margin-top: 24px; padding-top: 18px; border-top: 1px solid var(--border); }
.brief-findings h3, .brief-side h3 { margin: 0 0 13px; color: var(--foreground); font-size: 14px; font-weight: 600; }
.brief-findings ol, .brief-side ul { margin: 0; padding: 0; list-style: none; }
.brief-findings li { display: grid; grid-template-columns: 24px minmax(0, 1fr); gap: 9px; padding: 8px 0; }
.brief-item-body { min-width: 0; }
.brief-index { color: var(--subtle-foreground); font-size: 11px; padding-top: 2px; }
.brief-findings p { margin: 0; color: var(--foreground); font-size: 13px; line-height: 1.55; overflow-wrap: anywhere; }
.brief-findings small, .brief-side li small { display: block; margin-top: 4px; color: var(--muted-foreground); font-size: 11px; overflow-wrap: anywhere; }
.brief-side li { padding: 9px 0; border-bottom: 1px solid var(--border); color: var(--foreground); font-size: 12px; line-height: 1.55; overflow-wrap: anywhere; }
.source-trigger { display: inline-flex; margin-top: 5px; padding: 0; border: 0; background: transparent; color: var(--link, var(--foreground)); font: inherit; font-size: 11px; cursor: pointer; }
.source-trigger:hover { text-decoration: underline; }
.source-results { margin-top: 9px; padding: 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--background); }
.source-results > p { margin: 5px 0; color: var(--muted-foreground); font-size: 11px; line-height: 1.5; }
.source-results .source-disclaimer { color: var(--warning-foreground, var(--muted-foreground)); }
.source-results article { padding: 8px 0; border-top: 1px solid var(--border); }
.source-results article small { color: var(--muted-foreground); font-size: 10px; }
.source-results article p { margin: 5px 0 0; color: var(--foreground); font-size: 12px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; }
.brief-boundary { margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--border); }
.brief-boundary strong { color: var(--foreground); font-size: 12px; }
.brief-boundary p { margin: 7px 0 0; color: var(--muted-foreground); font-size: 12px; line-height: 1.5; }
.brief-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px; }
@media (max-width: 740px) { .brief-body { grid-template-columns: minmax(0, 1fr); } .brief-side { border-left: 0; border-top: 1px solid var(--border); } .brief-header, .brief-main, .brief-side { padding: 16px; } }
</style>
