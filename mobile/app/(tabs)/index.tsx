import React, { useState, useCallback } from 'react';
import { View, FlatList, Text, StyleSheet, RefreshControl, Pressable } from 'react-native';
import { useFocusEffect, router } from 'expo-router';
import { fetchPortfolio, refreshPortfolio, getToken, clearToken } from '../../lib/api';
import SummaryBar from '../../components/SummaryBar';
import PositionCard from '../../components/PositionCard';

function StatBox({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={[styles.statValue, color ? { color } : null]}>{value}</Text>
    </View>
  );
}

const MODE_COLORS: Record<string, string> = {
  longterm: '#818cf8', swing: '#34d399', daytrade: '#fb923c', scalping: '#f472b6',
};

function TradeRow({ trade }: { trade: any }) {
  const pnl = trade.pnl || 0;
  const pnlColor = pnl >= 0 ? '#22c55e' : '#ef4444';
  const reason = trade.close_reason || trade.reason || '';
  const modeColor = trade.mode ? (MODE_COLORS[trade.mode] ?? '#888') : null;
  return (
    <View style={styles.tradeCard}>
      <View style={styles.tradeHeader}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <Text style={styles.tradeSymbol}>{trade.symbol}</Text>
          {modeColor && (
            <Text style={[styles.tradeModeBadge, { color: modeColor, borderColor: modeColor }]}>
              {trade.mode.toUpperCase()}
            </Text>
          )}
        </View>
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
      {trade.date_closed && <Text style={styles.tradeDate}>{trade.date_closed}</Text>}
    </View>
  );
}

export default function PortfolioScreen() {
  const [activeTab, setActiveTab] = useState<'positions' | 'history'>('positions');
  const [positions, setPositions] = useState<any[]>([]);
  const [closed, setClosed] = useState<any[]>([]);
  const [summary, setSummary] = useState<any>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState('');

  const loadData = useCallback(async () => {
    try {
      const token = await getToken();
      if (!token) { router.replace('/login'); return; }
      const data = await fetchPortfolio();
      setPositions(data.positions || []);
      setClosed(
        (data.closed || []).sort((a: any, b: any) =>
          (b.date_closed || '').localeCompare(a.date_closed || '')
        )
      );
      setSummary(data.summary);
      setLastUpdated(data.last_updated || '');
      setError('');
    } catch (e: any) {
      if (e.message === 'Session expired') router.replace('/login');
      else setError(e.message);
    }
  }, []);

  useFocusEffect(useCallback(() => { loadData(); }, [loadData]));

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      const data = await refreshPortfolio();
      setPositions(data.positions || []);
      setClosed(
        (data.closed || []).sort((a: any, b: any) =>
          (b.date_closed || '').localeCompare(a.date_closed || '')
        )
      );
      setSummary(data.summary);
      setLastUpdated(data.last_updated || '');
    } catch (e: any) {
      setError(e.message);
    }
    setRefreshing(false);
  };

  const wins = closed.filter(t => (t.pnl || 0) > 0).length;
  const realized = closed.reduce((s, t) => s + (t.pnl || 0), 0);
  const avgHold = closed.length > 0
    ? Math.round(closed.reduce((s, t) => s + (t.hold_days || 0), 0) / closed.length)
    : 0;
  const winRate = closed.length > 0 ? ((wins / closed.length) * 100).toFixed(1) : '0';

  return (
    <View style={styles.container}>
      {summary && <SummaryBar summary={summary} />}

      {/* Sub-tab switcher */}
      <View style={styles.tabRow}>
        <Pressable
          style={[styles.tabBtn, activeTab === 'positions' && styles.tabBtnActive]}
          onPress={() => setActiveTab('positions')}
        >
          <Text style={[styles.tabBtnText, activeTab === 'positions' && styles.tabBtnTextActive]}>
            Positions ({positions.length})
          </Text>
        </Pressable>
        <Pressable
          style={[styles.tabBtn, activeTab === 'history' && styles.tabBtnActive]}
          onPress={() => setActiveTab('history')}
        >
          <Text style={[styles.tabBtnText, activeTab === 'history' && styles.tabBtnTextActive]}>
            History ({closed.length})
          </Text>
        </Pressable>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      {activeTab === 'positions' ? (
        <FlatList
          data={positions}
          keyExtractor={(item) => item.symbol}
          renderItem={({ item }) => <PositionCard pos={item} />}
          ListEmptyComponent={<Text style={styles.empty}>No open positions</Text>}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={handleRefresh} tintColor="#6366f1" />
          }
          ListFooterComponent={
            <View style={styles.footer}>
              {lastUpdated ? <Text style={styles.updated}>Updated: {lastUpdated}</Text> : null}
              <Pressable onPress={async () => { await clearToken(); router.replace('/login'); }}>
                <Text style={styles.logout}>Logout</Text>
              </Pressable>
            </View>
          }
        />
      ) : (
        <FlatList
          data={closed}
          keyExtractor={(item, i) => `${item.symbol}-${i}`}
          renderItem={({ item }) => <TradeRow trade={item} />}
          ListEmptyComponent={<Text style={styles.empty}>No closed trades</Text>}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={handleRefresh} tintColor="#6366f1" />
          }
          ListHeaderComponent={
            <View style={styles.statsBar}>
              <StatBox label="Realized" value={`$${realized.toFixed(0)}`}
                color={realized >= 0 ? '#22c55e' : '#ef4444'} />
              <StatBox label="Win Rate" value={`${winRate}%`} />
              <StatBox label="Avg Hold" value={`${avgHold}d`} />
            </View>
          }
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f0f23' },
  error: { color: '#ef4444', textAlign: 'center', padding: 8 },
  empty: { color: '#555', textAlign: 'center', padding: 32, fontSize: 15 },
  footer: { alignItems: 'center', padding: 16, gap: 12 },
  updated: { color: '#555', fontSize: 11 },
  logout: { color: '#ef4444', fontSize: 13 },

  tabRow: {
    flexDirection: 'row', backgroundColor: '#1a1a2e',
    padding: 6, gap: 6, marginHorizontal: 12, marginVertical: 8, borderRadius: 10,
  },
  tabBtn: {
    flex: 1, paddingVertical: 7, borderRadius: 7, alignItems: 'center',
  },
  tabBtnActive: { backgroundColor: '#6366f1' },
  tabBtnText: { color: '#888', fontSize: 13, fontWeight: '600' },
  tabBtnTextActive: { color: '#fff' },

  statsBar: {
    flexDirection: 'row', justifyContent: 'space-around',
    backgroundColor: '#1a1a2e', padding: 12, margin: 12, borderRadius: 8,
  },
  stat: { alignItems: 'center' },
  statLabel: { color: '#888', fontSize: 11 },
  statValue: { color: '#fff', fontSize: 16, fontWeight: '600' },

  tradeCard: {
    backgroundColor: '#16213e', padding: 10, borderRadius: 8,
    marginHorizontal: 12, marginBottom: 6,
  },
  tradeHeader: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 },
  tradeSymbol: { color: '#fff', fontSize: 15, fontWeight: '700' },
  tradePnl: { fontSize: 14, fontWeight: '600' },
  tradeDetails: { flexDirection: 'row', gap: 12, alignItems: 'center' },
  tradeDetail: { color: '#888', fontSize: 12 },
  reasonBadge: { color: '#fbbf24', fontSize: 10, fontWeight: '600' },
  tradeModeBadge: { fontSize: 9, fontWeight: '700', borderWidth: 1, borderRadius: 3, paddingHorizontal: 4, paddingVertical: 1 },
  tradeDate: { color: '#444', fontSize: 10, marginTop: 4 },
});
