import { createRouter, createWebHistory } from "vue-router";
import DashboardPage from "@/pages/DashboardPage.vue";
import { authMode, currentUser, ensureSession } from "@/lib/auth";
import { refreshLicense } from "@/lib/license";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", component: () => import("@/pages/LoginPage.vue"), meta: { title: "登录", public: true } },
    { path: "/license", component: () => import("@/pages/LicensePage.vue"), meta: { title: "系统授权", public: true } },
    { path: "/", component: DashboardPage, meta: { title: "工作台" } },
    { path: "/tasks", component: () => import("@/pages/TasksPage.vue"), meta: { title: "任务" } },
    { path: "/tasks/:taskId", component: () => import("@/pages/TaskWorkspacePage.vue"), meta: { title: "任务工作台", wide: true } },
    { path: "/documents", component: () => import("@/pages/DocumentsPage.vue"), meta: { title: "资料解析" } },
    { path: "/materials", component: () => import("@/pages/MaterialsPage.vue"), meta: { title: "材料库" } },
    { path: "/style", component: () => import("@/pages/TemplatesPage.vue"), meta: { title: "模板中心" } },
    { path: "/queue", component: () => import("@/pages/QueuePage.vue"), meta: { title: "运行队列" } },
    { path: "/settings", component: () => import("@/pages/SettingsPage.vue"), meta: { title: "系统设置", admin: true } },
    { path: "/admin/users", component: () => import("@/pages/AdminUsersPage.vue"), meta: { title: "账号管理", admin: true } },
    { path: "/reports", redirect: "/tasks" },
    { path: "/reports/:reportId", component: () => import("@/pages/ReportEditorPage.vue"), meta: { title: "报告编辑", editor: true } },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
  scrollBehavior: () => ({ top: 0 }),
});

router.beforeEach(async (to) => {
  try {
    await ensureSession();
  } catch {
    return to.path === "/login" ? true : { path: "/login", query: { redirect: to.fullPath } };
  }
  if (authMode.value === "off") return to.path === "/login" ? true : "/login";
  if (!currentUser.value && to.path !== "/login") {
    return { path: "/login", query: { redirect: to.fullPath } };
  }
  if (currentUser.value && to.path === "/login") return "/";
  if (currentUser.value && to.path !== "/license") {
    try {
      if (!(await refreshLicense()).active) return "/license";
    } catch { return "/license"; }
  }
  if (to.meta.admin && currentUser.value?.role !== "admin") return "/";
  return true;
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

window.addEventListener("ira-license-required", () => {
  if (router.currentRoute.value.path !== "/license") void router.replace("/license");
});
