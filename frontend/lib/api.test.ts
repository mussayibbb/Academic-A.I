import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, clearApiCache, readCachedApiData } from './api';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('api request reuse', () => {
  let token: string | null;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    token = null;
    clearApiCache();
    vi.stubGlobal('window', {
      localStorage: {
        getItem: (key: string) => key === 'token' ? token : null,
      },
    });
    fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse({ value: 1 })));
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    clearApiCache();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it.each([
    ['https://academic-ai-backend-nuuf.onrender.com/', 'https://academic-ai-backend-nuuf.onrender.com'],
    ['http://localhost:8000/', 'http://localhost:8000'],
    ['', ''],
  ])('resolves API URLs from NEXT_PUBLIC_API_URL (%s)', async (configuredBase, expectedBase) => {
    vi.stubEnv('NEXT_PUBLIC_API_URL', configuredBase);
    vi.resetModules();
    const configuredApi = await import('./api');

    expect(configuredApi.API_BASE).toBe(expectedBase);
    expect(configuredApi.apiUrl('/api/v1/auth/login')).toBe(`${expectedBase}/api/v1/auth/login`);

    await configuredApi.api('/api/v1/auth/login', { method: 'POST', body: '{}' });
    expect(fetchMock.mock.calls[0]?.[0]).toBe(`${expectedBase}/api/v1/auth/login`);
  });

  it('deduplicates simultaneous identical GET requests', async () => {
    let resolveResponse: (response: Response) => void = () => undefined;
    fetchMock.mockImplementation(() => new Promise<Response>((resolve) => {
      resolveResponse = resolve;
    }));

    const first = api<{ value: number }>('/api/v1/dashboard/teacher');
    const second = api<{ value: number }>('/api/v1/dashboard/teacher');
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resolveResponse(jsonResponse({ value: 3 }));
    await expect(Promise.all([first, second])).resolves.toEqual([
      { value: 3 },
      { value: 3 },
    ]);
  });

  it('caches successful GET responses briefly and expires them', async () => {
    vi.useFakeTimers();

    await api('/api/v1/dashboard/teacher');
    await api('/api/v1/dashboard/teacher');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(readCachedApiData('/api/v1/dashboard/teacher')).toEqual({ value: 1 });

    vi.advanceTimersByTime(8_001);
    expect(readCachedApiData('/api/v1/dashboard/teacher')).toBeNull();
    await api('/api/v1/dashboard/teacher');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does not retain notifications after their request completes', async () => {
    await api('/api/v1/notifications');
    await api('/api/v1/notifications');

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does not retain responses larger than the cache byte budget', async () => {
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse({ value: 'x'.repeat(2_100_000) })));

    await api('/api/v1/reports/large');
    await api('/api/v1/reports/large');

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('keeps cached responses isolated by authentication token', async () => {
    fetchMock.mockImplementation((_url: string, init: RequestInit) => {
      const headers = new Headers(init.headers);
      return Promise.resolve(jsonResponse({ authorization: headers.get('Authorization') }));
    });

    token = 'student-session';
    await expect(api('/api/v1/students/me')).resolves.toEqual({
      authorization: 'Bearer student-session',
    });

    token = 'teacher-session';
    await expect(api('/api/v1/students/me')).resolves.toEqual({
      authorization: 'Bearer teacher-session',
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('invalidates cached GET responses after mutations', async () => {
    await api('/api/v1/dashboard/teacher');
    await api('/api/v1/assignments', { method: 'POST', body: '{}' });
    await api('/api/v1/dashboard/teacher');

    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('preserves API errors and does not cache failed requests', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ detail: 'Not authorized' }, 403))
      .mockResolvedValueOnce(jsonResponse({ value: 2 }));

    await expect(api('/api/v1/dashboard/teacher')).rejects.toThrow('Not authorized');
    await expect(api('/api/v1/dashboard/teacher')).resolves.toEqual({ value: 2 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
