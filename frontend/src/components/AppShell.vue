<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, RouterLink } from "vue-router";
import { useUiStore } from "@/stores/ui";
import AppIcon from "./AppIcon.vue";
import WorkflowAssistant from "./WorkflowAssistant.vue";
import { navigationGroups } from "@/navigation";
import { authMode, currentUser, signOut } from "@/lib/auth";
import { api, jsonInit } from "@/api/http";
import { usesDockedAssistant } from "@/lib/assistantLayout";

const route = useRoute();
const ui = useUiStore();
const accountOpen = ref(false);
const passwordOpen = ref(false);
const currentPassword = ref("");
const newPassword = ref("");
const confirmPassword = ref("");
const passwordError = ref("");
const savingPassword = ref(false);
async function changePassword() {
  passwordError.value = "";
  if (newPassword.value !== confirmPassword.value) {
    passwordError.value = "两次输入的新密码不一致";
    return;
  }
  if (newPassword.value.length < 12) {
    passwordError.value = "新密码至少需要 12 位";
    return;
  }
  savingPassword.value = true;
  try {
    await api("/api/auth/change-password", jsonInit("POST", {
      current_password: currentPassword.value, new_password: newPassword.value,
    }));
    window.location.assign("/login");
  } catch {
    passwordError.value = "修改失败，请检查当前密码后重试";
  } finally {
    savingPassword.value = false;
  }
}
const editor = computed(() => Boolean(route.meta.editor));
const dockAssistantRoute = computed(
  () => usesDockedAssistant(route.path),
);
const shellStyle = computed(
  () =>
    ({
      "--assistant-dock-width": `min(${ui.assistant.dockWidth}px, 42vw, calc(100vw - ${ui.navCollapsed ? 56 : 176}px - 640px))`,
    }) as Record<string, string>,
);
</script>
<template>
  <div
    class="app-shell"
    :class="{
      'is-editor': editor,
      'is-nav-collapsed': ui.navCollapsed,
      'assistant-docked': ui.assistant.open && dockAssistantRoute,
    }"
    :style="shellStyle"
  >
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
      <nav class="primary-nav" aria-label="主导航">
        <div
          v-for="group in navigationGroups"
          :key="group.label"
          class="nav-group"
          role="group"
          :aria-label="group.label"
        >
          <span class="nav-group-label" aria-hidden="true">{{ group.label }}</span>
          <RouterLink
            v-for="item in group.items"
            v-show="item.to !== '/settings' || authMode === 'off' || currentUser?.role === 'admin'"
            :key="item.to"
            :to="item.to"
            class="nav-link"
            :title="ui.navCollapsed ? item.label : undefined"
            :class="{
              active:
                route.path === item.to ||
                (item.to === '/tasks' && route.path.startsWith('/tasks/')),
            }"
            @click="ui.closeNav()"
          >
            <AppIcon :name="item.icon" /> <span>{{ item.label }}</span>
          </RouterLink>
          <RouterLink
            v-if="group.label === '系统' && currentUser?.role === 'admin'"
            to="/admin/users" class="nav-link" :class="{ active: route.path === '/admin/users' }"
            @click="ui.closeNav()"
          ><AppIcon name="settings" /><span>账号管理</span></RouterLink>
        </div>
      </nav>
      <div class="nav-foot">
        <div class="nav-utilities">
          <div v-if="currentUser" class="account-area">
            <button class="account-trigger" type="button" :aria-expanded="accountOpen" aria-label="账户菜单" @click="accountOpen = !accountOpen">
              <span class="account-avatar">{{ currentUser.username.slice(0, 1).toUpperCase() }}</span>
              <span class="account-name">{{ currentUser.username }}</span>
              <span class="account-caret">⌄</span>
            </button>
            <div v-if="accountOpen" class="account-menu">
              <button type="button" @click="passwordOpen = true; accountOpen = false">修改密码</button>
              <button type="button" @click="signOut">退出登录</button>
            </div>
          </div>
          <button
            class="theme-toggle sidebar-theme-toggle"
            type="button"
            :aria-label="ui.theme === 'dark' ? '切换亮色主题' : '切换暗色主题'"
            :title="ui.theme === 'dark' ? '切换亮色主题' : '切换暗色主题'"
            @click="ui.toggleTheme()"
          >
            <AppIcon :name="ui.theme === 'dark' ? 'sun' : 'moon'" :size="16" />
          </button>
        </div>
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
    <div v-if="passwordOpen" class="account-dialog-backdrop" @click.self="passwordOpen = false">
      <form class="account-dialog" @submit.prevent="changePassword">
        <div class="account-dialog-head"><h2>修改密码</h2><button type="button" aria-label="关闭" @click="passwordOpen = false">×</button></div>
        <label>当前密码<input v-model="currentPassword" type="password" autocomplete="current-password" required /></label>
        <label>新密码<input v-model="newPassword" type="password" autocomplete="new-password" required /></label>
        <label>确认新密码<input v-model="confirmPassword" type="password" autocomplete="new-password" required /></label>
        <p v-if="passwordError" class="password-error" role="alert">{{ passwordError }}</p>
        <div class="account-dialog-actions"><button type="button" @click="passwordOpen = false">取消</button><button type="submit" :disabled="savingPassword">{{ savingPassword ? "正在保存…" : "保存并重新登录" }}</button></div>
      </form>
    </div>
  </div>
