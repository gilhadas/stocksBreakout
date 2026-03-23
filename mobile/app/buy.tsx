import React, { useState } from 'react';
import {
  View, Text, TextInput, StyleSheet, ScrollView,
  Pressable, ActivityIndicator, KeyboardAvoidingView, Platform,
} from 'react-native';
import { router } from 'expo-router';
import { buyPosition } from '../lib/api';

export default function BuyScreen() {
  const [symbol,     setSymbol]     = useState('');
  const [shares,     setShares]     = useState('');
  const [entryPrice, setEntryPrice] = useState('');
  const [stop,       setStop]       = useState('');
  const [target,     setTarget]     = useState('');
  const [sector,     setSector]     = useState('');
  const [broker,     setBroker]     = useState('');
  const [mode,       setMode]       = useState('swing');
  const [loading,    setLoading]    = useState(false);
  const [error,      setError]      = useState('');

  const handleBuy = async () => {
    if (!symbol.trim() || !shares || !entryPrice) {
      setError('Symbol, shares and entry price are required');
      return;
    }
    setLoading(true);
    setError('');
    try {
      await buyPosition({
        symbol:      symbol.trim().toUpperCase(),
        shares:      parseFloat(shares),
        entry_price: parseFloat(entryPrice),
        stop:        stop   ? parseFloat(stop)   : 0,
        target:      target ? parseFloat(target) : 0,
        sector:      sector.trim(),
        broker:      broker.trim(),
        mode,
      });
      router.back();
    } catch (e: any) {
      setError(e.message || 'Failed to add position');
    }
    setLoading(false);
  };

  const MODES = ['swing', 'daytrade', 'longterm', 'scalping'];

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView style={styles.container} contentContainerStyle={styles.content}>
        <Text style={styles.heading}>Add Position</Text>

        <Field label="Symbol *" value={symbol} onChangeText={t => setSymbol(t.toUpperCase())}
          placeholder="e.g. AAPL" autoCapitalize="characters" />
        <Field label="Shares *" value={shares} onChangeText={setShares}
          placeholder="e.g. 100" keyboardType="decimal-pad" />
        <Field label="Entry Price *" value={entryPrice} onChangeText={setEntryPrice}
          placeholder="e.g. 185.50" keyboardType="decimal-pad" />
        <Field label="Stop Loss" value={stop} onChangeText={setStop}
          placeholder="e.g. 178.00" keyboardType="decimal-pad" />
        <Field label="Target" value={target} onChangeText={setTarget}
          placeholder="e.g. 210.00" keyboardType="decimal-pad" />
        <Field label="Sector" value={sector} onChangeText={setSector}
          placeholder="e.g. Technology" />
        <Field label="Broker" value={broker} onChangeText={setBroker}
          placeholder="e.g. IBKR" />

        <Text style={styles.label}>Mode</Text>
        <View style={styles.modeRow}>
          {MODES.map(m => (
            <Pressable
              key={m}
              style={[styles.modeBtn, mode === m && styles.modeBtnActive]}
              onPress={() => setMode(m)}
            >
              <Text style={[styles.modeBtnText, mode === m && styles.modeBtnTextActive]}>
                {m}
              </Text>
            </Pressable>
          ))}
        </View>

        {error ? <Text style={styles.error}>{error}</Text> : null}

        {shares && entryPrice ? (
          <Text style={styles.costPreview}>
            Cost basis: ${(parseFloat(shares || '0') * parseFloat(entryPrice || '0')).toFixed(2)}
          </Text>
        ) : null}

        <Pressable
          style={[styles.buyBtn, loading && { opacity: 0.6 }]}
          onPress={handleBuy}
          disabled={loading}
        >
          {loading
            ? <ActivityIndicator color="#fff" />
            : <Text style={styles.buyBtnText}>Add Position</Text>
          }
        </Pressable>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function Field({
  label, value, onChangeText, placeholder, keyboardType, autoCapitalize,
}: {
  label: string;
  value: string;
  onChangeText: (t: string) => void;
  placeholder?: string;
  keyboardType?: any;
  autoCapitalize?: any;
}) {
  return (
    <View style={styles.fieldWrap}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        style={styles.input}
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor="#444"
        keyboardType={keyboardType || 'default'}
        autoCapitalize={autoCapitalize || 'none'}
        autoCorrect={false}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  flex:          { flex: 1, backgroundColor: '#0f0f23' },
  container:     { flex: 1, backgroundColor: '#0f0f23' },
  content:       { padding: 16, paddingBottom: 40 },
  heading:       { color: '#fff', fontSize: 20, fontWeight: '700', marginBottom: 20 },

  fieldWrap:     { marginBottom: 14 },
  label:         { color: '#888', fontSize: 12, marginBottom: 4 },
  input: {
    backgroundColor: '#16213e', color: '#fff', borderRadius: 8,
    padding: 12, fontSize: 15, borderWidth: 1, borderColor: '#2a2a4a',
  },

  modeRow:       { flexDirection: 'row', gap: 8, flexWrap: 'wrap', marginBottom: 14 },
  modeBtn:       { paddingHorizontal: 14, paddingVertical: 7, borderRadius: 7, backgroundColor: '#16213e', borderWidth: 1, borderColor: '#2a2a4a' },
  modeBtnActive: { backgroundColor: '#6366f1', borderColor: '#6366f1' },
  modeBtnText:   { color: '#888', fontSize: 13, fontWeight: '600' },
  modeBtnTextActive: { color: '#fff' },

  costPreview:   { color: '#888', fontSize: 13, textAlign: 'center', marginBottom: 12 },
  error:         { color: '#ef4444', textAlign: 'center', marginBottom: 12, fontSize: 13 },
  buyBtn: {
    backgroundColor: '#22c55e', borderRadius: 10, padding: 14,
    alignItems: 'center', marginTop: 8,
  },
  buyBtnText:    { color: '#fff', fontSize: 16, fontWeight: '700' },
});
