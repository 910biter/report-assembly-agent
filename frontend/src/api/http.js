export class ApiError extends Error {
    status;
    payload;
    constructor(message, status, payload) {
        super(message);
        this.status = status;
        this.payload = payload;
    }
}
export async function api(path, init) {
    const response = await fetch(path, { cache: "no-store", ...init });
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
        const message = typeof payload === "object" && payload && "error" in payload
            ? String(payload.error)
            : `请求失败（${response.status}）`;
        throw new ApiError(message, response.status, payload);
    }
    return payload;
}
export function jsonInit(method, payload) {
    return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) };
}
export function uploadForm(path, form, onProgress) {
    return new Promise((resolve, reject) => {
        const request = new XMLHttpRequest();
        request.open("POST", path);
        request.upload.onprogress = (event) => {
            if (event.lengthComputable)
                onProgress?.(event.loaded, event.total);
        };
        request.onerror = () => reject(new ApiError("上传连接失败", 0, null));
        request.onload = () => {
            let payload = request.responseText;
            try {
                payload = JSON.parse(request.responseText);
            }
            catch { /* plain-text error */ }
            if (request.status >= 200 && request.status < 300) {
                resolve(payload);
                return;
            }
            const message = typeof payload === "object" && payload && "error" in payload
                ? String(payload.error)
                : `请求失败（${request.status}）`;
            reject(new ApiError(message, request.status, payload));
        };
        request.send(form);
    });
}
export const apiUrl = (path) => path;
