/**
 * trading-api-kit — authentication API calls.
 *
 * Covers email/password login, Google OAuth, and logout.
 */

import { authFetch, saveToken, clearToken, getBaseUrl } from './client';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface LoginResponse {
  token: string;
  user_id?: string;
  email?: string;
}

export interface UserInfo {
  id: string;
  email: string;
  name: string;
  created_at: string | null;
  last_login: string | null;
}

// ── Login ─────────────────────────────────────────────────────────────────────

/**
 * Login with email + password. Saves the JWT to AsyncStorage on success.
 *
 * @throws Error('Invalid credentials') on 401
 */
export async function loginWithEmail(email: string, password: string): Promise<LoginResponse> {
  const base = await getBaseUrl();
  const res = await fetch(`${base}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error('Invalid credentials');
  const data = (await res.json()) as LoginResponse;
  await saveToken(data.token);
  return data;
}

/**
 * Legacy single-password login (no email required).
 * Kept for backward compat with old mobile clients.
 */
export async function loginWithPassword(password: string): Promise<LoginResponse> {
  const base = await getBaseUrl();
  const res = await fetch(`${base}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  });
  if (!res.ok) throw new Error('Wrong password');
  const data = (await res.json()) as LoginResponse;
  await saveToken(data.token);
  return data;
}

/** Log out the current user by clearing the stored JWT. */
export async function logout(): Promise<void> {
  await clearToken();
}

// ── Current user ──────────────────────────────────────────────────────────────

/** Fetch the authenticated user's profile from /auth/me. */
export async function getCurrentUser(): Promise<UserInfo> {
  return authFetch<UserInfo>('/auth/me');
}

// ── Google OAuth ──────────────────────────────────────────────────────────────

/**
 * Build the Google OAuth redirect URL for the current client platform.
 * Use with expo-web-browser's openAuthSessionAsync on native,
 * or window.location.href on web.
 *
 * @param appScheme  Your app's custom URL scheme (e.g., 'myapp').
 *                   Must match MOBILE_APP_SCHEME in server .env.
 */
export async function getGoogleAuthUrl(appScheme = 'myapp'): Promise<string> {
  const base = await getBaseUrl();
  const { Platform } = require('react-native');
  const client = Platform.OS === 'web' ? 'web' : 'mobile';
  return `${base}/auth/google?client=${client}`;
}
