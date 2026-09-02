import { DarkTheme, ThemeProvider } from '@react-navigation/native';
import { useFonts } from 'expo-font';
import { Stack, router } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { useEffect } from 'react';
import 'react-native-reanimated';
import { configure, saveToken } from '../lib/api';
import { BookProvider } from '../lib/book';

export { ErrorBoundary } from 'expo-router';

export const unstable_settings = {
  initialRouteName: 'login',
};

SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const [loaded, error] = useFonts({
    SpaceMono: require('../assets/fonts/SpaceMono-Regular.ttf'),
  });

  // Required once at app startup before any API call (see trading_api_kit
  // client.ts) — was never actually wired up, so any browser/device without a
  // previously-cached api_url in storage failed every auth call with a
  // generic "baseUrl not configured" error (surfaced to users as e.g.
  // "Failed to authenticate with Google"). Must be a useEffect, not
  // module-scope: AsyncStorage's web backend touches `window`, which doesn't
  // exist during `expo export`'s Node-based static rendering pass.
  useEffect(() => {
    configure({ baseUrl: 'https://gilhadas-stocks.com' });
  }, []);

  useEffect(() => {
    if (error) throw error;
  }, [error]);

  useEffect(() => {
    if (loaded) SplashScreen.hideAsync();
  }, [loaded]);

  // Web OAuth redirect: Google sends back to /#token=... — catch it here before any route mounts
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ''));
    const queryParams = new URLSearchParams(window.location.search);
    const urlToken = hashParams.get('token') || queryParams.get('token');
    if (urlToken) {
      window.history.replaceState({}, '', '/');
      saveToken(urlToken).then(() => router.replace('/(tabs)'));
    }
  }, []);

  if (!loaded) return null;

  return (
    <ThemeProvider value={DarkTheme}>
      <BookProvider>
        <Stack screenOptions={{ headerShown: false }}>
          <Stack.Screen name="login" />
          <Stack.Screen name="(tabs)" />
          <Stack.Screen
            name="buy"
            options={{
              headerShown: true,
              title: 'Add Position',
              headerStyle: { backgroundColor: '#0f0f23' },
              headerTintColor: '#fff',
              presentation: 'modal',
            }}
          />
        </Stack>
      </BookProvider>
    </ThemeProvider>
  );
}
