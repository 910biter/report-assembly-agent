export const navigationGroups = [
  {
    label: "工作区",
    items: [
      { to: "/", label: "工作台", icon: "home" },
      { to: "/tasks", label: "任务", icon: "tasks" },
    ],
  },
  {
    label: "资料中心",
    items: [
      { to: "/materials", label: "材料库", icon: "files" },
      { to: "/documents", label: "资料解析", icon: "documents" },
      { to: "/style", label: "模板中心", icon: "template" },
    ],
  },
  {
    label: "系统",
    items: [{ to: "/settings", label: "系统设置", icon: "settings" }],
  },
] as const;
