import { createApp } from "vue";
import { createPinia } from "pinia";
import { VueQueryPlugin, QueryClient } from "@tanstack/vue-query";
import App from "./App.vue";
import router from "./router";
import "./styles/tokens.css";
import "./styles/base.css";

const savedTheme = localStorage.getItem("ira-theme");
document.documentElement.dataset.theme = savedTheme === "light" ? "light" : "dark";
const savedPalette = localStorage.getItem("ira-palette");
const paletteVersion = localStorage.getItem("ira-palette-version");
// The earlier steel default was not an explicit user preference. Migrate it once
// while preserving later manual choices through the version marker.
const palette = paletteVersion === "2" && ["violet", "steel", "graphite"].includes(savedPalette || "")
  ? savedPalette!
  : savedPalette === "graphite"
    ? "graphite"
    : "violet";
localStorage.setItem("ira-palette", palette);
localStorage.setItem("ira-palette-version", "2");
document.documentElement.dataset.palette = palette;

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 15_000, retry: 1, refetchOnWindowFocus: false },
  },
});

window.addEventListener("vite:preloadError", (event: Event) => {
  event.preventDefault();
  // Route-level recovery is implemented in router.ts. This handles preloaded
  // chunks too, where Vite reports the error before Vue Router sees it.
  if (sessionStorage.getItem("ira:preload-reload") !== window.location.href) {
    sessionStorage.setItem("ira:preload-reload", window.location.href);
    window.location.reload();
  } else {
    sessionStorage.removeItem("ira:preload-reload");
  }
});

createApp(App)
  .use(createPinia())
  .use(router)
  .use(VueQueryPlugin, { queryClient })
  .mount("#app");
