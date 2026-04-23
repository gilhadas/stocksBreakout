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
  return `${base}/auth/google`;
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
