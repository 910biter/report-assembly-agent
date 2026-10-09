<script setup lang="ts">
import { computed } from "vue";
import { marked } from "marked";
import DOMPurify from "dompurify";

const props = defineProps<{ content: string }>();
// Model output is untrusted; Markdown must never introduce executable HTML.
const html = computed(() => DOMPurify.sanitize(
  marked.parse(props.content || "", { async: false, breaks: true }),
  { USE_PROFILES: { html: true }, FORBID_TAGS: ["img", "style", "form", "input", "button"] },
));
</script>

<template><div class="ui-markdown" v-html="html" /></template>

<style scoped>
.ui-markdown { min-width: 0; color: var(--foreground); font-size: 14px; line-height: 1.85; overflow-wrap: anywhere; }
.ui-markdown :deep(p) { margin: 0 0 12px; }
.ui-markdown :deep(:last-child) { margin-bottom: 0; }
.ui-markdown :deep(h1), .ui-markdown :deep(h2), .ui-markdown :deep(h3), .ui-markdown :deep(h4) { margin: 20px 0 8px; font-size: 16px; font-weight: 600; line-height: 1.5; }
.ui-markdown :deep(ul), .ui-markdown :deep(ol) { padding-left: 24px; margin: 8px 0 16px; }
.ui-markdown :deep(li) { margin: 6px 0; }
.ui-markdown :deep(strong) { font-weight: 600; }
.ui-markdown :deep(blockquote) { margin: 12px 0; padding: 8px 16px; border-left: 3px solid var(--border-strong); background: var(--muted); color: var(--muted-foreground); }
.ui-markdown :deep(pre) { max-width: 100%; overflow-x: auto; padding: 16px; background: var(--muted); border: 1px solid var(--border); border-radius: var(--radius-control); line-height: 1.6; }
.ui-markdown :deep(code) { font-size: 12px; background: var(--muted); padding: 2px 4px; border-radius: 4px; }
.ui-markdown :deep(pre code) { padding: 0; }
.ui-markdown :deep(table) { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; margin: 16px 0; font-size: 13px; }
.ui-markdown :deep(th), .ui-markdown :deep(td) { border: 1px solid var(--border); padding: 8px 12px; text-align: left; }
.ui-markdown :deep(th) { background: var(--muted); font-weight: 600; }
.ui-markdown :deep(a) { color: var(--foreground); text-decoration: underline; text-underline-offset: 3px; }
.ui-markdown :deep(a:hover) { color: var(--primary); }
.ui-markdown :deep(hr) { border: 0; border-top: 1px solid var(--border); margin: 20px 0; }
</style>
