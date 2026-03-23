import React, { useState, useCallback } from 'react';
import { View, FlatList, Text, StyleSheet, RefreshControl, Pressable } from 'react-native';
import { useFocusEffect, router } from 'expo-router';
import { fetchManualPortfolio, computeStops, getToken } from '../../lib/api';

const STATUS_COLOR: Record<string, string> = {
  SELL:    '#ef4444',
  CAUTION: '#f97316',
  HOLD:    '#22c55e',
  TARGET:  '#6366f1',
  UNKNOWN: '#555',
};

const STATUS_LABEL: Record<string, string> = {
  SELL:    'SELL',
  CAUTION: 'CAUTION',
  HOLD:    'HOLD',
  TARGET:  'TARGET ✓',
  UNKNOWN: '—',
};

function PositionRow({ pos }: { pos: any }) {
  const status   = pos.status || 'UNKNOWN';
  const color    = STATUS_COLOR[status] || '#555';
  const pnlColor = (pos.pnl_pct ?? 0) >= 0 ? '#22c55e' : '#ef4444';
  const price    = pos.current_price;

  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Text style={styles.symbol}>{pos.symbol}</Text>
        <View style={[styles.badge, { backgroundColor: color + '22', borderColor: color }]}>
          <Text style={[styles.badgeText, { color }]}>{STATUS_LABEL[status]}</Text>
        </View>
      </View>

      <View style={styles.row}>
        <View style={styles.col}>
          <Text style={styles.label}>Entry</Text>
          <Text style={styles.value}>${pos.entry_price?.toFixed(2) ?? '—'}</Text>
        </View>
        <View style={styles.col}>
          <Text style={styles.label}>Current</Text>
          <Text style={styles.value}>{price ? `$${price.toFixed(2)}` : '—'}</Text>
        </View>
        <View style={styles.col}>
          <Text style={styles.label}>P&L %</Text>
          <Text style={[styles.value, { color: pnlColor }]}>
            {pos.pnl_pct != null ? `${pos.pnl_pct > 0 ? '+' : ''}${pos.pnl_pct.toFixed(1)}%` : '—'}
          </Text>
        </View>
      </View>

      <View style={styles.row}>
        <View style={styles.col}>
          <Text style={styles.label}>Stop</Text>
          <Text style={[styles.value, { color: '#ef4444' }]}>${pos.stop?.toFixed(2) ?? '—'}</Text>
        </View>
        <View style={styles.col}>
          <Text style={styles.label}>Target</Text>
          <Text style={[styles.value, { color: '#22c55e' }]}>${pos.target?.toFixed(2) ?? '—'}</Text>
        </View>
        <View style={styles.col}>
          <Text style={styles.label}>Shares</Text>
          <Text style={styles.value}>{pos.shares ?? '—'}</Text>
        </View>
      </View>

      <View style={styles.row}>
        {pos.rr != null && (
          <View style={styles.col}>
            <Text style={styles.label}>R/R</Text>
            <Text style={styles.value}>{pos.rr.toFixed(2)}</Text>
          </View>
        )}
        {pos.sector && (
          <View style={styles.col}>
            <Text style={styles.label}>Sector</Text>
            <Text style={styles.value}>{pos.sector}</Text>
          </View>
        )}
        {pos.quality && (
          <View style={styles.col}>
            <Text style={styles.label}>Quality</Text>
            <Text style={styles.value}>{pos.quality}</Text>
          </View>
        )}
      </View>
      {(pos.entry_date || pos.date_added) ? (
        <Text style={styles.date}>Added: {pos.entry_date || pos.date_added}</Text>
      ) : null}
    </View>
  );
}

