export type LoginResponse = {
  access_token: string;
  user: { role: string };
};

function isLoginResponse(value: unknown): value is LoginResponse {
  if (typeof value !== 'object' || value === null) return false;
  const body = value as Partial<LoginResponse>;
  return (
    typeof body.access_token === 'string'
    && typeof body.user === 'object'
    && body.user !== null
    && typeof body.user.role === 'string'
  );
}

export async function parseLoginResponse(response: Response): Promise<LoginResponse> {
  const text = await response.text();
  let body: unknown = null;
  if (text.trim()) {
    try {
      body = JSON.parse(text) as unknown;
    } catch {
      body = null;
    }
  }

  if (!response.ok) {
    const detail = (
      typeof body === 'object'
      && body !== null
      && 'detail' in body
      && typeof body.detail === 'string'
    )
      ? body.detail
      : `Sign in failed (${response.status}). Please try again or contact support.`;
    throw new Error(detail);
  }

  if (!isLoginResponse(body)) {
    throw new Error('The sign-in service returned an invalid response. Please try again later.');
  }
  return body;
}
