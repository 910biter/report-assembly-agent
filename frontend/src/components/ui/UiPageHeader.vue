<script setup lang="ts">
defineProps<{ title: string; description?: string; featured?: boolean }>();
</script>

<template>
  <header
    class="ui-page-header"
    :class="{ 'ui-page-header--featured': featured }"
  >
    <div class="ui-page-header__copy">
      <slot name="eyebrow" />
      <h1>{{ title }}</h1>
      <slot name="meta">
        <p v-if="description">{{ description }}</p>
      </slot>
    </div>
    <div v-if="$slots.actions" class="ui-page-header__actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.ui-page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-6);
  min-height: 64px;
  padding: 4px 0 8px;
  border-bottom: 0;
}
.ui-page-header--featured {
  border-bottom-color: var(--border);
  background: transparent;
}
.ui-page-header h1 {
  margin: 0;
  color: var(--foreground);
  font-size: 25px;
  font-weight: 600;
  letter-spacing: -0.025em;
}
.ui-page-header p {
  margin: 4px 0 0;
  color: var(--muted-foreground);
  font-size: 13px;
}
.ui-page-header__actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: var(--space-2);
}
@media (max-width: 640px) {
  .ui-page-header {
    min-height: 56px;
    padding: 4px 0 8px;
  }
  .ui-page-header h1 {
    font-size: 23px;
  }
  .ui-page-header p {
    display: none;
  }
}
</style>
