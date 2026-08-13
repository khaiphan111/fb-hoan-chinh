// FB Live/Die Checker — Tác giả: @nhanxp | Hỗ trợ: Telegram/Facebook nhanxp
const KEY = "fbc_token";

export function getToken(): string {
  return localStorage.getItem(KEY) || "";
}
export function setToken(t: string) {
  localStorage.setItem(KEY, t);
}
export function clearToken() {
  localStorage.removeItem(KEY);
}

export async function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    const headers: any = {
      Authorization: `Bearer ${getToken()}`,
      ...(opts.headers || {}),
    };
    if (!(opts.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }

    res = await fetch(path, {
      ...opts,
      headers,
    });
  } catch {
    throw new Error("Không kết nối được máy chủ. Hãy chắc chắn ứng dụng đang chạy.");
  }
  if (res.status === 401) {
    clearToken();
    throw new Error("Phiên đăng nhập đã hết. Vui lòng đăng nhập lại.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Có lỗi xảy ra");
  return data as T;
}

export async function login(username: string, password: string): Promise<string> {
  let res: Response;
  try {
    res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
  } catch {
    throw new Error("Không kết nối được máy chủ. Hãy chắc chắn ứng dụng đang chạy.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Sai tên đăng nhập hoặc mật khẩu");
  setToken(data.token);
  return data.token;
}
