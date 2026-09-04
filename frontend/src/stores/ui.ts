import { defineStore } from "pinia";

export const useUiStore = defineStore("ui", {
  state: () => ({
    navOpen: false,
    createOpen: false,
    draftId: "",
    taskDraft: { theme: "", requirements: "" },
    assistant: {
      open: false,
      tab: "progress" as "progress" | "artifacts" | "discuss",
      focus: null as Record<string, any> | null,
      revision: 0,
    },
  }),
  actions: {
    toggleNav() { this.navOpen = !this.navOpen; },
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
  },
});
