import { createRouter, createWebHistory } from "vue-router";
import DashboardPage from "@/pages/DashboardPage.vue";
export default createRouter({
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
