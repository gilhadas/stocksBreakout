import React, { useState, useEffect } from 'react';
import { View, Text, TextInput, Pressable, StyleSheet, ActivityIndicator, ScrollView } from 'react-native';
import * as WebBrowser from 'expo-web-browser';
import { router } from 'expo-router';
import { loginWithEmail, getToken, getGoogleAuthUrl, saveToken } from '../lib/api';

export default function LoginScreen() {
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getToken().then((t) => {
      if (t) router.replace('/(tabs)');
      else setLoading(false);
    });
  }, []);

  const handleEmailLogin = async () => {
    setError('');
    if (!email || !password) {
      setError('Email and password required');
      return;
    }
    setLoading(true);
    try {
      await loginWithEmail(email, password);
      router.replace('/(tabs)');
    } catch {
      setError('Invalid credentials');
      setLoading(false);
    }
  };

  const handleGoogleLogin = async () => {
    try {
      setLoading(true);
      const url = await getGoogleAuthUrl();

      const { Platform } = require('react-native');
      if (Platform.OS === 'web') {
        // Web: full-page redirect — API sends back to /?token= which useEffect catches
        window.location.href = url;
        return;
      }

      // Native: openAuthSessionAsync intercepts the stocksbreakout:// custom scheme
      const result = await WebBrowser.openAuthSessionAsync(url, 'stocksbreakout://oauth-callback');
      if (result.type === 'success' && result.url) {
        const urlObj = new URL(result.url);
        const token = urlObj.searchParams.get('token');
        if (token) {
          await saveToken(token);
          router.replace('/(tabs)');
          return;
        }
      }
      setError('Google login cancelled or failed');
    } catch (e) {
      setError('Failed to authenticate with Google');
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <View style={styles.container}>
        <ActivityIndicator size="large" color="#6366f1" />
      </View>
    );
  }

  return (
    <ScrollView style={styles.container}>
      <Text style={styles.title}>StocksBreakout</Text>
      <Text style={styles.subtitle}>Portfolio Tracker</Text>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <TextInput
        style={styles.input}
        placeholder="Email"
        placeholderTextColor="#555"
        value={email}
        onChangeText={setEmail}
        autoCapitalize="none"
        keyboardType="email-address"
      />
      <TextInput
        style={styles.input}
        placeholder="Password"
        placeholderTextColor="#555"
        value={password}
        onChangeText={setPassword}
        secureTextEntry
        autoCapitalize="none"
      />
      <Pressable style={styles.button} onPress={handleEmailLogin} disabled={loading}>
        <Text style={styles.buttonText}>Login</Text>
      </Pressable>

      <Text style={styles.divider}>or</Text>

      <Pressable style={styles.button} onPress={handleGoogleLogin} disabled={loading}>
        <Text style={styles.buttonText}>🔐 Login with Google</Text>
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flexGrow: 1, backgroundColor: '#0f0f23', padding: 24, paddingTop: 40 },
  title: { color: '#fff', fontSize: 28, fontWeight: '700', textAlign: 'center', marginBottom: 4 },
  subtitle: { color: '#888', fontSize: 14, textAlign: 'center', marginBottom: 24 },
  error: { color: '#ef4444', textAlign: 'center', marginBottom: 12, fontSize: 14 },
  input: {
    backgroundColor: '#1a1a2e', color: '#fff', borderRadius: 8,
    padding: 14, fontSize: 15, marginBottom: 12, borderWidth: 1, borderColor: '#333',
  },
  button: {
    backgroundColor: '#6366f1', borderRadius: 8, padding: 14, alignItems: 'center', marginBottom: 12,
  },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
  divider: { color: '#555', fontSize: 14, textAlign: 'center', marginVertical: 16, fontWeight: '500' },
});
