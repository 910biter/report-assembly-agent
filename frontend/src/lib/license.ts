import { ref } from "vue";

export interface LicenseStatus {
  active: boolean;
  status: string;
  expires_at?: number;
  activated_at?: number;
  installation_id?: string;
  license_id?: string;
  generation?: number;
  duration_months?: number;
  signing_ready?: boolean;
}

export const licenseStatus = ref<LicenseStatus | null>(null);
let pending: Promise<LicenseStatus> | null = null;

export async function refreshLicense(): Promise<LicenseStatus> {
  if (pending) return pending;
  pending = (async () => {
    const response = await fetch("/api/license/status", { cache: "no-store", credentials: "same-origin" });
    if (!response.ok) throw new Error("无法读取授权状态，请重试");
    const value = await response.json() as LicenseStatus;
    licenseStatus.value = value;
    return value;
  })().finally(() => { pending = null; });
  return pending;
}

export function licenseRequired(payload: unknown) {
  if (typeof payload === "object" && payload && "detail" in payload
      && payload.detail === "LICENSE_REQUIRED") {
    licenseStatus.value = { active: false, status: "expired" };
    window.dispatchEvent(new Event("ira-license-required"));
  }
}

export const licenseMessages: Record<string, string> = {
  LICENSE_MODULE_UNAVAILABLE: "授权校验组件缺失或损坏，请联系管理员修复安装。",
  INVALID_LICENSE: "许可证格式或签名无效，请检查是否复制完整。",
  UNKNOWN_SIGNING_KEY: "此许可证的签发密钥不受当前版本信任，请联系授权方。",
  DEVICE_MISMATCH: "许可证与本机安装不匹配，请使用当前安装识别码重新申请。",
  LICENSE_ALREADY_USED: "此许可证已使用或已被替换，请申请新的许可证。",
  CLOCK_ROLLBACK: "检测到系统时间回退，请检查服务器时间。",
  CLOCK_BEFORE_ISSUE: "服务器时间早于许可证签发时间，请先校正时间。",
  STATE_DAMAGED: "本地授权记录异常，请联系授权方恢复。",
  LICENSE_STORAGE_UNAVAILABLE: "无法保存授权，请检查服务数据目录的权限和空间。",
  INSTALLATION_IDENTITY_UNAVAILABLE: "无法读取本机身份，请检查安装环境。",
  INVALID_RECOVERY: "恢复凭证已失效或不匹配，请重新申请。",
};
