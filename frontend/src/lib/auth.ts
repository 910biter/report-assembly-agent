import { ref } from "vue";

export interface SessionUser {
  id: number;
  username: string;
  role: "admin" | "user";
}

export const authMode = ref<"checking" | "off" | "on">("checking");
export const currentUser = ref<SessionUser | null>(null);
let pending: Promise<void> | null = null;

export function clearSession() {
  currentUser.value = null;
}

export function ensureSession(): Promise<void> {
  if (authMode.value !== "checking") return Promise.resolve();
  if (pending) return pending;
  pending = (async () => {
    const response = await fetch("/api/auth/me", { cache: "no-store", credentials: "same-origin" });
    if (response.status === 503) {
      authMode.value = "off";
      return;
    }
    authMode.value = "on";
    currentUser.value = response.ok ? (await response.json()) as SessionUser : null;
  })().finally(() => { pending = null; });
  return pending;
}

export async function signIn(username: string, password: string): Promise<void> {
  const response = await fetch("/api/auth/login", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    throw new Error(response.status === 429 ? "尝试次数过多，请稍后再试" : "用户名或密码不正确");
  }
  authMode.value = "on";
  currentUser.value = (await response.json()) as SessionUser;
}

export async function signOut(): Promise<void> {
  try {
    await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
  } finally {
    clearSession();
    window.location.assign("/login");
  }
}
