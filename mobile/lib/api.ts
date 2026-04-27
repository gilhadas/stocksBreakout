/**
 * API client for StocksBreakout Portfolio API.
 * Handles JWT auth and all fetch calls.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

const TOKEN_KEY = 'jwt_token';
const API_URL_KEY = 'api_url';

// Permanent API URL via Cloudflare named tunnel — never changes
const API_BASE_URL = 'https://gilhadas-stocks.com';

let _baseUrl = API_BASE_URL;

export async function getBaseUrl(): Promise<string> {
  return API_BASE_URL;
}

export async function setBaseUrl(url: string) {
  _baseUrl = url.replace(/\/+$/, '');
  await AsyncStorage.setItem(API_URL_KEY, _baseUrl);
}

export async function getToken(): Promise<string | null> {
  return AsyncStorage.getItem(TOKEN_KEY);
}

export async function saveToken(token: string) {
  await AsyncStorage.setItem(TOKEN_KEY, token);
}

export async function clearToken() {
  await AsyncStorage.removeItem(TOKEN_KEY);
}

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

async function authFetch(path: string, opts: RequestInit = {}) {
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
    throw new Error('Session expired');
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function login(password: string, apiUrl?: string) {
  if (apiUrl) await setBaseUrl(apiUrl);
  const base = await getBaseUrl();
  const res = await fetch(`${base}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  });
  if (!res.ok) throw new Error('Wrong password');
  const data = await res.json();
  await saveToken(data.token);
  return data.token;
}

export async function loginWithEmail(email: string, password: string) {
  const base = await getBaseUrl();
  const res = await fetch(`${base}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error('Invalid credentials');
  const data = await res.json();
  await saveToken(data.token);
  return data.token;
}

export async function getGoogleAuthUrl(): Promise<string> {
  const base = await getBaseUrl();
  // Platform import is from react-native — 'web' when running as Expo web export
  const { Platform } = require('react-native');
  const client = Platform.OS === 'web' ? 'web' : 'mobile';
  return `${base}/auth/google?client=${client}`;
}

export function resetPortfolio() {
  return authFetch('/portfolio/reset', { method: 'POST' });
}

export async function recalculatePortfolio(minDate?: string, positionPct?: number): Promise<Record<string, unknown>> {
  const { job_id } = await authFetch('/portfolio/recalculate', {
    method: 'POST',
    body: JSON.stringify({ min_date: minDate ?? null, position_pct: positionPct ?? null }),
  }) as { job_id: string };

  // Poll until done (max 5 minutes, 3s interval)
  for (let i = 0; i < 100; i++) {
    await new Promise(r => setTimeout(r, 3000));
    const status = await authFetch(`/portfolio/recalculate/status/${job_id}`) as Record<string, unknown>;
    if (status.status === 'done') return status.result as Record<string, unknown>;
    if (status.status === 'error') throw new Error((status.error as string) || 'Recalculate failed');
  }
  throw new Error('Recalculate timed out');
}

export function fetchPortfolio() {
  return authFetch('/portfolio');
}

export function refreshPortfolio() {
  return authFetch('/portfolio/refresh', { method: 'POST' });
}

export function fetchSkipped() {
  return authFetch('/portfolio/skipped');
}

export function fetchManualPortfolio() {
  return authFetch('/manual-portfolio');
}

export function computeStops() {
  return authFetch('/manual-portfolio/compute-stops', { method: 'POST' });
}

export function sellPosition(symbol: string, exit_price: number) {
  return authFetch('/manual-portfolio/sell', {
    method: 'POST',
    body: JSON.stringify({ symbol, exit_price }),
  });
}

export function buyPosition(data: {
  symbol: string;
  shares: number;
  entry_price: number;
  stop?: number;
  target?: number;
  sector?: string;
  broker?: string;
  mode?: string;
}) {
  return authFetch('/manual-portfolio/buy', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function suggestSwaps() {
  return authFetch('/portfolio/suggest-swaps', { method: 'POST' });
}

export function executeSwap(close_symbol: string, open_symbol: string) {
  return authFetch('/portfolio/execute-swap', {
    method: 'POST',
    body: JSON.stringify({ close_symbol, open_symbol }),
  });
}

export function undoSwap() {
  return authFetch('/portfolio/undo-swap', { method: 'POST' });
}

export function registerPushToken(token: string) {
  return authFetch('/push/register', {
    method: 'POST',
    body: JSON.stringify({ token }),
  });
}

export function analyzeSymbol(symbol: string, mode: string, timeframe: string) {
  return authFetch('/analyze', {
    method: 'POST',
    body: JSON.stringify({ symbol, mode, timeframe }),
  });
}

export function analyzeChat(report: any, history: any[], question: string) {
  return authFetch('/analyze/chat', {
    method: 'POST',
    body: JSON.stringify({ report, history, question }),
  });
}

export function analyzeLlmStatus(): Promise<{ enabled: boolean }> {
  return authFetch('/analyze/llm-status');
}
