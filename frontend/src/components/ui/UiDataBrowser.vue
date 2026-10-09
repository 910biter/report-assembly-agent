<script setup lang="ts">
withDefaults(defineProps<{
  detailOpen?: boolean;
  detailWidth?: string;
  label?: string;
}>(), { detailOpen: false, detailWidth: "340px" });
</script>

<template>
  <section
    class="ui-data-browser"
    :class="{ 'ui-data-browser--detail-open': detailOpen }"
    :style="{ '--ui-detail-width': detailWidth }"
    :aria-label="label"
  >
    <div class="ui-data-browser__main"><slot /></div>
    <aside
      v-if="detailOpen && $slots.detail"
      class="ui-data-browser__detail"
    >
      <slot name="detail" />
    </aside>
  </section>
</template>

<style scoped>
.ui-data-browser {
  display: grid;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  min-height: 520px;
  overflow: clip;
  grid-template-columns: minmax(0, 1fr);
  border: 1px solid var(--border);
  border-radius: var(--radius-panel);
  background: var(--card);
  align-items: start;
}

.ui-data-browser--detail-open {
  grid-template-columns: minmax(0, 1fr) var(--ui-detail-width);
}

.ui-data-browser__main {
  min-width: 0;
  max-width: 100%;
  overflow-x: hidden;
}

.ui-data-browser__detail {
  min-width: 0;
  position: sticky;
  top: var(--header-height);
  align-self: start;
  max-height: calc(100dvh - var(--header-height));
  overflow-y: auto;
  border-left: 1px solid var(--border);
  background: var(--surface-raised);
  overflow-wrap: anywhere;
  scrollbar-gutter: stable;
}

@media (max-width: 1180px) {
  .ui-data-browser--detail-open {
    grid-template-columns: minmax(0, 1fr);
  }

  .ui-data-browser__detail {
    position: fixed;
    inset: var(--header-height) 0 0 auto;
    max-height: none;
    overflow-y: auto;
    z-index: 40;
    width: min(var(--ui-detail-width), 100%);
    box-shadow: var(--shadow-float);
  }
}

@media (max-width: 640px) {
  .ui-data-browser {
    min-height: 460px;
    border-radius: var(--radius-sm);
  }

  .ui-data-browser__detail {
    width: 100%;
  }
}
</style>
