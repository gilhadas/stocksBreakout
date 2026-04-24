import React, { useState, useEffect } from 'react';
import { View, Text, TextInput, Pressable, StyleSheet, ActivityIndicator, ScrollView } from 'react-native';
import * as WebBrowser from 'expo-web-browser';
import { router } from 'expo-router';
import { loginWithEmail, getToken, getGoogleAuthUrl, saveToken } from '../lib/api';

export default function LoginScreen() {
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const [token, setToken] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<'email' | 'token'>('email');

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
      const url = await getGoogleAuthUrl();
      await WebBrowser.openBrowserAsync(url);
      setTab('token');
      setError('After logging in with Google, paste the token below');
    } catch {
      setError('Failed to open browser');
    }
  };

  const handleTokenSubmit = async () => {
    setError('');
    if (!token.trim()) {
      setError('Token required');
      return;
    }
    try {
      await saveToken(token.trim());
      router.replace('/(tabs)');
    } catch {
      setError('Invalid token');
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

      <View style={styles.tabContainer}>
        <Pressable
          style={[styles.tab, tab === 'email' && styles.tabActive]}
          onPress={() => setTab('email')}
        >
          <Text style={styles.tabText}>Email</Text>
        </Pressable>
        <Pressable
          style={[styles.tab, tab === 'token' && styles.tabActive]}
          onPress={() => setTab('token')}
        >
          <Text style={styles.tabText}>Token</Text>
        </Pressable>
      </View>

      {tab === 'email' && (
        <>
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
          <Pressable style={styles.button} onPress={handleEmailLogin}>
            <Text style={styles.buttonText}>Login</Text>
          </Pressable>
        </>
      )}

      {tab === 'token' && (
        <>
          <Text style={styles.tokenHint}>1. Tap "Google Login" to open browser</Text>
          <Text style={styles.tokenHint}>2. After authenticating, copy the token from the URL</Text>
          <Text style={styles.tokenHint}>3. Paste it below</Text>
          <Pressable style={styles.button} onPress={handleGoogleLogin}>
            <Text style={styles.buttonText}>🔑 Open Google Login</Text>
          </Pressable>
          <TextInput
            style={styles.input}
            placeholder="Paste token here"
            placeholderTextColor="#555"
            value={token}
            onChangeText={setToken}
            autoCapitalize="none"
            multiline
          />
          <Pressable style={styles.button} onPress={handleTokenSubmit}>
            <Text style={styles.buttonText}>Login with Token</Text>
          </Pressable>
        </>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flexGrow: 1, backgroundColor: '#0f0f23', padding: 24, paddingTop: 40 },
  title: { color: '#fff', fontSize: 28, fontWeight: '700', textAlign: 'center', marginBottom: 4 },
  subtitle: { color: '#888', fontSize: 14, textAlign: 'center', marginBottom: 24 },
  error: { color: '#ef4444', textAlign: 'center', marginBottom: 12, fontSize: 14 },
  tabContainer: { flexDirection: 'row', marginBottom: 20, gap: 8 },
  tab: {
    flex: 1, backgroundColor: '#1a1a2e', borderRadius: 6, padding: 10, alignItems: 'center',
    borderWidth: 1, borderColor: '#333',
  },
  tabActive: { borderColor: '#6366f1', backgroundColor: '#6366f1' },
  tabText: { color: '#fff', fontSize: 13, fontWeight: '600' },
  input: {
    backgroundColor: '#1a1a2e', color: '#fff', borderRadius: 8,
    padding: 14, fontSize: 15, marginBottom: 12, borderWidth: 1, borderColor: '#333',
  },
  button: {
    backgroundColor: '#6366f1', borderRadius: 8, padding: 14, alignItems: 'center', marginBottom: 12,
  },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
  tokenHint: { color: '#888', fontSize: 13, marginBottom: 8 },
});
