/**
 * trading-api-kit — core HTTP client with JWT auth.
 *
 * Configurable base URL, automatic token injection, 401 → session clear.
 * Works in React Native and Expo web.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

const TOKEN_KEY = 'jwt_token';
const API_URL_KEY = 'api_url';

// ── Config ────────────────────────────────────────────────────────────────────

let _baseUrl = '';

/**
 * Initialize the client with your server's base URL.
 * Call this once at app startup before any API calls.
 *
 * @example
 *   import { configure } from 'trading-api-kit';
 *   configure({ baseUrl: 'https://my-scanner.example.com' });
 */
export async function configure(opts: { baseUrl: string }) {
  _baseUrl = opts.baseUrl.replace(/\/+$/, '');
  await AsyncStorage.setItem(API_URL_KEY, _baseUrl);
}

export async function getBaseUrl(): Promise<string> {
  if (_baseUrl) return _baseUrl;
  const stored = await AsyncStorage.getItem(API_URL_KEY);
  if (stored) { _baseUrl = stored; return _baseUrl; }
  throw new Error('trading-api-kit: baseUrl not configured. Call configure({ baseUrl }) first.');
}

// ── Token management ──────────────────────────────────────────────────────────

export async function getToken(): Promise<string | null> {
  return AsyncStorage.getItem(TOKEN_KEY);
}

export async function saveToken(token: string): Promise<void> {
  await AsyncStorage.setItem(TOKEN_KEY, token);
}

export async function clearToken(): Promise<void> {
  await AsyncStorage.removeItem(TOKEN_KEY);
}

/**
 * Decode the email claim from the stored JWT without verifying signature.
 * Useful for displaying "Logged in as" in the UI.
 */
export async function getEmailFromToken(): Promise<string> {
  const token = await getToken();
  if (!token) return '';
  try {
    const payload = token.split('.')[1];
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/');
    const json = JSON.parse(atob(base64));
    return (json.email as string) || '';
  } catch {
    return '';
  }
}

/**
 * Check whether the stored JWT is present (does not verify expiry — server
 * will return 401 if expired and the token will be cleared automatically).
 */
export async function isLoggedIn(): Promise<boolean> {
  const token = await getToken();
  return token !== null;
}

// ── Core fetch ────────────────────────────────────────────────────────────────

export class SessionExpiredError extends Error {
  constructor() { super('Session expired'); this.name = 'SessionExpiredError'; }
}

/**
 * Authenticated fetch wrapper.
 * Injects Bearer token, sets Content-Type: application/json, and on 401
 * clears the stored token and throws SessionExpiredError.
 *
 * @example
 *   const data = await authFetch('/portfolio');
 *   const result = await authFetch('/portfolio/buy', {
 *     method: 'POST',
 *     body: JSON.stringify({ symbol: 'AAPL', shares: 10 })
 *   });
 */
export async function authFetch<T = unknown>(
  path: string,
  opts: RequestInit = {},
): Promise<T> {
  const base = await getBaseUrl();
  const token = await getToken();
  const res = await fetch(`${base}${path}`, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...opts.headers,
    },
  });
  if (res.status === 401) {
    await clearToken();
    throw new SessionExpiredError();
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`HTTP ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}
