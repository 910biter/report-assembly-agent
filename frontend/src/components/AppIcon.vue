<script setup lang="ts">
import {
  ArrowUpRight,
  CircleHelp,
  Clock3,
  FileText,
  FolderOpen,
  House,
  LayoutTemplate,
  ListTodo,
  Menu,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  PanelsTopLeft,
  Plus,
  Search,
  Settings,
  Sparkles,
  Sun,
  SquareCheckBig,
  X,
} from "lucide-vue-next";
import { computed } from "vue";

const props = withDefaults(defineProps<{ name: string; size?: number }>(), {
  size: 18,
});
const icons = {
  home: House,
  tasks: ListTodo,
  files: FolderOpen,
  template: LayoutTemplate,
  settings: Settings,
  plus: Plus,
  close: X,
  search: Search,
  menu: Menu,
  document: FileText,
  dashboard: PanelsTopLeft,
  activity: Sparkles,
  review: SquareCheckBig,
  clock: Clock3,
  arrow: ArrowUpRight,
  sun: Sun,
  moon: Moon,
  "sidebar-close": PanelLeftClose,
  "sidebar-open": PanelLeftOpen,
};
const icon = computed(
  () => icons[props.name as keyof typeof icons] || CircleHelp,
);
</script>

<template>
  <component :is="icon" :size="size" :stroke-width="1.75" aria-hidden="true" />
</template>
