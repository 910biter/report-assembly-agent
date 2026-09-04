<script setup lang="ts">
withDefaults(
  defineProps<{ width?: string; height?: string; rounded?: boolean }>(),
  { width: "100%", height: "14px", rounded: true },
);
</script>

<template>
  <span
    class="ui-skeleton"
    :class="{ rounded }"
    :style="{ width, height }"
    aria-hidden="true"
  />
</template>

<style scoped>
.ui-skeleton {
  display: block;
  background: linear-gradient(
    90deg,
    var(--muted) 15%,
    var(--surface-hover) 50%,
    var(--muted) 85%
  );
  background-size: 220% 100%;
  animation: ui-shimmer 1.4s ease-in-out infinite;
}
.rounded {
  border-radius: 99px;
}
@keyframes ui-shimmer {
  to {
    background-position: -220% 0;
  }
}
@media (prefers-reduced-motion: reduce) {
  .ui-skeleton {
    animation: none;
  }
}
</style>
