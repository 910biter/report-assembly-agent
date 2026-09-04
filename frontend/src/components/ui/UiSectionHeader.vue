<script setup lang="ts">
defineProps<{ eyebrow?: string; title: string; description?: string }>();
</script>

<template>
  <header class="ui-section-header">
    <div>
      <span v-if="eyebrow" class="ui-section-header__eyebrow">{{
        eyebrow
      }}</span>
      <h2>{{ title }}</h2>
      <p v-if="description">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="ui-section-header__actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.ui-section-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
}
.ui-section-header h2 {
  margin: 2px 0 4px;
  color: var(--foreground);
  font-size: 17px;
  letter-spacing: -0.015em;
}
.ui-section-header p {
  margin: 0;
  color: var(--muted-foreground);
  font-size: 13px;
}
.ui-section-header__eyebrow {
  display: block;
  margin-bottom: 4px;
  color: var(--primary);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}
.ui-section-header__actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: var(--space-2);
}
@media (max-width: 640px) {
  .ui-section-header {
    display: grid;
  }
  .ui-section-header__actions {
    justify-content: flex-start;
  }
}
</style>
