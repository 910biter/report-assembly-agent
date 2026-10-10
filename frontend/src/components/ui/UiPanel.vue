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
  border-radius: var(--radius-panel);
  background: var(--card);
}
.ui-panel--padded {
  padding: var(--space-5);
}
.ui-panel--raised {
  border-color: var(--border-strong);
  background: var(--surface-raised);
}
.ui-panel--inset {
  background: color-mix(in srgb, var(--surface-raised) 72%, var(--background));
  border-color: var(--border);
}
</style>
