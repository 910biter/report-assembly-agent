<script setup lang="ts">
import { computed } from "vue";
import { useRoute, RouterLink } from "vue-router";
import { useUiStore } from "@/stores/ui";
import AppIcon from "./AppIcon.vue";
import WorkflowAssistant from "./WorkflowAssistant.vue";

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
  <div class="app-shell" :class="{ 'is-editor': editor, 'is-nav-collapsed': ui.navCollapsed }">
    <aside class="sidebar" :class="{ open: ui.navOpen }">
      <div class="brand">
        <span class="brand-mark"><AppIcon name="document" :size="17" /></span>
        <strong>报告整编</strong>
        <button
          class="sidebar-toggle"
          type="button"
          :aria-label="ui.navCollapsed ? '展开侧栏' : '收起侧栏'"
          :title="ui.navCollapsed ? '展开侧栏' : '收起侧栏'"
          @click="ui.toggleNavCollapsed()"
        >
          <AppIcon :name="ui.navCollapsed ? 'sidebar-open' : 'sidebar-close'" :size="16" />
        </button>
      </div>
      <nav>
        <RouterLink
          v-for="item in nav"
          :key="item.to"
          :to="item.to"
          :class="{
            active:
              route.path === item.to ||
              (item.to === '/tasks' && route.path.startsWith('/tasks/')),
          }"
          @click="ui.closeNav()"
        >
          <AppIcon :name="item.icon" /> <span>{{ item.label }}</span>
        </RouterLink>
      </nav>
      <div class="nav-foot">
        <span class="health-dot"></span><span>服务运行正常</span>
      </div>
    </aside>
    <div class="shell-main">
      <header class="topbar">
        <button
          class="nav-toggle"
          aria-label="打开导航"
          @click="ui.toggleNav()"
        >
          <AppIcon name="menu" />
        </button>
        <div v-if="route.meta.editor" class="topbar-title">
          <strong>{{ route.meta.title }}</strong
          ><span v-if="route.meta.editor">报告工作区</span>
        </div>
        <button
          class="theme-toggle"
          type="button"
          :aria-label="ui.theme === 'dark' ? '切换亮色主题' : '切换暗色主题'"
          :title="ui.theme === 'dark' ? '切换亮色主题' : '切换暗色主题'"
          @click="ui.toggleTheme()"
        >
          <AppIcon :name="ui.theme === 'dark' ? 'sun' : 'moon'" :size="16" />
        </button>
      </header>
      <main class="content" :class="{ wide: route.meta.wide, editor }">
        <slot />
      </main>
    </div>
    <button
      v-if="ui.navOpen"
      class="nav-scrim"
      aria-label="关闭导航"
      @click="ui.closeNav()"
    ></button>
    <WorkflowAssistant />
  </div>
