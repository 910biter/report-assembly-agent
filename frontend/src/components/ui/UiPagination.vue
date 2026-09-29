<script setup lang="ts">
import { computed } from "vue";

const props = withDefaults(defineProps<{
  page: number;
  pageSize: number;
  total: number;
}>(), { pageSize: 20 });
const emit = defineEmits<{
  "update:page": [value: number];
  "update:pageSize": [value: number];
}>();
const pageCount = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)));
const start = computed(() => props.total ? (props.page - 1) * props.pageSize + 1 : 0);
const end = computed(() => Math.min(props.page * props.pageSize, props.total));
</script>

<template>
  <nav class="ui-pagination" aria-label="列表分页">
    <span class="ui-pagination__range">{{ start }}–{{ end }} / {{ total }}</span>
    <label class="ui-pagination__size">每页
      <select :value="pageSize" aria-label="每页条数" @change="emit('update:pageSize', Number(($event.target as HTMLSelectElement).value))">
        <option :value="5">5 条</option>
        <option :value="10">10 条</option>
        <option :value="20">20 条</option>
        <option :value="50">50 条</option>
      </select>
    </label>
    <div class="ui-pagination__controls">
      <button type="button" :disabled="page <= 1" aria-label="上一页" @click="emit('update:page', page - 1)">上一页</button>
      <span>{{ page }} / {{ pageCount }}</span>
      <button type="button" :disabled="page >= pageCount" aria-label="下一页" @click="emit('update:page', page + 1)">下一页</button>
    </div>
  </nav>
</template>

<style scoped>
.ui-pagination { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 16px; padding: 16px 4px; color: var(--muted-foreground); font-size: 13px; }
.ui-pagination__size, .ui-pagination__controls { display: inline-flex; align-items: center; gap: 8px; }
.ui-pagination select, .ui-pagination button { min-height: 32px; padding: 0 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-raised); color: var(--foreground); }
.ui-pagination button:not(:disabled) { cursor: pointer; }
.ui-pagination button:not(:disabled):hover { background: var(--surface-hover); border-color: var(--border-strong); }
.ui-pagination button:disabled { opacity: .45; cursor: not-allowed; }
</style>
