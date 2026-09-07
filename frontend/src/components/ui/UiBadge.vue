<script setup lang="ts">
import { computed } from "vue";
import { cn } from "@/lib/utils";

const props = withDefaults(
  defineProps<{
    tone?: "neutral" | "info" | "success" | "warning" | "danger";
    dot?: boolean;
  }>(),
  { tone: "neutral", dot: true },
);

const classes = computed(() =>
  cn("ui-badge", `ui-badge--${props.tone}`, { "ui-badge--dot": props.dot }),
);
</script>

<template>
  <span :class="classes"><slot /></span>
</template>

<style scoped>
.ui-badge {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.4;
  white-space: nowrap;
  border: 1px solid color-mix(in srgb, currentColor 24%, transparent);
}
.ui-badge--dot::before {
  content: "";
  width: 6px;
  height: 6px;
  margin-right: 6px;
  border-radius: 99px;
  background: currentColor;
  opacity: 0.78;
}
.ui-badge--neutral {
  color: var(--muted-foreground);
  background: var(--muted);
}
.ui-badge--info {
  color: var(--info);
  background: var(--info-soft);
}
.ui-badge--success {
  color: var(--foreground);
  background: var(--muted);
  border-color: var(--border);
}
.ui-badge--success::before {
  background: var(--success);
}
.ui-badge--warning {
  color: var(--warning);
  background: var(--warning-soft);
}
.ui-badge--danger {
  color: var(--destructive);
  background: var(--destructive-soft);
}
</style>