</template>
<style scoped>
.app-shell {
  min-height: 100vh;
  background: var(--background);
}
.sidebar {
  position: fixed;
  inset: 0 auto 0 0;
  z-index: 30;
  width: var(--nav-width);
  background: var(--nav);
  border-right: 1px solid var(--border);
  box-shadow: none;
  transition: width var(--motion-normal);
}
.brand {
  position: relative;
  height: var(--header-height);
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 0 22px;
  border-bottom: 1px solid var(--border);
}
.sidebar-toggle {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  margin-left: auto;
  color: var(--nav-muted);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  transition: color var(--motion-fast), background var(--motion-fast);
}
.sidebar-toggle:hover {
  color: var(--nav-foreground);
  background: var(--nav-raised);
}
.brand-mark {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  color: var(--primary-foreground);
  background: var(--primary-gradient);
  border-radius: 10px;
}
.brand strong {
  color: var(--nav-foreground);
  font-size: 15px;
  letter-spacing: -0.01em;
}
nav {
  display: grid;
  gap: 5px;
  padding: 22px 12px;
}
nav a {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 43px;
  padding: 0 12px;
  color: var(--nav-muted);
  border-radius: 8px;
  font-size: 13px;
  transition:
    background var(--motion-fast),
    color var(--motion-fast);
}
nav a:hover {
  color: var(--nav-foreground);
  background: var(--nav-raised);
}
nav a.active {
  color: var(--nav-foreground);
  background: var(--nav-raised);
  font-weight: 600;
  box-shadow: inset 0 0 0 1px var(--border);
}
nav a.active::before {
  content: "";
  position: absolute;
  left: -12px;
  top: 10px;
  bottom: 10px;
  width: 3px;
  background: var(--accent);
  border-radius: 0 3px 3px 0;
}
.nav-foot {
  position: absolute;
  bottom: 22px;
  left: 23px;
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--nav-muted);
  font-size: 11px;
}
.health-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--health);
  box-shadow: 0 0 0 3px var(--health-soft);
}
.shell-main {
  min-height: 100vh;
  margin-left: var(--nav-width);
  transition: margin-left var(--motion-normal);
}
.is-nav-collapsed .sidebar {
  width: 72px;
}
.is-nav-collapsed .shell-main {
  margin-left: 72px;
}
.is-nav-collapsed .brand {
  justify-content: center;
  gap: 4px;
  padding: 0 8px;
}
.is-nav-collapsed .brand strong,
.is-nav-collapsed .nav-foot span:last-child,
.is-nav-collapsed nav a span {
  display: none;
}
.is-nav-collapsed .sidebar-toggle {
  position: static;
  width: 24px;
  height: 24px;
  margin: 0;
}
.is-nav-collapsed .brand-mark {
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
}
.is-nav-collapsed nav {
  padding-inline: 10px;
}
.is-nav-collapsed nav a {
  justify-content: center;
  padding: 0;
}
.is-nav-collapsed nav a.active::before {
  left: -10px;
}
.is-nav-collapsed .nav-foot {
  left: 0;
  right: 0;
  justify-content: center;
}
.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  height: var(--header-height);
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 32px;
  background: color-mix(in srgb, var(--background) 82%, transparent);
  border-bottom: 1px solid var(--border);
  box-shadow: none;
  backdrop-filter: blur(16px) saturate(1.04);
}
.topbar-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.topbar strong {
  color: var(--foreground);
  font-size: 14px;
  letter-spacing: -0.01em;
}
.topbar-title span {
  color: var(--subtle-foreground);
  font-size: 12px;
}
.theme-toggle {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  margin-left: auto;
  color: var(--muted-foreground);
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--card);
  transition: color var(--motion-fast), background var(--motion-fast), border-color var(--motion-fast);
}
.theme-toggle:hover {
  color: var(--primary);
  border-color: var(--border-strong);
  background: var(--primary-soft);
}
.nav-toggle {
  display: none;
  border: 0;
  background: transparent;
}
.content {
  width: min(calc(100% - 64px), var(--content-max));
  margin: 0 auto;
  padding: 32px 0 56px;
}
.content.editor {
  width: 100%;
  max-width: none;
  padding: 0;
}
.nav-scrim {
  display: none;
}
@media (max-width: 1024px) {
  .sidebar {
    transform: translateX(-100%);
    transition: transform var(--motion-fast);
    box-shadow: var(--shadow-float);
  }
  .sidebar.open {
    transform: translateX(0);
  }
  .shell-main {
    margin-left: 0;
  }
  .is-nav-collapsed .shell-main {
    margin-left: 0;
  }
  .is-nav-collapsed .sidebar {
    width: var(--nav-width);
  }
  .is-nav-collapsed .brand {
    justify-content: flex-start;
    padding: 0 22px;
  }
  .is-nav-collapsed .brand strong,
  .is-nav-collapsed .nav-foot span:last-child,
  .is-nav-collapsed nav a span {
    display: inline;
  }
  .is-nav-collapsed nav a {
    justify-content: flex-start;
    padding: 0 12px;
  }
  .is-nav-collapsed .nav-foot {
    left: 23px;
    right: auto;
    justify-content: flex-start;
  }
  .nav-toggle {
    display: grid;
    place-items: center;
  }
  .nav-scrim {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 25;
    border: 0;
    background: var(--scrim);
  }
}
@media (max-width: 600px) {
  .topbar {
    padding: 0 16px;
  }
  .content {
    width: calc(100% - 28px);
  }
}
</style>
