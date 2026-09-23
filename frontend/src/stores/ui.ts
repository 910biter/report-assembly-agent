import { defineStore } from "pinia";

export const useUiStore = defineStore("ui", {
  state: () => ({
    theme: (typeof localStorage !== "undefined" && localStorage.getItem("ira-theme") === "light" ? "light" : "dark") as "dark" | "light",
    palette: (typeof localStorage !== "undefined" && ["violet", "steel", "graphite"].includes(localStorage.getItem("ira-palette") || "") ? localStorage.getItem("ira-palette") : "violet") as "violet" | "steel" | "graphite",
    navCollapsed: typeof localStorage !== "undefined" && localStorage.getItem("ira-nav-collapsed") === "true",
    navOpen: false,
    createOpen: false,
    draftId: "",
    taskDraft: {
      theme: "",
      requirements: "",
      workflowMode: "automatic",
      templateId: "",
      template: null as Record<string, any> | null,
      materials: [] as Array<Record<string, any>>,
    },
    assistant: {
      open: false,
      tab: "progress" as "progress" | "artifacts" | "discuss",
      focus: null as Record<string, any> | null,
      revision: 0,
      dockWidth: 520,
    },
  }),
  actions: {
    setTheme(theme: "dark" | "light") {
      this.theme = theme;
      if (typeof document !== "undefined") document.documentElement.dataset.theme = theme;
      if (typeof localStorage !== "undefined") localStorage.setItem("ira-theme", theme);
    },
    toggleTheme() { this.setTheme(this.theme === "dark" ? "light" : "dark"); },
    setPalette(palette: "violet" | "steel" | "graphite") {
      this.palette = palette;
      if (typeof document !== "undefined") document.documentElement.dataset.palette = palette;
      if (typeof localStorage !== "undefined") localStorage.setItem("ira-palette", palette);
    },
    toggleNav() { this.navOpen = !this.navOpen; },
    toggleNavCollapsed() {
      this.navCollapsed = !this.navCollapsed;
      if (typeof localStorage !== "undefined") localStorage.setItem("ira-nav-collapsed", String(this.navCollapsed));
    },
    closeNav() { this.navOpen = false; },
    ensureDraftId() {
      if (!this.draftId) {
        const generated = globalThis.crypto?.randomUUID?.()
          || `draft-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        this.draftId = sessionStorage.getItem("ira-draft-id") || generated;
        sessionStorage.setItem("ira-draft-id", this.draftId);
      }
      return this.draftId;
    },
    applyDraftProposal(after: Record<string, unknown>) {
      if (String(after.theme || "").trim()) this.taskDraft.theme = String(after.theme).trim();
      const requirements = after.requirements ?? after.content;
      if (String(requirements || "").trim()) this.taskDraft.requirements = String(requirements).trim();
    },
    openAssistant(focus: Record<string, any> = {}) {
      this.assistant.focus = focus;
      this.assistant.tab = "discuss";
      this.assistant.open = true;
      this.assistant.revision += 1;
    },
    closeAssistant() { this.assistant.open = false; },
    setAssistantTab(tab: "progress" | "artifacts" | "discuss") { this.assistant.tab = tab; },
    setAssistantDockWidth(width: number) { this.assistant.dockWidth = width; },
  },
});
