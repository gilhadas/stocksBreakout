/**
 * trading-api-kit — Expo push notification registration.
 *
 * Requests permission, gets the Expo push token, and registers it
 * with the server's /push/register endpoint.
 */

import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';
import { authFetch } from './client';

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

/**
 * Request push notification permission and register the device token
 * with the server. Returns the token string or null if unsupported/denied.
 *
 * @example
 *   useEffect(() => { registerForPushNotifications(); }, []);
 */
export async function registerForPushNotifications(): Promise<string | null> {
  if (Platform.OS === 'web') return null; // Web doesn't support Expo push

  const { status: existing } = await Notifications.getPermissionsAsync();
  let finalStatus = existing;

  if (existing !== 'granted') {
    const { status } = await Notifications.requestPermissionsAsync();
    finalStatus = status;
  }

  if (finalStatus !== 'granted') return null;

  const { data: token } = await Notifications.getExpoPushTokenAsync();

  try {
    await registerPushToken(token);
  } catch (e) {
    console.warn('[trading-api-kit] Push token registration failed:', e);
  }

  return token;
}

/** Register a raw Expo push token with the server. */
export async function registerPushToken(token: string): Promise<void> {
  await authFetch('/push/register', {
    method: 'POST',
    body: JSON.stringify({ token }),
  });
}
