import React, { useState, useCallback } from 'react';
import { View, FlatList, Text, StyleSheet, RefreshControl, Pressable, Modal, TextInput, ActivityIndicator } from 'react-native';
import { useFocusEffect, router } from 'expo-router';
import { fetchManualPortfolio, computeStops, sellPosition, getToken } from '../../lib/api';
import SummaryBar from '../../components/SummaryBar';

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

function PositionRow({ pos, onSell }: { pos: any; onSell: () => void }) {
  const status   = pos.status || 'UNKNOWN';
  const color    = STATUS_COLOR[status] || '#555';
  const pnlColor = (pos.pnl_pct ?? 0) >= 0 ? '#22c55e' : '#ef4444';
  const price    = pos.current_price;

  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Text style={styles.symbol}>{pos.symbol}</Text>
        <View style={styles.cardHeaderRight}>
          <View style={[styles.badge, { backgroundColor: color + '22', borderColor: color }]}>
            <Text style={[styles.badgeText, { color }]}>{STATUS_LABEL[status]}</Text>
          </View>
          <Pressable style={styles.sellBtn} onPress={onSell}>
            <Text style={styles.sellBtnText}>Sell</Text>
          </Pressable>
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

function TradeRow({ trade }: { trade: any }) {
  const pnl = trade.pnl || trade.realized_pnl || 0;
  const pnlColor = pnl >= 0 ? '#22c55e' : '#ef4444';
  const reason = trade.close_reason || trade.reason || '';
  const entryPrice = trade.entry_price ?? trade.buy_price;
  const exitPrice  = trade.exit_price  ?? trade.sell_price;

  return (
    <View style={styles.tradeCard}>
      <View style={styles.tradeHeader}>
        <Text style={styles.tradeSymbol}>{trade.symbol}</Text>
        <Text style={[styles.tradePnl, { color: pnlColor }]}>
          {pnl >= 0 ? '+' : ''}${pnl.toFixed(0)}
        </Text>
      </View>
      <View style={styles.tradeDetails}>
        {entryPrice != null && exitPrice != null && (
          <Text style={styles.tradeDetail}>
            ${entryPrice.toFixed(2)} → ${exitPrice.toFixed(2)}
          </Text>
        )}
        {trade.hold_days != null && (
          <Text style={styles.tradeDetail}>{trade.hold_days}d</Text>
        )}
        {reason ? <Text style={styles.reasonBadge}>{reason}</Text> : null}
      </View>
      {(trade.date_closed || trade.close_date) && (
        <Text style={styles.tradeDate}>{trade.date_closed || trade.close_date}</Text>
      )}
    </View>
  );
}

export default function ManualPortfolioScreen() {
  const [activeTab, setActiveTab]     = useState<'positions' | 'history'>('positions');
  const [positions, setPositions]     = useState<any[]>([]);
  const [closed, setClosed]           = useState<any[]>([]);
  const [cash, setCash]               = useState<number | null>(null);
  const [refreshing, setRefreshing]   = useState(false);
  const [error, setError]             = useState('');
  const [lastUpdated, setLastUpdated] = useState('');
  const [computing, setComputing]     = useState(false);
  const [computeMsg, setComputeMsg]   = useState('');
  const [sellTarget, setSellTarget]   = useState<any>(null);  // position being sold
  const [exitPrice, setExitPrice]     = useState('');
  const [selling, setSelling]         = useState(false);
  const [sellError, setSellError]     = useState('');

  const handleComputeStops = async () => {
    setComputing(true);
    setComputeMsg('');
    try {
      const result = await computeStops();
      setComputeMsg(`Stops computed for ${result.updated} positions`);
      await loadData();
    } catch (e: any) {
      setComputeMsg(`Error: ${e.message}`);
    }
    setComputing(false);
  };

  const openSellModal = (pos: any) => {
    setSellTarget(pos);
    setExitPrice(pos.current_price ? pos.current_price.toFixed(2) : '');
    setSellError('');
  };

  const handleSell = async () => {
    if (!exitPrice) { setSellError('Enter exit price'); return; }
    setSelling(true);
    setSellError('');
    try {
      await sellPosition(sellTarget.symbol, parseFloat(exitPrice));
      setSellTarget(null);
      await loadData();
    } catch (e: any) {
      setSellError(e.message || 'Sell failed');
    }
    setSelling(false);
  };

  const loadData = useCallback(async () => {
    try {
      const token = await getToken();
      if (!token) { router.replace('/login'); return; }
      const data = await fetchManualPortfolio();
      setPositions(data.positions || []);
      setClosed(
        (data.closed || []).sort((a: any, b: any) =>
          ((b.date_closed || b.close_date || '')).localeCompare((a.date_closed || a.close_date || ''))
        )
      );
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

  const sellCount    = positions.filter(p => p.status === 'SELL').length;
  const cautionCount = positions.filter(p => p.status === 'CAUTION').length;
  const targetCount  = positions.filter(p => p.status === 'TARGET').length;

  const unrealized = positions.reduce((s, p) => {
    if (p.current_price && p.entry_price && p.shares)
      return s + (p.current_price - p.entry_price) * p.shares;
    return s;
  }, 0);
  const positionsValue = positions.reduce((s, p) =>
    s + (p.current_price && p.shares ? p.current_price * p.shares : 0), 0);
  const realizedTotal = closed.reduce((s, t) => s + (t.pnl || t.realized_pnl || 0), 0);
  const winCount = closed.filter(t => (t.pnl || t.realized_pnl || 0) > 0).length;

  const manualSummary = {
    capital:      positionsValue + (cash ?? 0),
    cash:         cash ?? 0,
    total_value:  positionsValue + (cash ?? 0),
    total_pnl:    unrealized,
    unrealized,
    realized:     realizedTotal,
    open_count:   positions.length,
    closed_count: closed.length,
    win_count:    winCount,
  };

  const avgHold = closed.length > 0
    ? Math.round(closed.reduce((s, t) => s + (t.hold_days || 0), 0) / closed.length)
    : 0;
  const winRate = closed.length > 0 ? ((winCount / closed.length) * 100).toFixed(1) : '0';

  return (
    <View style={styles.container}>
      <SummaryBar summary={manualSummary} />

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

      {/* Sell Modal */}
      <Modal visible={!!sellTarget} transparent animationType="fade" onRequestClose={() => setSellTarget(null)}>
        <Pressable style={styles.modalOverlay} onPress={() => setSellTarget(null)}>
          <Pressable style={styles.modalBox} onPress={() => {}}>
            <Text style={styles.modalTitle}>Sell {sellTarget?.symbol}</Text>
            <Text style={styles.modalSub}>
              Entry: ${sellTarget?.entry_price?.toFixed(2)}  ·  Shares: {sellTarget?.shares}
            </Text>
            <Text style={styles.label}>Exit Price</Text>
            <TextInput
              style={styles.input}
              value={exitPrice}
              onChangeText={setExitPrice}
              keyboardType="decimal-pad"
              placeholder="Exit price"
              placeholderTextColor="#444"
              autoFocus
            />
            {sellError ? <Text style={styles.sellError}>{sellError}</Text> : null}
            {exitPrice && sellTarget ? (
              <Text style={styles.pnlPreview}>
                {(() => {
                  const pnl = (parseFloat(exitPrice) - sellTarget.entry_price) * sellTarget.shares;
                  return `P&L: ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(0)}`;
                })()}
              </Text>
            ) : null}
            <View style={styles.modalBtns}>
              <Pressable style={styles.modalCancel} onPress={() => setSellTarget(null)}>
                <Text style={styles.modalCancelText}>Cancel</Text>
              </Pressable>
              <Pressable
                style={[styles.modalConfirm, selling && { opacity: 0.6 }]}
                onPress={handleSell}
                disabled={selling}
              >
                {selling
                  ? <ActivityIndicator color="#fff" />
                  : <Text style={styles.modalConfirmText}>Confirm Sell</Text>
                }
              </Pressable>
            </View>
          </Pressable>
        </Pressable>
      </Modal>

      {activeTab === 'positions' ? (
        <>
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

          <Pressable style={styles.buyBtn} onPress={() => router.push('/buy')}>
            <Text style={styles.buyBtnText}>+ Add Position</Text>
          </Pressable>

          <FlatList
            data={positions}
            keyExtractor={(item, i) => item.symbol ?? String(i)}
            renderItem={({ item }) => <PositionRow pos={item} onSell={() => openSellModal(item)} />}
            ListEmptyComponent={<Text style={styles.empty}>No manual positions</Text>}
            refreshControl={
              <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#6366f1" />
            }
            ListFooterComponent={
              lastUpdated ? <Text style={styles.updated}>Updated: {lastUpdated}</Text> : null
            }
            contentContainerStyle={{ paddingBottom: 24 }}
          />
        </>
      ) : (
        <FlatList
          data={closed}
          keyExtractor={(item, i) => `${item.symbol}-${i}`}
          renderItem={({ item }) => <TradeRow trade={item} />}
          ListEmptyComponent={<Text style={styles.empty}>No closed trades</Text>}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#6366f1" />
          }
          ListHeaderComponent={
            <View style={styles.statsBar}>
              <View style={styles.stat}>
                <Text style={styles.statLabel}>Realized</Text>
                <Text style={[styles.statValue, { color: realizedTotal >= 0 ? '#22c55e' : '#ef4444' }]}>
                  ${realizedTotal.toFixed(0)}
                </Text>
              </View>
              <View style={styles.stat}>
                <Text style={styles.statLabel}>Win Rate</Text>
                <Text style={styles.statValue}>{winRate}%</Text>
              </View>
              <View style={styles.stat}>
                <Text style={styles.statLabel}>Avg Hold</Text>
                <Text style={styles.statValue}>{avgHold}d</Text>
              </View>
            </View>
          }
          contentContainerStyle={{ paddingBottom: 24 }}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f0f23' },

  tabRow: {
    flexDirection: 'row', backgroundColor: '#1a1a2e',
    padding: 6, gap: 6, marginHorizontal: 12, marginVertical: 8, borderRadius: 10,
  },
  tabBtn:         { flex: 1, paddingVertical: 7, borderRadius: 7, alignItems: 'center' },
  tabBtnActive:   { backgroundColor: '#6366f1' },
  tabBtnText:     { color: '#888', fontSize: 13, fontWeight: '600' },
  tabBtnTextActive: { color: '#fff' },

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

  statsBar: {
    flexDirection: 'row', justifyContent: 'space-around',
    backgroundColor: '#1a1a2e', padding: 12, margin: 12, borderRadius: 8,
  },
  stat:      { alignItems: 'center' },
  statLabel: { color: '#888', fontSize: 11 },
  statValue: { color: '#fff', fontSize: 16, fontWeight: '600' },

  tradeCard: {
    backgroundColor: '#16213e', padding: 10, borderRadius: 8,
    marginHorizontal: 12, marginBottom: 6,
  },
  tradeHeader:  { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 },
  tradeSymbol:  { color: '#fff', fontSize: 15, fontWeight: '700' },
  tradePnl:     { fontSize: 14, fontWeight: '600' },
  tradeDetails: { flexDirection: 'row', gap: 12, alignItems: 'center' },
  tradeDetail:  { color: '#888', fontSize: 12 },
  reasonBadge:  { color: '#fbbf24', fontSize: 10, fontWeight: '600' },
  tradeDate:    { color: '#444', fontSize: 10, marginTop: 4 },

  cardHeaderRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  sellBtn:      { backgroundColor: '#ef444422', borderWidth: 1, borderColor: '#ef4444', borderRadius: 6, paddingHorizontal: 10, paddingVertical: 4 },
  sellBtnText:  { color: '#ef4444', fontSize: 11, fontWeight: '700' },

  buyBtn:       { backgroundColor: '#22c55e22', borderWidth: 1, borderColor: '#22c55e', borderRadius: 8, margin: 8, padding: 11, alignItems: 'center' },
  buyBtnText:   { color: '#22c55e', fontSize: 13, fontWeight: '700' },

  modalOverlay: { flex: 1, backgroundColor: '#000000aa', justifyContent: 'center', padding: 24 },
  modalBox:     { backgroundColor: '#16213e', borderRadius: 14, padding: 20, gap: 12 },
  modalTitle:   { color: '#fff', fontSize: 18, fontWeight: '700' },
  modalSub:     { color: '#888', fontSize: 12 },
  input:        { backgroundColor: '#0f0f23', color: '#fff', borderRadius: 8, padding: 12, fontSize: 16, borderWidth: 1, borderColor: '#2a2a4a' },
  sellError:    { color: '#ef4444', fontSize: 12 },
  pnlPreview:   { color: '#6366f1', fontSize: 14, fontWeight: '600', textAlign: 'center' },
  modalBtns:    { flexDirection: 'row', gap: 10, marginTop: 4 },
  modalCancel:  { flex: 1, padding: 12, borderRadius: 8, alignItems: 'center', backgroundColor: '#1a1a2e' },
  modalCancelText: { color: '#888', fontWeight: '600' },
  modalConfirm: { flex: 1, padding: 12, borderRadius: 8, alignItems: 'center', backgroundColor: '#ef4444' },
  modalConfirmText: { color: '#fff', fontWeight: '700' },
});
