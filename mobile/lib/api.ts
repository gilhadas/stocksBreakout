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

// ── Book variants (live control-vs-autoswap A/B) ─────────────────────────────
// Every portfolio call is book-scoped. `book` is always optional and an omitted
// value resolves to 'control' server-side, so an older client keeps seeing the
// exact book it saw before the A/B existed.
export type BookName = string;

export interface BookInfo {
  name: BookName;
  label: string;
  auto_swap: boolean;
  max_swaps_per_day: number;
}

/** `?book=` suffix for GET endpoints (authFetch takes a raw path string). */
function bookQuery(book?: BookName) {
  return book ? `?book=${encodeURIComponent(book)}` : '';
}

export function fetchBooks() {
  return authFetch<{ default: BookName; books: BookInfo[] }>('/portfolio/books');
}

export function fetchBookComparison() {
  return authFetch('/portfolio/compare');
}

export function resetPortfolio(book?: BookName) {
  return authFetch('/portfolio/reset', {
    method: 'POST',
    body: JSON.stringify({ book: book ?? null }),
  });
}

export async function recalculatePortfolio(
  minDate?: string,
  positionPct?: number,
  book?: BookName,
): Promise<Record<string, unknown>> {
  const { job_id } = await authFetch<{ job_id: string }>('/portfolio/recalculate', {
    method: 'POST',
    body: JSON.stringify({
      min_date: minDate ?? null,
      position_pct: positionPct ?? null,
      book: book ?? null,
    }),
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

export function fetchPortfolio(book?: BookName) {
  return authFetch(`/portfolio${bookQuery(book)}`);
}

export function refreshPortfolio(book?: BookName) {
  return authFetch('/portfolio/refresh', {
    method: 'POST',
    body: JSON.stringify({ book: book ?? null }),
  });
}

export function fetchSkipped(book?: BookName) {
  return authFetch(`/portfolio/skipped${bookQuery(book)}`);
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

export function fetchSwapSuggestions(book?: BookName) {
  return authFetch(`/portfolio/swap-suggestions${bookQuery(book)}`);
}

export function suggestSwaps(book?: BookName) {
  return authFetch('/portfolio/suggest-swaps', {
    method: 'POST',
    body: JSON.stringify({ book: book ?? null }),
  });
}

export function executeSwap(close_symbol: string, open_symbol: string, book?: BookName) {
  return authFetch('/portfolio/execute-swap', {
    method: 'POST',
    body: JSON.stringify({ close_symbol, open_symbol, book: book ?? null }),
  });
}

export function undoSwap(book?: BookName) {
  return authFetch('/portfolio/undo-swap', {
    method: 'POST',
    body: JSON.stringify({ book: book ?? null }),
  });
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
