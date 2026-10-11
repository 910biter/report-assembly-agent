<script setup lang="ts">
defineProps<{ title: string; description?: string }>();
</script>

<template>
  <div class="ui-empty-state">
    <div v-if="$slots.icon" class="ui-empty-state__icon">
      <slot name="icon" />
    </div>
    <strong>{{ title }}</strong>
    <p v-if="description">{{ description }}</p>
    <div v-if="$slots.action" class="ui-empty-state__action">
      <slot name="action" />
    </div>
  </div>
</template>

<style scoped>
.ui-empty-state {
  display: grid;
  place-items: center;
  padding: 46px 28px;
  text-align: center;
}
.ui-empty-state__icon {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  margin-bottom: 12px;
  color: var(--primary);
  background: var(--primary-soft);
  border-radius: 12px;
}
.ui-empty-state strong {
  font-size: 15px;
}
.ui-empty-state p {
  max-width: 360px;
  margin: 5px 0 0;
  color: var(--muted-foreground);
  font-size: 13px;
}
.ui-empty-state__action {
  margin-top: 16px;
}
</style>
