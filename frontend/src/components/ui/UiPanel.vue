<script setup lang="ts">
withDefaults(
  defineProps<{ variant?: "default" | "raised" | "inset"; padded?: boolean }>(),
  { variant: "default", padded: true },
);
</script>

<template>
  <section
    :class="[
      'ui-panel',
      `ui-panel--${variant}`,
      { 'ui-panel--padded': padded },
    ]"
  >
    <slot />
  </section>
</template>

<style scoped>
.ui-panel {
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--card);
  box-shadow: var(--shadow-panel);
}
.ui-panel--padded {
  padding: var(--space-6);
}
.ui-panel--raised {
  box-shadow: var(--shadow-float);
}
.ui-panel--inset {
  background: var(--surface-raised);
  border-color: var(--border);
}
</style>
