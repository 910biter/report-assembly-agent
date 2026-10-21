<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRouter } from "vue-router";
import { KeyRound, Copy, Upload, ShieldCheck, LogOut } from "lucide-vue-next";
import UiButton from "@/components/ui/UiButton.vue";
import { currentUser, signOut } from "@/lib/auth";
import { licenseStatus, refreshLicense, licenseMessages } from "@/lib/license";

const router = useRouter();
const admin = computed(() => currentUser.value?.role === "admin");
const token = ref("");
const request = ref("");
const error = ref("");
const message = ref("");
const busy = ref(false);
const checking = ref(true);
const copied = ref(false);
let timer: ReturnType<typeof setInterval> | undefined;
const title = computed(() => licenseStatus.value?.active ? "系统授权"
  : licenseStatus.value?.status === "expired" ? "授权已到期"
  : licenseStatus.value?.status === "unlicensed" ? "激活系统" : "授权需要处理");
const expires = computed(() => licenseStatus.value?.expires_at
  ? new Date(licenseStatus.value.expires_at * 1000).toLocaleString("zh-CN") : "");
const abnormal = computed(() => licenseStatus.value && !licenseStatus.value.active
  && !["expired", "unlicensed"].includes(licenseStatus.value.status));
const clockRecovery = computed(() => licenseStatus.value?.status === "clock_rollback");

async function check() {
  try {
    const wasBlocked = licenseStatus.value && !licenseStatus.value.active;
    await refreshLicense();
    if (wasBlocked && licenseStatus.value?.active && !admin.value) await router.replace("/");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "无法读取授权状态";
  } finally { checking.value = false; }
}

