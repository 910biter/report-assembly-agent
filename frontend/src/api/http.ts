export class ApiError extends Error {
  constructor(message: string, public status: number, public payload: unknown) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { cache: "no-store", ...init });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof payload === "object" && payload && "error" in payload
      ? String((payload as { error: unknown }).error)
      : `请求失败（${response.status}）`;
    throw new ApiError(message, response.status, payload);
  }
  return payload as T;
}

export function jsonInit(method: string, payload: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) };
}

export const apiUrl = (path: string) => path;
