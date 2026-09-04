import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";
import vue from "@vitejs/plugin-vue";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  base: "/ui-assets/",
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: "../web/spa",
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