async function loadRequest() {
  const response = await fetch("/api/license/request", { cache: "no-store" });
  const body = await response.json();
  if (!response.ok) throw new Error(licenseMessages[body.detail] || "无法读取安装识别码");
  request.value = body.request;
}
async function copyRequest() {
  try {
    await navigator.clipboard.writeText(request.value);
    copied.value = true;
  } catch {
    // Clipboard API is unavailable on ordinary remote HTTP deployments.
    const input = document.querySelector<HTMLTextAreaElement>("#installation-request");
    input?.focus();
    input?.select();
    copied.value = document.execCommand("copy");
    if (!copied.value) message.value = "已选中识别码，请按 Ctrl+C 复制。";
  }
}
async function importFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (file) {
    if (file.size > 16384) error.value = "许可文件过大，请选择正确的许可证文件。";
    else token.value = (await file.text()).trim();
  }
  input.value = "";
}
async function activate() {
  if (busy.value || !token.value.trim()) return;
  busy.value = true;
  error.value = "";
  try {
    const response = await fetch(clockRecovery.value ? "/api/license/recover-clock" : "/api/license/activate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: token.value.trim() }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(licenseMessages[body.detail] || "激活未完成，请联系授权方。");
    await refreshLicense();
    token.value = "";
    message.value = body.status === "unchanged" ? "许可证已激活，有效期保持不变。" : "激活成功";
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "激活失败";
  } finally { busy.value = false; }
}
async function exportRecovery() {
  try {
    const response = await fetch("/api/license/recovery-request", { cache: "no-store" });
    if (!response.ok) throw new Error("无法生成恢复请求，请联系授权方。");
    const blob = new Blob([JSON.stringify(await response.json(), null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url; link.download = "license-clock-recovery-request.json"; link.click();
    URL.revokeObjectURL(url);
  } catch (cause) { error.value = String(cause); }
}
onMounted(async () => {
  await check();
  if (admin.value) {
    try { await loadRequest(); } catch (cause) { error.value = String(cause); }
  }
  timer = setInterval(check, 30000);
});
onUnmounted(() => clearInterval(timer));
</script>

<template>
  <main class="license-screen">
    <section class="license-frame" aria-labelledby="license-title">
      <header>
        <div class="license-symbol"><ShieldCheck v-if="licenseStatus?.active" :size="24" /><KeyRound v-else :size="24" /></div>
        <p class="product-name">报告整编</p>
        <h1 id="license-title">{{ checking ? "正在检查授权" : title }}</h1>
        <p v-if="admin && licenseStatus?.duration_months" class="expiry">授权期限 {{ licenseStatus.duration_months }} 个月</p>
        <p v-if="admin && licenseStatus?.activated_at" class="helper">激活时间 {{ new Date(licenseStatus.activated_at * 1000).toLocaleString("zh-CN") }}</p>
        <p v-if="expires" class="expiry">有效期至 {{ expires }}</p>
      </header>
      <template v-if="!checking && admin">
        <p v-if="abnormal" class="notice" role="alert">{{ licenseMessages[licenseStatus!.status.toUpperCase()] || "授权状态异常，请联系授权方检查服务器时间或本地授权记录。" }}</p>
        <div v-if="clockRecovery">
          <p class="helper">若服务器曾误设为未来时间，校正后导出恢复请求并交给授权方。恢复不会延长授权期限。</p>
          <UiButton variant="secondary" @click="exportRecovery">导出恢复请求</UiButton>
        </div>
        <form @submit.prevent="activate">
          <div class="field-heading">
            <label for="installation-request">安装识别码</label>
            <UiButton type="button" variant="ghost" size="sm" :disabled="!request" @click="copyRequest"><Copy :size="14" />{{ copied ? "已复制" : "复制" }}</UiButton>
          </div>
          <textarea id="installation-request" :value="request" readonly rows="2" spellcheck="false" aria-describedby="request-help" />
          <p id="request-help" class="helper">将识别码提供给授权方，获取本机许可证。</p>
          <div class="field-heading">
            <label for="license-token">{{ clockRecovery ? "时间恢复凭证" : licenseStatus?.active ? "更换许可证" : "许可证" }}</label>
            <label class="file-import"><Upload :size="14" />导入文件<input type="file" accept=".license,.txt" @change="importFile" /></label>
          </div>
          <textarea id="license-token" v-model="token" rows="4" spellcheck="false" placeholder="粘贴许可证" autocomplete="off" />
          <p v-if="licenseStatus?.active" class="helper">新许可证按签发期限从本次激活起重新计时，剩余期限不累加。</p>
          <p v-if="error" class="error" role="alert">{{ error }}</p>
          <p v-if="message" class="notice" role="status">{{ message }}</p>
          <div class="actions">
            <UiButton v-if="licenseStatus?.active" type="button" variant="secondary" @click="router.replace('/')">进入工作台</UiButton>
            <UiButton type="submit" :loading="busy" :disabled="!token.trim() || (!!abnormal && !clockRecovery)">{{ clockRecovery ? "恢复时间校验" : licenseStatus?.active ? "更换授权" : "激活" }}</UiButton>
          </div>
        </form>
      </template>
      <template v-else-if="!checking">
        <p class="notice">系统{{ licenseStatus?.status === "expired" ? "授权已到期" : "尚未获得有效授权" }}，请联系管理员完成授权。</p>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <UiButton variant="secondary" @click="check">重新检查</UiButton>
      </template>
      <footer><span>{{ currentUser?.username }}</span><UiButton variant="ghost" size="sm" @click="signOut"><LogOut :size="14" />退出登录</UiButton></footer>
    </section>
  </main>
</template>

<style scoped>
.license-screen { min-height: 100dvh; display: grid; place-items: center; padding: 24px; background: var(--background); color: var(--foreground); }
.license-frame { width: min(100%, 540px); padding: clamp(20px, 4vw, 36px); background: var(--card); border: 1px solid var(--border-strong); border-radius: var(--radius-panel); }
.license-symbol { display: grid; place-items: center; width: 44px; height: 44px; background: var(--primary-soft); color: var(--primary); border-radius: var(--radius-control); }
.product-name { margin: 20px 0 6px; font-size: 12px; color: var(--muted-foreground); }
h1 { margin: 0; font-size: 28px; font-weight: 600; }
.expiry { color: var(--muted-foreground); font-size: 13px; }
form { margin-top: 26px; }
.field-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 38px; font-size: 13px; }
textarea { width: 100%; resize: vertical; min-height: 62px; padding: 12px; border: 1px solid var(--border-strong); border-radius: var(--radius-control); background: var(--muted); color: var(--foreground); font: inherit; font-size: 13px; overflow-wrap: anywhere; }
textarea:focus-visible { outline: 2px solid var(--ring); outline-offset: 2px; }
textarea[readonly] { color: var(--muted-foreground); font-family: monospace; }
.helper { margin: 7px 0 18px; font-size: 12px; color: var(--muted-foreground); line-height: 1.6; }
.file-import { display: inline-flex; align-items: center; gap: 5px; cursor: pointer; position: relative; }
.file-import input { position: absolute; inset: 0; opacity: 0; width: 100%; cursor: pointer; }
.file-import:focus-within { outline: 2px solid var(--ring); }
.error { color: var(--destructive); font-size: 13px; line-height: 1.7; }
.notice { font-size: 14px; line-height: 1.7; }
.actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 10px; margin-top: 18px; }
footer { display: flex; align-items: center; justify-content: space-between; border-top: 1px solid var(--border); margin-top: 28px; padding-top: 12px; font-size: 12px; color: var(--muted-foreground); }
</style>
