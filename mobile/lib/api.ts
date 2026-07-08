/**
 * StocksBreakout mobile API client.
 *
 * Generic auth/push is provided by trading-api-kit (trading_api_kit/ts_client/).
 * This file re-exports the kit and adds StocksBreakout-specific endpoints.
 *
 * To adapt for another scanner: copy trading_api_kit/ts_client/src/ into
 * your project, configure it, and replace the scanner-specific calls below.
 */

// ── Re-export everything from the reusable kit ────────────────────────────────
export {
  configure,
  authFetch,
  getToken,
  saveToken,
  clearToken,
  getEmailFromToken,
  isLoggedIn,
  loginWithEmail,
  loginWithPassword,
  logout,
  getCurrentUser,
  getGoogleAuthUrl,
  registerForPushNotifications,
  registerPushToken,
  SessionExpiredError,
} from '../../trading_api_kit/ts_client/src/index';

// Legacy alias — kept for screens that used the old `login()` single-arg function
import { loginWithPassword } from '../../trading_api_kit/ts_client/src/index';
export async function login(password: string, _apiUrl?: string) {
  return loginWithPassword(password);
}

// ── StocksBreakout-specific endpoints ─────────────────────────────────────────
import { authFetch } from '../../trading_api_kit/ts_client/src/client';

export function resetPortfolio() {
  return authFetch('/portfolio/reset', { method: 'POST' });
}

export async function recalculatePortfolio(
  minDate?: string,
  positionPct?: number,
): Promise<Record<string, unknown>> {
  const { job_id } = await authFetch<{ job_id: string }>('/portfolio/recalculate', {
    method: 'POST',
    body: JSON.stringify({ min_date: minDate ?? null, position_pct: positionPct ?? null }),
  });

  // Poll until done (max 5 minutes, 3s interval)
  for (let i = 0; i < 100; i++) {
    await new Promise((r) => setTimeout(r, 3000));
    const status = await authFetch<Record<string, unknown>>(
      `/portfolio/recalculate/status/${job_id}`,
    );
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
  return authFetch('/manual-portfolio/buy', { method: 'POST', body: JSON.stringify(data) });
}

export function fetchSwapSuggestions() {
  return authFetch('/portfolio/swap-suggestions');
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

export function analyzeSymbol(symbol: string, mode: string, timeframe: string) {
  return authFetch('/analyze', {
    method: 'POST',
    body: JSON.stringify({ symbol, mode, timeframe }),
  });
}

export function analyzeChat(report: unknown, history: unknown[], question: string) {
  return authFetch('/analyze/chat', {
    method: 'POST',
    body: JSON.stringify({ report, history, question }),
  });
}

export function analyzeLlmStatus(): Promise<{ enabled: boolean }> {
  return authFetch('/analyze/llm-status');
}
