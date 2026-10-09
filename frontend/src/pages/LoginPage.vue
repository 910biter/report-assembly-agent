<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { signIn } from "@/lib/auth";
import UiButton from "@/components/ui/UiButton.vue";

const route = useRoute();
const router = useRouter();
const username = ref("");
const password = ref("");
const pending = ref(false);
const error = ref("");

async function submit() {
  if (pending.value) return;
  pending.value = true;
  error.value = "";
  try {
    await signIn(username.value, password.value);
    const destination = String(route.query.redirect || "/");
    await router.replace(destination.startsWith("/") && !destination.startsWith("//") ? destination : "/");
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "登录失败，请重试";
  } finally {
    pending.value = false;
  }
}
</script>

<template>
  <main class="login-screen">
    <div class="login-frame">
      <div class="login-mark" aria-hidden="true">R</div>
      <div class="login-heading">
        <p>报告整编工作台</p>
        <h1>登录工作区</h1>
        <span>使用管理员分配的账号继续。</span>
      </div>
      <form class="login-form" @submit.prevent="submit">
        <label for="login-username">用户名</label>
        <input id="login-username" v-model.trim="username" name="username" autocomplete="username" required autofocus />
        <label for="login-password">密码</label>
        <input id="login-password" v-model="password" name="password" type="password" autocomplete="current-password" required />
        <p v-if="error" class="login-error" role="alert">{{ error }}</p>
        <UiButton type="submit" :loading="pending" class="login-submit">登录</UiButton>
      </form>
      <p class="login-foot">账号由管理员创建；暂不提供自助注册。</p>
    </div>
  </main>
</template>

<style scoped>
.login-screen { min-height: 100vh; display: grid; place-items: center; padding: 24px; background: var(--background); color: var(--foreground); }
.login-frame { width: min(100%, 390px); padding: 32px; background: var(--card); border: 1px solid var(--border-strong); border-radius: var(--radius-panel); }
.login-mark { display: grid; place-items: center; width: 38px; height: 38px; border-radius: var(--radius-control); background: var(--primary-soft); color: var(--accent); font-weight: 700; }
.login-heading { margin: 24px 0 28px; }
.login-heading p { margin: 0 0 5px; color: var(--muted-foreground); font-size: 12px; }
.login-heading h1 { margin: 0; font-size: 27px; font-weight: 600; letter-spacing: -.03em; }
.login-heading span { display: block; margin-top: 9px; color: var(--muted-foreground); font-size: 13px; }
.login-form { display: grid; gap: 9px; }
.login-form label { margin-top: 10px; font-size: 13px; color: var(--foreground); }
.login-form input { width: 100%; height: 40px; padding: 0 12px; border: 1px solid var(--border-strong); border-radius: var(--radius-control); background: var(--muted); color: var(--foreground); outline: none; }
.login-form input:focus { border-color: var(--primary); box-shadow: 0 0 0 3px var(--ring); }
.login-submit { width: 100%; margin-top: 14px; }
.login-error { margin: 4px 0 0; color: var(--destructive); font-size: 13px; }
.login-foot { margin: 22px 0 0; color: var(--subtle-foreground); font-size: 12px; }
</style>
