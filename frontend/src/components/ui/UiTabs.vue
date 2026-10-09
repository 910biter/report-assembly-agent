<script setup lang="ts">
type TabItem = {
  value: string;
  label: string;
  badge?: string | number;
  disabled?: boolean;
};

defineProps<{
  modelValue: string;
  items: TabItem[];
  ariaLabel?: string;
}>();

const emit = defineEmits<{ "update:modelValue": [value: string] }>();
</script>

<template>
  <nav class="ui-tabs" :aria-label="ariaLabel || '内容切换'">
    <button
      v-for="item in items"
      :key="item.value"
      type="button"
      :class="{ 'ui-tabs__item--active': modelValue === item.value }"
      :disabled="item.disabled"
      @click="emit('update:modelValue', item.value)"
    >
      <span>{{ item.label }}</span>
      <b v-if="item.badge !== undefined">{{ item.badge }}</b>
    </button>
  </nav>
</template>

<style scoped>
.ui-tabs {
  display: flex;
  gap: 4px;
  min-width: 0;
  overflow: auto;
  padding: 4px;
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--muted);
  width: fit-content;
  max-width: 100%;
  scrollbar-width: none;
}

.ui-tabs::-webkit-scrollbar {
  display: none;
}

.ui-tabs button {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: var(--space-1);
  min-height: 36px;
  padding: 0 12px;
  border: 0;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted-foreground);
  font: 500 13px/1 var(--font-ui);
  transition:
    background var(--motion-fast),
    color var(--motion-fast),
    box-shadow var(--motion-fast);
}

.ui-tabs button:hover:not(:disabled) {
  color: var(--foreground);
  background: color-mix(in srgb, var(--surface-hover) 64%, transparent);
}

.ui-tabs__item--active {
  color: var(--foreground);
  background: var(--card);
  border-color: var(--border-strong);
}

.ui-tabs button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.ui-tabs b {
  display: grid;
  min-width: 17px;
  height: 17px;
  place-items: center;
  border-radius: 99px;
  background: var(--surface-hover);
  color: var(--muted-foreground);
  font-size: 10px;
}
</style>
