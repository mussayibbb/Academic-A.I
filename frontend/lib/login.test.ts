import { describe, expect, it } from 'vitest';
import { parseLoginResponse } from './login';

describe('parseLoginResponse', () => {
  it('accepts a successful JSON login response', async () => {
    const response = new Response(
      JSON.stringify({ access_token: 'test-token', user: { role: 'TEACHER' } }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );

    await expect(parseLoginResponse(response)).resolves.toEqual({
      access_token: 'test-token',
      user: { role: 'TEACHER' },
    });
  });

  it('uses the API detail for JSON authentication errors', async () => {
    const response = new Response(
      JSON.stringify({ detail: 'Invalid email or password' }),
      { status: 401, headers: { 'Content-Type': 'application/json' } },
    );

    await expect(parseLoginResponse(response)).rejects.toThrow(
      'Invalid email or password',
    );
  });

  it('does not parse a plain-text server error as JSON', async () => {
    const response = new Response('Internal Server Error', {
      status: 500,
      headers: { 'Content-Type': 'text/plain' },
    });

    await expect(parseLoginResponse(response)).rejects.toThrow(
      'Sign in failed (500). Please try again or contact support.',
    );
  });

  it('handles an empty or malformed successful response safely', async () => {
    await expect(
      parseLoginResponse(new Response('', { status: 200 })),
    ).rejects.toThrow('The sign-in service returned an invalid response.');
    await expect(
      parseLoginResponse(new Response('<html>not JSON</html>', { status: 200 })),
    ).rejects.toThrow('The sign-in service returned an invalid response.');
  });
});
