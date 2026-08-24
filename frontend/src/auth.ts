/**
 * 认证层 —— token 存取 + 登录/注册 API
 */

const TOKEN_KEY = 'ecom_token';

/** 读取 token */
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

/** 保存 token */
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

/** 删除 token（登出） */
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

/** 是否已登录（有 token 即视为已登录） */
export function isLoggedIn(): boolean {
  return !!getToken();
}

interface LoginResult {
  access_token: string;
  user_id: number;
  username: string;
}

/** 登录：返回 token，成功后自动保存 */
export async function login(username: string, password: string): Promise<LoginResult> {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail || `登录失败: ${res.status}`);
  }
  const data = await res.json();
  setToken(data.access_token);
  return data;
}

/** 注册：成功后自动登录并保存 token */
export async function register(username: string, password: string, name: string): Promise<LoginResult> {
  const res = await fetch('/api/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, name }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail || `注册失败: ${res.status}`);
  }
  const data = await res.json();
  setToken(data.access_token);
  return data;
}
