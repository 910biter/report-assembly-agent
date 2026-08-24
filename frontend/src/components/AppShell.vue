<script setup lang="ts">
import { computed } from "vue";
import { useRoute, RouterLink } from "vue-router";
import { useUiStore } from "@/stores/ui";
import AppIcon from "./AppIcon.vue";

const route = useRoute();
const ui = useUiStore();
const editor = computed(() => Boolean(route.meta.editor));
const nav = [
  { to: "/", label: "工作台", icon: "home" },
  { to: "/tasks", label: "任务", icon: "tasks" },
  { to: "/materials", label: "材料库", icon: "files" },
  { to: "/style", label: "模板中心", icon: "template" },
  { to: "/settings", label: "系统设置", icon: "settings" },
];
</script>
<template>
  <div class="app-shell" :class="{ 'is-editor': editor }">
    <aside class="sidebar" :class="{ open: ui.navOpen }">
      <div class="brand"><span class="brand-mark">R</span><div><strong>报告整编</strong><small>Research Workbench</small></div></div>
      <nav>
        <RouterLink v-for="item in nav" :key="item.to" :to="item.to" :class="{ active: route.path === item.to || (item.to === '/tasks' && route.path.startsWith('/tasks/')) }" @click="ui.closeNav()">
          <AppIcon :name="item.icon" /> <span>{{ item.label }}</span>
        </RouterLink>
      </nav>
      <div class="nav-foot"><span class="health-dot"></span> 本地智能整编系统</div>
    </aside>
    <div class="shell-main">
      <header class="topbar">
        <button class="nav-toggle" aria-label="打开导航" @click="ui.toggleNav()"><AppIcon name="menu" /></button>
        <strong>{{ route.meta.title }}</strong>
      </header>
      <main class="content" :class="{ wide: route.meta.wide, editor }"><slot /></main>
    </div>
    <button v-if="ui.navOpen" class="nav-scrim" aria-label="关闭导航" @click="ui.closeNav()"></button>
  </div>
</template>
<style scoped>
.app-shell { min-height: 100vh; }
.sidebar { position: fixed; inset: 0 auto 0 0; z-index: 30; width: var(--nav-width); background: #fafbfc; border-right: 1px solid var(--color-border); }
.brand { height: var(--header-height); display: flex; align-items: center; gap: 11px; padding: 0 20px; border-bottom: 1px solid var(--color-border); }
.brand-mark { display: grid; place-items: center; width: 29px; height: 29px; color: white; background: #274f7d; border-radius: 5px; font-family: Georgia, serif; font-size: 17px; }
.brand strong, .brand small { display: block; line-height: 1.35; }.brand small { color: var(--color-faint); font-size: 9px; letter-spacing: .08em; text-transform: uppercase; }
nav { display: grid; gap: 3px; padding: 18px 12px; }
nav a { position: relative; display: flex; align-items: center; gap: 12px; min-height: 42px; padding: 0 12px; color: #56606c; border-radius: 5px; }
nav a:hover { background: #f0f2f5; } nav a.active { color: var(--color-primary); background: var(--color-primary-soft); font-weight: 650; }
nav a.active::before { content: ""; position: absolute; left: -12px; top: 9px; bottom: 9px; width: 3px; background: var(--color-primary); }
.nav-foot { position: absolute; bottom: 18px; left: 24px; color: var(--color-faint); font-size: 11px; }.health-dot { display: inline-block; width: 6px; height: 6px; margin-right: 5px; border-radius: 50%; background: #49966d; }
.shell-main { min-height: 100vh; margin-left: var(--nav-width); }.topbar { position: sticky; top: 0; z-index: 20; height: var(--header-height); display: flex; align-items: center; gap: 12px; padding: 0 28px; background: rgba(255,255,255,.94); border-bottom: 1px solid var(--color-border); backdrop-filter: blur(10px); }.topbar strong { font-size: 15px; }.nav-toggle { display: none; border: 0; background: transparent; }
.content { width: min(calc(100% - 48px), var(--content-max)); margin: 0 auto; padding: 28px 0 48px; }.content.editor { width: 100%; max-width: none; padding: 0; }.nav-scrim { display: none; }
@media (max-width: 1024px) { .sidebar { transform: translateX(-100%); transition: transform var(--motion-fast); box-shadow: var(--shadow-float); }.sidebar.open { transform: translateX(0); }.shell-main { margin-left: 0; }.nav-toggle { display: grid; place-items: center; }.nav-scrim { display: block; position: fixed; inset: 0; z-index: 25; border: 0; background: rgba(23,29,37,.25); } }
@media (max-width: 600px) { .topbar { padding: 0 16px; }.content { width: calc(100% - 28px); } }
</style>
