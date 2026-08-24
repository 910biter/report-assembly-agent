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
export const apiUrl = (path) => path;
