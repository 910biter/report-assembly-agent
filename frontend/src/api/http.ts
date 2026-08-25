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

export function uploadForm<T>(
  path: string,
  form: FormData,
  onProgress?: (loaded: number, total: number) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", path);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded, event.total);
    };
    request.onerror = () => reject(new ApiError("上传连接失败", 0, null));
    request.onload = () => {
      let payload: unknown = request.responseText;
      try { payload = JSON.parse(request.responseText); } catch { /* plain-text error */ }
      if (request.status >= 200 && request.status < 300) {
        resolve(payload as T);
        return;
      }
      const message = typeof payload === "object" && payload && "error" in payload
        ? String((payload as { error: unknown }).error)
        : `请求失败（${request.status}）`;
      reject(new ApiError(message, request.status, payload));
    };
    request.send(form);
  });
}

export const apiUrl = (path: string) => path;
