export const API_BASE = '';

export function readToken(): string | null {
  return typeof window === 'undefined' ? null : window.localStorage.getItem('token');
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = readToken();
  const headers = new Headers(init.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers, cache: 'no-store' });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as {
      detail?: string | { message?: string; blockers?: string[]; issues?: string[] };
    } | null;
    const detail = body?.detail;
    const message = typeof detail === 'string'
      ? detail
      : detail?.message
        ? [detail.message, ...(detail.blockers ?? detail.issues ?? [])].join(' ')
        : `Request failed (${response.status})`;
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}