</template>
<style scoped>
.nav-utilities { display: flex; align-items: center; gap: 4px; min-width: 0; }
.account-area { position: relative; flex: 1; min-width: 0; }
.sidebar-theme-toggle { flex: 0 0 34px; margin-left: 0; }
.account-trigger { width: 100%; display: flex; align-items: center; gap: 10px; padding: 6px; border: 1px solid transparent; border-radius: 8px; color: var(--foreground); background: transparent; cursor: pointer; text-align: left; }
.account-trigger:hover { border-color: var(--border-strong); background: var(--surface-hover); }
.account-avatar { display: grid; place-items: center; flex: 0 0 28px; height: 28px; border-radius: 6px; background: var(--primary-soft); color: var(--foreground); font-weight: 600; }
.account-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.account-menu { position: absolute; bottom: calc(100% + 6px); left: 0; right: 0; padding: 5px; border: 1px solid var(--border-strong); border-radius: 10px; background: var(--surface); box-shadow: var(--shadow-float); }
.account-menu button { width: 100%; padding: 9px; border: 0; border-radius: 6px; color: var(--foreground); background: transparent; text-align: left; cursor: pointer; }
.account-menu button:hover { background: var(--surface-hover); }
.account-dialog-backdrop { position: fixed; inset: 0; z-index: 100; display: grid; place-items: center; padding: 16px; background: var(--scrim); }
.account-dialog { width: min(100%, 390px); display: grid; gap: 15px; padding: 22px; border: 1px solid var(--border-strong); border-radius: 12px; background: var(--surface); box-shadow: var(--shadow-float); }
.account-dialog-head { display: flex; align-items: center; justify-content: space-between; }
.account-dialog h2 { margin: 0; font-size: 18px; }
.account-dialog-head button { border: 0; background: transparent; color: var(--foreground); font-size: 22px; cursor: pointer; }
.account-dialog label { display: grid; gap: 7px; color: var(--foreground); font-size: 13px; }
.account-dialog input { width: 100%; height: 38px; padding: 0 10px; border: 1px solid var(--border-strong); border-radius: 7px; color: var(--foreground); background: var(--background); }
.account-dialog input:focus { outline: 2px solid var(--primary); outline-offset: 1px; }
.account-dialog-actions { display: flex; justify-content: flex-end; gap: 8px; }
.account-dialog-actions button { padding: 9px 12px; border: 1px solid var(--border-strong); border-radius: 7px; color: var(--foreground); background: var(--surface-hover); cursor: pointer; }
.account-dialog-actions button[type=submit] { background: var(--primary); color: white; }
.password-error { margin: 0; color: var(--destructive); font-size: 12px; }
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
  transition: width var(--motion-normal), border-color var(--motion-normal);
}
.brand {
  position: relative;
  height: 56px;
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 0 12px;
  border-bottom: 1px solid var(--border);
}
.sidebar-toggle {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
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
  width: 30px;
  height: 30px;
  color: var(--nav-foreground);
  background: transparent;
  border: 0;
  border-radius: 8px;
}
.brand strong {
  color: var(--nav-foreground);
  font-size: 14px;
  letter-spacing: -0.01em;
}
nav.primary-nav {
  display: grid;
  gap: 14px;
  padding: 16px 8px;
}
.nav-group {
  display: grid;
  gap: 3px;
}
.nav-group + .nav-group {
  padding-top: 9px;
  border-top: 0;
}
.nav-group-label {
  padding: 0 10px 4px;
  color: var(--subtle-foreground);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0;
  line-height: 1.5;
}
.nav-link {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 34px;
  padding: 0 10px;
  color: var(--nav-muted);
  border-radius: 8px;
  font-size: 13px;
  transition:
    background var(--motion-fast),
    color var(--motion-fast);
}
.nav-link:hover {
  color: var(--nav-foreground);
  background: var(--nav-raised);
}
.nav-link.active {
  color: var(--nav-foreground);
  background: var(--nav-raised);
  font-weight: 500;
  box-shadow: none;
}
.nav-link.active::before {
  content: none;
  position: absolute;
  left: -10px;
  top: 9px;
  bottom: 9px;
  width: 2px;
  background: var(--primary);
  border-radius: 0 2px 2px 0;
}
.nav-foot {
  position: absolute;
  bottom: 16px;
  left: 12px;
  right: 12px;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 7px;
  color: var(--nav-muted);
  font-size: 11px;
}
.shell-main {
  min-height: 100vh;
  min-width: 0;
  overflow-x: clip;
  margin-left: var(--nav-width);
  transition: margin-left var(--motion-normal);
}
@media (min-width: 1280px) {
  .app-shell.assistant-docked .shell-main {
    margin-right: var(--assistant-dock-width);
    transition:
      margin-left var(--motion-normal),
      margin-right var(--motion-normal);
  }
}
.is-nav-collapsed .sidebar {
  width: 56px;
}
.is-nav-collapsed .shell-main {
  margin-left: 56px;
}
.is-nav-collapsed .brand {
  justify-content: center;
  padding: 0;
}
.is-nav-collapsed .brand strong,
.is-nav-collapsed .nav-foot span:last-child,
.is-nav-collapsed nav a span,
.is-nav-collapsed .nav-group-label {
  display: none;
}
.is-nav-collapsed .sidebar-toggle {
  position: absolute;
  top: 15px;
  right: -12px;
  z-index: 1;
  width: 24px;
  height: 24px;
  margin: 0;
  border: 1px solid var(--border-strong);
  border-radius: 50%;
  background: var(--nav);
  box-shadow: 0 3px 10px rgba(0, 0, 0, 0.28);
}
.is-nav-collapsed .brand-mark {
  width: 30px;
  height: 30px;
  flex: 0 0 30px;
}
.is-nav-collapsed .nav-utilities { flex-direction: column; }
.is-nav-collapsed .account-area { flex: none; }
.is-nav-collapsed nav.primary-nav {
  gap: 8px;
  padding-inline: 8px;
}
.is-nav-collapsed .nav-group + .nav-group {
  padding-top: 7px;
}
.is-nav-collapsed nav a {
  justify-content: center;
  padding: 0;
}
.is-nav-collapsed nav a.active::before {
  left: -8px;
}
.is-nav-collapsed .nav-foot {
  left: 0;
  right: 0;
  justify-content: center;
}
.is-nav-collapsed .account-trigger { justify-content: center; padding: 4px; }
.is-nav-collapsed .account-name, .is-nav-collapsed .account-caret, .is-nav-collapsed .health-line { display: none; }
.is-nav-collapsed .account-menu { left: 40px; right: auto; bottom: 0; width: 150px; }
@media (max-width: 1024px) {
  .is-nav-collapsed .account-trigger { justify-content: flex-start; padding: 6px; }
  .is-nav-collapsed .account-name, .is-nav-collapsed .account-caret, .is-nav-collapsed .health-line { display: flex; }
  .is-nav-collapsed .account-menu { left: 0; right: 0; bottom: calc(100% + 6px); width: auto; }
}
.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  height: var(--header-height);
  display: none;
  align-items: center;
  gap: 12px;
  padding: 0 32px;
  background: color-mix(in srgb, var(--background) 92%, transparent);
  border-bottom: 1px solid var(--border);
  box-shadow: none;
  backdrop-filter: blur(10px);
}
.theme-toggle {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  margin-left: auto;
  color: var(--muted-foreground);
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  transition: color var(--motion-fast), background var(--motion-fast), border-color var(--motion-fast);
}
.theme-toggle:hover {
  color: var(--foreground);
  border-color: var(--border);
  background: var(--surface-hover);
}
.nav-toggle {
  display: none;
  border: 0;
  background: transparent;
}
.content {
  min-width: 0;
  max-width: var(--content-max);
  width: min(calc(100% - 64px), var(--content-max));
  margin: 0 auto;
  padding: 28px 0 56px;
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
  .topbar { display: flex; }
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
  .is-nav-collapsed nav a span,
  .is-nav-collapsed .nav-group-label {
    display: inline;
  }
  .is-nav-collapsed nav a {
    justify-content: flex-start;
    padding: 0 12px;
  }
  .is-nav-collapsed .nav-foot {
    left: 18px;
    right: 18px;
    justify-content: flex-start;
  }
  .is-nav-collapsed .nav-utilities { flex-direction: row; }
  .is-nav-collapsed .account-area { flex: 1; }
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
