import React, { useState, useEffect } from 'react';
import { View, Text, TextInput, Pressable, StyleSheet, ActivityIndicator } from 'react-native';
import { router } from 'expo-router';
import { login, getToken } from '../lib/api';

export default function LoginScreen() {
  const [apiUrl, setApiUrl] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  // Auto-redirect if already logged in
  useEffect(() => {
    getToken().then((t) => {
      if (t) router.replace('/(tabs)');
      else setLoading(false);
    });
  }, []);

  const handleLogin = async () => {
    setError('');
    setLoading(true);
    try {
      await login(password, apiUrl || undefined);
      router.replace('/(tabs)');
    } catch {
      setError('Login failed — check password and server URL');
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
    <View style={styles.container}>
      <Text style={styles.title}>StocksBreakout</Text>
      <Text style={styles.subtitle}>Portfolio Tracker</Text>

      <TextInput
        style={styles.input}
        placeholder="API URL (e.g. https://your-tunnel.trycloudflare.com)"
        placeholderTextColor="#555"
        value={apiUrl}
        onChangeText={setApiUrl}
        autoCapitalize="none"
        autoCorrect={false}
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

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Pressable style={styles.button} onPress={handleLogin}>
        <Text style={styles.buttonText}>Login</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f0f23', justifyContent: 'center', padding: 24 },
  title: { color: '#fff', fontSize: 28, fontWeight: '700', textAlign: 'center' },
  subtitle: { color: '#888', fontSize: 14, textAlign: 'center', marginBottom: 32 },
  input: {
    backgroundColor: '#1a1a2e', color: '#fff', borderRadius: 8,
    padding: 14, fontSize: 15, marginBottom: 12, borderWidth: 1, borderColor: '#333',
  },
  error: { color: '#ef4444', textAlign: 'center', marginBottom: 8 },
  button: {
    backgroundColor: '#6366f1', borderRadius: 8, padding: 14, alignItems: 'center', marginTop: 8,
  },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
});
