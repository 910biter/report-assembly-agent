import { defineStore } from "pinia";

export const useUiStore = defineStore("ui", {
  state: () => ({ navOpen: false, createOpen: false }),
  actions: {
    toggleNav() { this.navOpen = !this.navOpen; },
    closeNav() { this.navOpen = false; },
  },
});
