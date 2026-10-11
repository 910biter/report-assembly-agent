<script setup lang="ts">
import { ref } from "vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { api, jsonInit } from "@/api/http";
import { currentUser, signOut } from "@/lib/auth";
import UiPageHeader from "@/components/ui/UiPageHeader.vue";
import UiButton from "@/components/ui/UiButton.vue";

interface Account { id: number; username: string; role: string; disabled: number; created_at: string }
const queryClient = useQueryClient();
const accounts = useQuery({ queryKey: ["admin-users"], queryFn: () => api<Account[]>("/api/admin/users") });
const username = ref("");
const password = ref("");
const busy = ref(false);
const message = ref("");

async function refresh() { await queryClient.invalidateQueries({ queryKey: ["admin-users"] }); }
async function create() {
  if (busy.value) return;
  busy.value = true; message.value = "";
  try {
    await api("/api/admin/users", jsonInit("POST", { username: username.value, password: password.value }));
    username.value = ""; password.value = ""; message.value = "账号已创建";
    await refresh();
  } catch (error) { message.value = error instanceof Error ? error.message : "创建失败"; }
  finally { busy.value = false; }
}
async function disable(account: Account) {
  if (!confirm(`停用账号 ${account.username}？该用户的现有会话会立即失效。`)) return;
  try {
    await api(`/api/admin/users/${account.id}/disable`, { method: "POST" });
    message.value = `已停用 ${account.username}`; await refresh();
  } catch (error) { message.value = error instanceof Error ? error.message : "操作失败"; }
}
async function reset(account: Account) {
  const next = prompt(`设置 ${account.username} 的新密码（至少 12 个字符）`);
  if (next === null) return;
  try {
    await api(`/api/admin/users/${account.id}/reset-password`, jsonInit("POST", { password: next }));
    if (account.id === currentUser.value?.id) {
      await signOut();
      return;
    }
    message.value = `已重置 ${account.username} 的密码并注销旧会话`;
  } catch (error) { message.value = error instanceof Error ? error.message : "操作失败"; }
}
</script>

<template>
  <div class="page-stack accounts-page">
    <UiPageHeader title="账号管理" />
    <div class="accounts-grid">
      <section class="accounts-list" aria-label="用户列表">
        <div class="section-heading"><h2>用户</h2><span>{{ accounts.data.value?.length || 0 }} 个账号</span></div>
        <p v-if="accounts.isLoading.value" class="muted">正在读取账号…</p>
        <p v-else-if="accounts.isError.value" class="error">账号加载失败，请刷新重试。</p>
        <div v-for="account in accounts.data.value || []" :key="account.id" class="account-row">
          <div><strong>{{ account.username }}</strong><small>{{ account.role === "admin" ? "管理员" : account.disabled ? "已停用" : "普通用户" }}</small></div>
          <div v-if="!account.disabled && (account.role !== 'admin' || account.id === currentUser?.id)" class="row-actions">
            <UiButton variant="ghost" size="sm" @click="reset(account)">{{ account.id === currentUser?.id ? "修改我的密码" : "重置密码" }}</UiButton>
            <UiButton v-if="account.role !== 'admin'" variant="ghost" size="sm" @click="disable(account)">停用</UiButton>
          </div>
        </div>
      </section>
      <section class="create-panel" aria-label="创建账号">
        <h2>创建普通用户</h2>
        <p>用户只能查看自己的材料、任务和报告。</p>
        <form @submit.prevent="create">
          <label for="account-name">用户名</label>
          <input id="account-name" v-model.trim="username" autocomplete="off" minlength="3" maxlength="64" required />
          <label for="account-password">初始密码</label>
          <input id="account-password" v-model="password" type="password" autocomplete="new-password" minlength="12" required />
          <UiButton type="submit" :loading="busy">创建账号</UiButton>
        </form>
        <p v-if="message" class="feedback" role="status">{{ message }}</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.accounts-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, 340px); gap: 24px; align-items: start; }
.accounts-list { min-width: 0; }
.section-heading, .account-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; border-bottom: 1px solid var(--border); }
.section-heading { padding: 0 0 14px; }
.section-heading h2, .create-panel h2 { margin: 0; font-size: 16px; font-weight: 600; }
.section-heading span, .muted, .create-panel p { color: var(--muted-foreground); font-size: 13px; }
.account-row { min-height: 66px; }
.account-row strong { display: block; font-size: 14px; font-weight: 550; }
.account-row small { display: block; margin-top: 3px; color: var(--muted-foreground); font-size: 12px; }
.row-actions { display: flex; gap: 4px; }
.create-panel { padding: 22px; border: 1px solid var(--border-strong); border-radius: var(--radius-panel); background: var(--card); }
.create-panel form { display: grid; gap: 10px; margin-top: 20px; }
.create-panel label { margin-top: 6px; font-size: 13px; }
.create-panel input { height: 38px; padding: 0 10px; border: 1px solid var(--border-strong); border-radius: var(--radius-control); background: var(--muted); color: var(--foreground); }
.create-panel input:focus { outline: 2px solid var(--ring); }
.create-panel button { margin-top: 8px; }
.feedback { margin: 16px 0 0; } .error { color: var(--destructive); }
@media (max-width: 820px) { .accounts-grid { grid-template-columns: 1fr; } }
</style>
