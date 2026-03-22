import React, { useState, useCallback } from 'react';
import { View, FlatList, Text, StyleSheet, RefreshControl } from 'react-native';
import { useFocusEffect, router } from 'expo-router';
import { fetchPortfolio, getToken } from '../../lib/api';

export default function HistoryScreen() {
  const [closed, setClosed] = useState<any[]>([]);
  const [stats, setStats] = useState({ realized: 0, winRate: '0', avgHold: 0 });
  const [refreshing, setRefreshing] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const token = await getToken();
      if (!token) { router.replace('/login'); return; }
      const data = await fetchPortfolio();
      const trades = (data.closed || []).sort(
        (a: any, b: any) => (b.date_closed || '').localeCompare(a.date_closed || '')
      );
      setClosed(trades);

      const wins = trades.filter((t: any) => (t.pnl || 0) > 0).length;
      const realized = trades.reduce((s: number, t: any) => s + (t.pnl || 0), 0);
      const avgHold = trades.length > 0
        ? trades.reduce((s: number, t: any) => s + (t.hold_days || 0), 0) / trades.length
        : 0;
      setStats({
        realized,
        winRate: trades.length > 0 ? ((wins / trades.length) * 100).toFixed(1) : '0',
        avgHold: Math.round(avgHold),
      });
    } catch (e: any) {
      if (e.message === 'Session expired') router.replace('/login');
    }
  }, []);

  useFocusEffect(
    useCallback(() => { loadData(); }, [loadData])
  );

  const handleRefresh = async () => {
    setRefreshing(true);
    await loadData();
    setRefreshing(false);
  };

  return (
    <View style={styles.container}>
      <View style={styles.statsBar}>
        <StatBox label="Realized" value={`$${stats.realized.toFixed(0)}`}
          color={stats.realized >= 0 ? '#22c55e' : '#ef4444'} />
        <StatBox label="Win Rate" value={`${stats.winRate}%`} />
        <StatBox label="Avg Hold" value={`${stats.avgHold}d`} />
      </View>

      <FlatList
        data={closed}
        keyExtractor={(item, i) => `${item.symbol}-${i}`}
        renderItem={({ item }) => <TradeRow trade={item} />}
        ListEmptyComponent={<Text style={styles.empty}>No closed trades</Text>}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={handleRefresh} tintColor="#6366f1" />
        }
      />
    </View>
  );
}

function StatBox({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={[styles.statValue, color ? { color } : null]}>{value}</Text>
    </View>
  );
}

function TradeRow({ trade }: { trade: any }) {
  const pnl = trade.pnl || 0;
  const pnlColor = pnl >= 0 ? '#22c55e' : '#ef4444';
  const reason = trade.close_reason || trade.reason || '';

  return (
    <View style={styles.tradeCard}>
      <View style={styles.tradeHeader}>
        <Text style={styles.tradeSymbol}>{trade.symbol}</Text>
        <Text style={[styles.tradePnl, { color: pnlColor }]}>
          {pnl >= 0 ? '+' : ''}${pnl.toFixed(0)}
        </Text>
      </View>
      <View style={styles.tradeDetails}>
        <Text style={styles.tradeDetail}>
          ${trade.entry_price?.toFixed(2)} → ${trade.exit_price?.toFixed(2)}
        </Text>
        <Text style={styles.tradeDetail}>{trade.hold_days || 0}d</Text>
        {reason ? <Text style={styles.reasonBadge}>{reason}</Text> : null}
      </View>
      {trade.date_closed && (
        <Text style={styles.tradeDate}>{trade.date_closed}</Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f0f23' },
  statsBar: {
    flexDirection: 'row', justifyContent: 'space-around',
    backgroundColor: '#1a1a2e', padding: 12, margin: 12, borderRadius: 8,
  },
  stat: { alignItems: 'center' },
  statLabel: { color: '#888', fontSize: 11 },
  statValue: { color: '#fff', fontSize: 16, fontWeight: '600' },
  empty: { color: '#555', textAlign: 'center', padding: 32, fontSize: 15 },
  tradeCard: { backgroundColor: '#16213e', padding: 10, borderRadius: 8, marginHorizontal: 12, marginBottom: 6 },
  tradeHeader: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 },
  tradeSymbol: { color: '#fff', fontSize: 15, fontWeight: '700' },
  tradePnl: { fontSize: 14, fontWeight: '600' },
  tradeDetails: { flexDirection: 'row', gap: 12, alignItems: 'center' },
  tradeDetail: { color: '#888', fontSize: 12 },
  reasonBadge: { color: '#fbbf24', fontSize: 10, fontWeight: '600' },
  tradeDate: { color: '#444', fontSize: 10, marginTop: 4 },
});
