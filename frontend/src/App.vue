<script setup lang="ts">
import { RouterView, useRoute } from "vue-router";
import AppShell from "@/components/AppShell.vue";
import { onMounted, onUnmounted } from "vue";
import { currentUser } from "@/lib/auth";
import { refreshLicense } from "@/lib/license";
const route = useRoute();
let timer: ReturnType<typeof setInterval>;
onMounted(() => {
  timer = setInterval(async () => {
    if (!currentUser.value || route.meta.public) return;
    try {
      if (!(await refreshLicense()).active) window.dispatchEvent(new Event("ira-license-required"));
    } catch { /* Network errors are surfaced by requests; never infer activation. */ }
  }, 30000);
});
onUnmounted(() => clearInterval(timer));
</script>

<template>
  <RouterView v-if="route.meta.public" />
  <AppShell v-else><RouterView /></AppShell>
</template>
