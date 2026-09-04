<script setup lang="ts">
import AppIcon from "@/components/AppIcon.vue";

defineProps<{
  items: Array<{
    label: string;
    value: string | number;
    icon?: string;
    tone?: "default" | "info" | "warning";
  }>;
}>();
</script>

<template>
  <section class="ui-metric-strip" aria-label="概览指标">
    <article
      v-for="item in items"
      :key="item.label"
      class="ui-metric-strip__item"
      :class="`is-${item.tone || 'default'}`"
    >
      <AppIcon
        v-if="item.icon"
        :name="item.icon"
        :size="16"
        class="ui-metric-strip__icon"
      />
      <div>
        <span>{{ item.label }}</span
        ><strong>{{ item.value }}</strong>
      </div>
    </article>
  </section>
</template>

<style scoped>
.ui-metric-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  overflow: hidden;
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
}
.ui-metric-strip__item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: 96px;
  padding: 16px 20px;
  border-right: 1px solid var(--border);
  transition: background var(--motion-fast);
}
.ui-metric-strip__item:last-child {
  border-right: 0;
}
.ui-metric-strip__item:hover {
  background: var(--surface-hover);
}
.ui-metric-strip__icon {
  display: none;
  color: var(--primary);
  background: transparent;
}
.is-info .ui-metric-strip__icon {
  color: var(--info);
  background: transparent;
}
.is-warning .ui-metric-strip__icon {
  color: var(--warning);
  background: transparent;
}
.ui-metric-strip span,
.ui-metric-strip strong {
  display: block;
}
.ui-metric-strip span {
  color: var(--muted-foreground);
  font-size: 12px;
}
.ui-metric-strip strong {
  margin-top: 2px;
  color: var(--foreground);
  font-size: 27px;
  font-weight: 600;
  letter-spacing: -0.035em;
  line-height: 1.1;
}
@media (max-width: 980px) {
  .ui-metric-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .ui-metric-strip__item:nth-child(2) {
    border-right: 0;
  }
  .ui-metric-strip__item:nth-child(-n + 2) {
    border-bottom: 1px solid var(--border);
  }
}
@media (max-width: 620px) {
  .ui-metric-strip {
    grid-template-columns: 1fr;
  }
  .ui-metric-strip__item,
  .ui-metric-strip__item:nth-child(2) {
    border-right: 0;
    border-bottom: 1px solid var(--border);
  }
  .ui-metric-strip__item:last-child {
    border-bottom: 0;
  }
}
</style>
