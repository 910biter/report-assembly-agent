import { createRouter, createWebHistory } from "vue-router";
import DashboardPage from "@/pages/DashboardPage.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: DashboardPage, meta: { title: "工作台" } },
    { path: "/tasks", component: () => import("@/pages/TasksPage.vue"), meta: { title: "任务" } },
    { path: "/tasks/:taskId", component: () => import("@/pages/TaskWorkspacePage.vue"), meta: { title: "任务工作台", wide: true } },
    { path: "/materials", component: () => import("@/pages/MaterialsPage.vue"), meta: { title: "材料库" } },
    { path: "/style", component: () => import("@/pages/TemplatesPage.vue"), meta: { title: "模板中心" } },
    { path: "/settings", component: () => import("@/pages/SettingsPage.vue"), meta: { title: "系统设置" } },
    { path: "/reports", redirect: "/tasks" },
    { path: "/reports/:reportId", component: () => import("@/pages/ReportEditorPage.vue"), meta: { title: "报告编辑", editor: true } },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
  scrollBehavior: () => ({ top: 0 }),
});

// Hashed chunks change on every deployment. A tab that has kept an older SPA
// shell open must reload once instead of leaving the user on a broken route.
const CHUNK_RELOAD_KEY = "ira:chunk-reload";
router.onError((error, to) => {
  const message = String(error?.message || error);
  if (!/dynamically imported module|module script|Failed to fetch/i.test(message)) return;
  const target = to.fullPath || window.location.href;
  if (sessionStorage.getItem(CHUNK_RELOAD_KEY) === target) {
    sessionStorage.removeItem(CHUNK_RELOAD_KEY);
    return;
  }
  sessionStorage.setItem(CHUNK_RELOAD_KEY, target);
  window.location.assign(target);
});

export default router;
