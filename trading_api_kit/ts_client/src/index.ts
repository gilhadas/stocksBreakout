/**
 * trading-api-kit — public TypeScript API.
 *
 * Usage:
 *   import { configure, loginWithEmail, authFetch, registerForPushNotifications }
 *     from 'trading-api-kit';
 *
 *   // 1. Configure once at startup
 *   await configure({ baseUrl: 'https://my-scanner.example.com' });
 *
 *   // 2. Login
 *   await loginWithEmail('user@example.com', 'password');
 *
 *   // 3. Call your custom endpoints
 *   const portfolio = await authFetch('/portfolio');
 *
 *   // 4. Register push notifications
 *   await registerForPushNotifications();
 */

// Core client
export {
  configure,
  getBaseUrl,
  getToken,
  saveToken,
  clearToken,
  getEmailFromToken,
  isLoggedIn,
  authFetch,
  SessionExpiredError,
} from './client';

// Auth
export {
  loginWithEmail,
  loginWithPassword,
  logout,
  getCurrentUser,
  getGoogleAuthUrl,
} from './auth';
export type { LoginResponse, UserInfo } from './auth';

// Push notifications
export { registerForPushNotifications, registerPushToken } from './notifications';
