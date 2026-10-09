import { ref, watch } from "vue";
import { authMode, currentUser } from "./auth.ts";

const pageSizes = new Set([5, 10, 20, 50]);

export function usePersistentPageSize(list: string) {
  const key = () => `ira-page-size:${authMode.value === "on" ? currentUser.value?.id ?? "guest" : "local"}:${list}`;
  const endpoint = `/api/auth/preferences/${encodeURIComponent(`page_size:${list}`)}`;
  let initial = 20;
  try {
    const saved = Number(localStorage.getItem(key()));
    if (pageSizes.has(saved)) initial = saved;
  } catch {
    // Browser storage may be unavailable; pagination still works in memory.
  }

  const pageSize = ref(initial);
  let restoring = false;
  let requestId = 0;
  watch(pageSize, (value) => {
    if (!pageSizes.has(value)) return;
    try {
      localStorage.setItem(key(), String(value));
    } catch {
      // Keep the current selection even if the browser cannot persist it.
    }
    if (restoring || authMode.value !== "on" || !currentUser.value) return;
    void fetch(endpoint, {
      method: "PUT", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    }).catch(() => { /* The local choice remains usable during a network interruption. */ });
  }, { flush: "sync" });
  watch(() => currentUser.value?.id, async (userId) => {
    const currentRequest = ++requestId;
    restoring = true;
    try {
      const saved = Number(localStorage.getItem(key()));
      pageSize.value = pageSizes.has(saved) ? saved : 20;
    } catch {
      pageSize.value = 20;
    }
    restoring = false;
    if (authMode.value !== "on" || !userId) return;
    try {
      const response = await fetch(endpoint, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok || currentRequest !== requestId || currentUser.value?.id !== userId) return;
      const value = Number(((await response.json()) as { value: number }).value);
      restoring = true;
      pageSize.value = pageSizes.has(value) ? value : 20;
      restoring = false;
    } catch {
      // Keep the cached selection until the account preference can be loaded.
    }
  }, { immediate: true });
  return pageSize;
}
