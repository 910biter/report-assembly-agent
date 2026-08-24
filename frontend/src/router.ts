import { createRouter, createWebHistory } from "vue-router";
import DashboardPage from "@/pages/DashboardPage.vue";
import TasksPage from "@/pages/TasksPage.vue";
import TaskWorkspacePage from "@/pages/TaskWorkspacePage.vue";
import MaterialsPage from "@/pages/MaterialsPage.vue";
import TemplatesPage from "@/pages/TemplatesPage.vue";
import SettingsPage from "@/pages/SettingsPage.vue";
import ReportEditorPage from "@/pages/ReportEditorPage.vue";

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: DashboardPage, meta: { title: "工作台" } },
    { path: "/tasks", component: TasksPage, meta: { title: "任务" } },
    { path: "/tasks/:taskId", component: TaskWorkspacePage, meta: { title: "任务工作台", wide: true } },
    { path: "/materials", component: MaterialsPage, meta: { title: "材料库" } },
    { path: "/style", component: TemplatesPage, meta: { title: "模板中心" } },
    { path: "/settings", component: SettingsPage, meta: { title: "系统设置" } },
    { path: "/reports", redirect: "/tasks" },
    { path: "/reports/:reportId", component: ReportEditorPage, meta: { title: "报告编辑", editor: true } },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
  scrollBehavior: () => ({ top: 0 }),
});