export default function ManualPortfolioScreen() {
  const [positions, setPositions] = useState<any[]>([]);
  const [cash, setCash]           = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError]         = useState('');
  const [lastUpdated, setLastUpdated] = useState('');
  const [computing, setComputing]     = useState(false);
  const [computeMsg, setComputeMsg]   = useState('');

  const handleComputeStops = async () => {
    setComputing(true);
    setComputeMsg('');
    try {
      const result = await computeStops();
      setComputeMsg(`Stops computed for ${result.updated} positions`);
      await loadData();  // reload with new stops
    } catch (e: any) {
      setComputeMsg(`Error: ${e.message}`);
    }
    setComputing(false);
  };

  const loadData = useCallback(async () => {
    try {
      const token = await getToken();
      if (!token) { router.replace('/login'); return; }
      const data = await fetchManualPortfolio();
      setPositions(data.positions || []);
      setCash(data.cash ?? null);
      setLastUpdated(data.last_updated || '');
      setError('');
    } catch (e: any) {
      if (e.message === 'Session expired') router.replace('/login');
      else setError(e.message);
    }
  }, []);

  useFocusEffect(useCallback(() => { loadData(); }, [loadData]));

  const onRefresh = async () => {
    setRefreshing(true);
    await loadData();
    setRefreshing(false);
  };

  // Count statuses for the summary bar
  const sellCount    = positions.filter(p => p.status === 'SELL').length;
  const cautionCount = positions.filter(p => p.status === 'CAUTION').length;
  const targetCount  = positions.filter(p => p.status === 'TARGET').length;

  return (
    <View style={styles.container}>
      {/* Summary bar */}
      <View style={styles.summary}>
        <View style={styles.summaryItem}>
          <Text style={styles.summaryValue}>{positions.length}</Text>
          <Text style={styles.summaryLabel}>Positions</Text>
        </View>
        <View style={styles.summaryItem}>
          <Text style={[styles.summaryValue, { color: '#ef4444' }]}>{sellCount}</Text>
          <Text style={styles.summaryLabel}>Sell</Text>
        </View>
        <View style={styles.summaryItem}>
          <Text style={[styles.summaryValue, { color: '#f97316' }]}>{cautionCount}</Text>
          <Text style={styles.summaryLabel}>Caution</Text>
        </View>
        <View style={styles.summaryItem}>
          <Text style={[styles.summaryValue, { color: '#6366f1' }]}>{targetCount}</Text>
          <Text style={styles.summaryLabel}>Target</Text>
        </View>
        {cash !== null && (
          <View style={styles.summaryItem}>
            <Text style={styles.summaryValue}>${cash.toLocaleString()}</Text>
            <Text style={styles.summaryLabel}>Cash</Text>
          </View>
        )}
      </View>

      {/* Compute stops button */}
      <Pressable
        style={[styles.computeBtn, computing && { opacity: 0.6 }]}
        onPress={handleComputeStops}
        disabled={computing}
      >
        <Text style={styles.computeBtnText}>
          {computing ? 'Computing stops...' : 'Compute Stop Loss (ATR14 + Swing Low)'}
        </Text>
      </Pressable>
      {computeMsg ? <Text style={styles.computeMsg}>{computeMsg}</Text> : null}

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <FlatList
        data={positions}
        keyExtractor={(item, i) => item.symbol ?? String(i)}
        renderItem={({ item }) => <PositionRow pos={item} />}
        ListEmptyComponent={<Text style={styles.empty}>No manual positions</Text>}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#6366f1" />
        }
        ListFooterComponent={
          lastUpdated ? (
            <Text style={styles.updated}>Updated: {lastUpdated}</Text>
          ) : null
        }
        contentContainerStyle={{ paddingBottom: 24 }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container:    { flex: 1, backgroundColor: '#0f0f23' },
  summary:      { flexDirection: 'row', backgroundColor: '#1a1a2e', padding: 12, gap: 8, justifyContent: 'space-around' },
  summaryItem:  { alignItems: 'center' },
  summaryValue: { color: '#fff', fontSize: 18, fontWeight: '700' },
  summaryLabel: { color: '#888', fontSize: 11, marginTop: 2 },

  card:         { backgroundColor: '#16213e', margin: 8, borderRadius: 10, padding: 12, gap: 8 },
  cardHeader:   { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  symbol:       { color: '#fff', fontSize: 18, fontWeight: '700' },
  badge:        { borderRadius: 6, borderWidth: 1, paddingHorizontal: 8, paddingVertical: 3 },
  badgeText:    { fontSize: 11, fontWeight: '700' },

  row:          { flexDirection: 'row', gap: 8 },
  col:          { flex: 1 },
  label:        { color: '#888', fontSize: 10 },
  value:        { color: '#fff', fontSize: 14, fontWeight: '600', marginTop: 2 },

  date:         { color: '#555', fontSize: 10, marginTop: 4 },
  computeBtn:   { backgroundColor: '#1e3a5f', margin: 8, borderRadius: 8, padding: 12, alignItems: 'center', borderWidth: 1, borderColor: '#6366f1' },
  computeBtnText: { color: '#6366f1', fontSize: 13, fontWeight: '600' },
  computeMsg:   { color: '#22c55e', textAlign: 'center', fontSize: 12, marginBottom: 4 },
  error:        { color: '#ef4444', textAlign: 'center', padding: 8 },
  empty:        { color: '#555', textAlign: 'center', padding: 32, fontSize: 15 },
  updated:      { color: '#555', fontSize: 11, textAlign: 'center', padding: 16 },
});
