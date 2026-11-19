const configuredApiBase = process.env.NEXT_PUBLIC_API_URL?.trim();
export const API_BASE = configuredApiBase ? configuredApiBase.replace(/\/+$/, '') : '';

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

const GET_CACHE_TTL_MS = 8_000;
const MAX_CACHE_ENTRIES = 80;
const MAX_CACHE_BYTES = 4 * 1024 * 1024;

type CacheEntry = {
  body: string;
  size: number;
  expiresAt: number;
};

type ApiErrorBody = {
  detail?: string | { message?: string; blockers?: string[]; issues?: string[] };
};

const responseCache = new Map<string, CacheEntry>();
const inFlightRequests = new Map<string, Promise<string>>();
let cacheVersion = 0;
let cachedBytes = 0;

export function readToken(): string | null {
  return typeof window === 'undefined' ? null : window.localStorage.getItem('token');
}

function requestHeaders(init: RequestInit, token: string | null): Headers {
  const headers = new Headers(init.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  return headers;
}

export function clearApiCache(): void {
  cacheVersion += 1;
  responseCache.clear();
  inFlightRequests.clear();
  cachedBytes = 0;
}

export function readCachedApiData<T>(path: string, init: RequestInit = {}): T | null {
  if (typeof window === 'undefined' || (init.method ?? 'GET').toUpperCase() !== 'GET' || init.signal) return null;
  const token = readToken();
  const key = requestKey(path, token, requestHeaders(init, token), cacheVersion);
  const cached = responseCache.get(key);
  if (!cached) return null;
  if (cached.expiresAt <= Date.now()) {
    deleteCachedResponse(key);
    return null;
  }
  responseCache.delete(key);
  responseCache.set(key, cached);
  return JSON.parse(cached.body) as T;
}

function cacheTtl(path: string): number {
  const pathname = path.split('?', 1)[0];
  if (pathname.endsWith('/notifications') || pathname.endsWith('/early-warnings')) return 0;
  return GET_CACHE_TTL_MS;
}

function requestKey(path: string, token: string | null, headers: Headers, version: number): string {
  const headerEntries: string[] = [];
  headers.forEach((value, name) => headerEntries.push(`${name}:${value}`));
  headerEntries.sort();
  return [version, token ?? '', path, ...headerEntries].join('\u0000');
}

function deleteCachedResponse(key: string): void {
  const cached = responseCache.get(key);
  if (!cached) return;
  cachedBytes -= cached.size;
  responseCache.delete(key);
}

function storeResponse(key: string, entry: CacheEntry): void {
  deleteCachedResponse(key);
  if (entry.size > MAX_CACHE_BYTES) return;
  responseCache.set(key, entry);
  cachedBytes += entry.size;
  while (responseCache.size > MAX_CACHE_ENTRIES || cachedBytes > MAX_CACHE_BYTES) {
    const oldestKey = responseCache.keys().next().value;
    if (oldestKey === undefined) break;
    const oldestEntry = responseCache.get(oldestKey);
    if (oldestEntry) cachedBytes -= oldestEntry.size;
    responseCache.delete(oldestKey);
  }
}

async function requestError(response: Response): Promise<Error> {
  const body = await response.json().catch(() => null) as ApiErrorBody | null;
  const detail = body?.detail;
  const message = typeof detail === 'string'
    ? detail
    : detail?.message
      ? [detail.message, ...(detail.blockers ?? detail.issues ?? [])].join(' ')
      : `Request failed (${response.status})`;
  return new Error(message);
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase();
  const token = readToken();
  const headers = requestHeaders(init, token);

  const isMutation = method !== 'GET';
  if (isMutation) clearApiCache();

  const ttl = cacheTtl(path);
  const canShareRequest = method === 'GET' && !init.signal;
  const version = cacheVersion;
  const key = requestKey(path, token, headers, version);

  if (canShareRequest) {
    const cached = responseCache.get(key);
    if (cached && cached.expiresAt > Date.now()) {
      responseCache.delete(key);
      responseCache.set(key, cached);
      return JSON.parse(cached.body) as T;
    }
    if (cached) deleteCachedResponse(key);

    const pending = inFlightRequests.get(key);
    if (pending) return JSON.parse(await pending) as T;
  }

  const request = (async () => {
    const response = await fetch(apiUrl(path), { ...init, headers, cache: 'no-store' });
    if (!response.ok) throw await requestError(response);
    const data: unknown = await response.json();
    const body = JSON.stringify(data);
    if (body === undefined) throw new Error('API returned a response that cannot be reused safely.');
    if (canShareRequest && ttl > 0 && version === cacheVersion) {
      storeResponse(key, { body, size: body.length * 2, expiresAt: Date.now() + ttl });
    }
    return body;
  })();

  if (canShareRequest) inFlightRequests.set(key, request);
  try {
    return JSON.parse(await request) as T;
  } finally {
    if (canShareRequest && inFlightRequests.get(key) === request) inFlightRequests.delete(key);
    if (isMutation) clearApiCache();
  }
}
