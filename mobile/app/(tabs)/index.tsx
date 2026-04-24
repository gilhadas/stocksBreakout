import React, { useState, useCallback } from 'react';
import { View, FlatList, Text, StyleSheet, RefreshControl, Pressable, Alert, Platform, TextInput } from 'react-native';
import { cacheDirectory, writeAsStringAsync, EncodingType } from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';

function buildCSV(rows: any[]): string {
  const headers = ['Symbol','Mode','Quality','Entry','Date','Current','Stop','Gain%','GainDollar','ClosePrice','ExitDate','Stopped'];
  const escape = (v: any) => {
    const s = v == null ? '' : String(v);
    return s.includes(',') || s.includes('"') ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [
    headers.join(','),
    ...rows.map(r => [
      r.symbol, r.mode, r.quality,
      r.entry_price?.toFixed(2),
      r.date_added,
      r.current_price?.toFixed(2),
      r.stop?.toFixed(2),
      r.gain_pct?.toFixed(2),
      r.gain_dollar?.toFixed(2),
      r.close_price != null ? r.close_price.toFixed(2) : '',
      r.exit_date ?? '',
      r.stopped ? 'YES' : 'NO',
    ].map(escape).join(',')),
  ];
  return lines.join('\n');
}

async function exportSkippedCSV(rows: any[]) {
  try {
    const csv = buildCSV(rows);
    if (Platform.OS === 'web') {
      const blob = new Blob([csv], { type: 'text/csv' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'skipped_signals.csv';
      a.click();
      URL.revokeObjectURL(url);
      return;
    }
    if (!cacheDirectory) {
      Alert.alert('Export Error', 'File system not available.');
      return;
    }
    const path = `${cacheDirectory}skipped_signals.csv`;
    await writeAsStringAsync(path, csv, { encoding: EncodingType.UTF8 });
    const isAvailable = await Sharing.isAvailableAsync();
    if (!isAvailable) {
      Alert.alert('Export Error', 'Sharing is not available on this device.');
      return;
    }
    await Sharing.shareAsync(path, { mimeType: 'text/csv', UTI: 'public.comma-separated-values-text' });
  } catch (err: any) {
    Alert.alert('Export Failed', err?.message ?? String(err));
  }
}
import { useFocusEffect, router } from 'expo-router';
import { fetchPortfolio, refreshPortfolio, fetchSkipped, suggestSwaps, executeSwap, undoSwap, getToken, clearToken, resetPortfolio, recalculatePortfolio } from '../../lib/api';
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

const MODE_BADGE_COLORS: Record<string, string> = {
  longterm: '#818cf8', swing: '#34d399', daytrade: '#fb923c', scalping: '#f472b6',
};

function SkippedCard({ item }: { item: any }) {
  const modeColor = item.mode ? (MODE_BADGE_COLORS[item.mode] ?? '#888') : '#888';
  const stopped: boolean = item.stopped === true;
  const gainPct: number = item.gain_pct ?? 0;
  const gainColor = gainPct >= 0 ? '#22c55e' : '#ef4444';
  return (
    <View style={[styles.skippedCard, stopped && styles.skippedCardStopped]}>
      {/* Header: symbol + badges + gain % */}
      <View style={styles.skippedHeader}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <Text style={styles.skippedSymbol}>{item.symbol}</Text>
          <Text style={[styles.modeBadge, { color: modeColor, borderColor: modeColor }]}>
            {(item.mode || '').toUpperCase()}
          </Text>
          <Text style={[styles.qualityBadge, item.quality === 'GOLD' ? styles.qualityGold : styles.qualityPremium]}>
            {item.quality}
          </Text>
          {stopped && <Text style={styles.stoppedBadge}>SOLD</Text>}
        </View>
        <Text style={[styles.skippedColValue, { color: gainColor }]}>
          {gainPct >= 0 ? '+' : ''}{gainPct.toFixed(1)}%
        </Text>
      </View>
      {/* Row 1: Entry / Date / Current / Stop */}
      <View style={styles.skippedCols}>
        <View style={styles.skippedCol}>
          <Text style={styles.skippedColLabel}>Entry</Text>
          <Text style={styles.skippedColValue}>${item.entry_price?.toFixed(2)}</Text>
        </View>
        <View style={styles.skippedCol}>
          <Text style={styles.skippedColLabel}>Date</Text>
          <Text style={styles.skippedColValue}>{item.date_added}</Text>
        </View>
        <View style={styles.skippedCol}>
          <Text style={styles.skippedColLabel}>Current</Text>
          <Text style={[styles.skippedColValue, { color: gainColor }]}>
            ${item.current_price?.toFixed(2) ?? '—'}
          </Text>
        </View>
        <View style={styles.skippedCol}>
          <Text style={styles.skippedColLabel}>Stop</Text>
          <Text style={[styles.skippedColValue, { color: '#f59e0b' }]}>
            ${item.stop?.toFixed(2) ?? '—'}
          </Text>
        </View>
      </View>
      {/* Row 2: exit details when stopped */}
      {stopped && (
        <View style={styles.skippedCols}>
          <View style={styles.skippedCol}>
            <Text style={styles.skippedColLabel}>Close price</Text>
            <Text style={[styles.skippedColValue, { color: '#ef4444' }]}>
              ${item.close_price?.toFixed(2) ?? '—'}
            </Text>
          </View>
          <View style={styles.skippedCol}>
            <Text style={styles.skippedColLabel}>Exit date</Text>
            <Text style={styles.skippedColValue}>{item.exit_date ?? '—'}</Text>
          </View>
          <View style={styles.skippedCol}>
            <Text style={styles.skippedColLabel}>P&L</Text>
            <Text style={[styles.skippedColValue, { color: '#ef4444' }]}>
              {item.gain_dollar >= 0 ? '+' : ''}${item.gain_dollar?.toFixed(0)}
            </Text>
          </View>
        </View>
      )}
    </View>
  );
}

export default function PortfolioScreen() {
  const [activeTab, setActiveTab] = useState<'positions' | 'history' | 'skipped'>('positions');
  const [positions, setPositions] = useState<any[]>([]);
  const [closed, setClosed] = useState<any[]>([]);
  const [skipped, setSkipped] = useState<any[]>([]);
  const [skippedTotalGainPct, setSkippedTotalGainPct] = useState(0);
  const [skippedTotalNorm, setSkippedTotalNorm] = useState(0);
  const [summary, setSummary] = useState<any>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [swapping, setSwapping] = useState(false);
  const [swapResults, setSwapResults] = useState<any[] | null>(null);
  const [executingSwap, setExecutingSwap] = useState<string | null>(null);
  const [swapToast, setSwapToast] = useState<{ msg: string; canUndo: boolean; kind: 'ok' | 'err' } | null>(null);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState('');
  const [showManage, setShowManage] = useState(false);
  const [manageDate, setManageDate] = useState('');
  const [manageBusy, setManageBusy] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const token = await getToken();
      if (!token) { router.replace('/login'); return; }
      const [data, skippedData] = await Promise.all([fetchPortfolio(), fetchSkipped()]);
      setPositions(data.positions || []);
      setClosed(
        (data.closed || []).sort((a: any, b: any) =>
          (b.date_closed || '').localeCompare(a.date_closed || '')
        )
      );
      setSummary(data.summary);
      setLastUpdated(data.last_updated || '');
      setSkipped(skippedData.skipped || []);

      setSkippedTotalGainPct(skippedData.total_gain_pct || 0);
      setSkippedTotalNorm(skippedData.total_gain_normalized || 0);
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
      const [data, skippedData] = await Promise.all([refreshPortfolio(), fetchSkipped()]);
      setPositions(data.positions || []);
      setClosed(
        (data.closed || []).sort((a: any, b: any) =>
          (b.date_closed || '').localeCompare(a.date_closed || '')
        )
      );
      setSummary(data.summary);
      setLastUpdated(data.last_updated || '');
      setSkipped(skippedData.skipped || []);

      setSkippedTotalGainPct(skippedData.total_gain_pct || 0);
      setSkippedTotalNorm(skippedData.total_gain_normalized || 0);
    } catch (e: any) {
      setError(e.message);
    }
    setRefreshing(false);
  };

  const handleSwapAdvisor = async () => {
    setSwapping(true);
    setSwapResults(null);
    setSwapToast(null);
    try {
      const result = await suggestSwaps();
      setSwapResults(result.swaps || []);
    } catch (e: any) {
      setSwapResults([]);
      setError(e?.message ?? String(e));
    }
    setSwapping(false);
  };

  const handleExecuteSwap = async (closeSym: string, openSym: string) => {
    const key = `${closeSym}→${openSym}`;
    setExecutingSwap(key);
    setSwapToast(null);
    try {
      const result = await executeSwap(closeSym, openSym);
      // Drop the executed pair from the inline list so it can't be clicked again
      setSwapResults((prev) =>
        prev ? prev.filter((s) => !(s.close_symbol === closeSym && s.open_symbol === openSym)) : prev
      );
      setSwapToast({
        msg: `Closed ${closeSym} @ $${result.closed?.exit_price?.toFixed(2)} → Opened ${openSym} @ $${result.opened?.entry_price?.toFixed(2)}`,
        canUndo: true,
        kind: 'ok',
      });
      await loadData();
      // Auto-dismiss toast after 8s (undo window)
      setTimeout(() => setSwapToast((t) => (t && t.canUndo ? null : t)), 8000);
    } catch (e: any) {
      setSwapToast({ msg: e?.message ?? 'Swap failed', canUndo: false, kind: 'err' });
      setTimeout(() => setSwapToast(null), 4000);
    }
    setExecutingSwap(null);
  };

  const handleUndoSwap = async () => {
    try {
      await undoSwap();
      setSwapToast({ msg: 'Swap undone.', canUndo: false, kind: 'ok' });
      await loadData();
      setTimeout(() => setSwapToast(null), 3000);
    } catch (e: any) {
      setSwapToast({ msg: e?.message ?? 'Undo failed', canUndo: false, kind: 'err' });
      setTimeout(() => setSwapToast(null), 4000);
    }
  };

  // Cross-platform confirm — RN Web's Alert.alert ignores button callbacks
  const confirm = (title: string, msg: string): Promise<boolean> => {
    if (Platform.OS === 'web') {
      // eslint-disable-next-line no-alert
      return Promise.resolve(window.confirm(`${title}\n\n${msg}`));
    }
    return new Promise((resolve) => {
      Alert.alert(title, msg, [
        { text: 'Cancel', style: 'cancel', onPress: () => resolve(false) },
        { text: 'OK', style: 'destructive', onPress: () => resolve(true) },
      ]);
    });
  };

  const showError = (msg: string) => {
    if (Platform.OS === 'web') window.alert(msg);
    else Alert.alert('Failed', msg);
  };

  // Validate YYYY-MM-DD; empty string is allowed (means "all time")
  const isValidDate = (s: string) => s === '' || /^\d{4}-\d{2}-\d{2}$/.test(s);

  const handleRecalculate = async () => {
    const date = manageDate.trim();
    if (!isValidDate(date)) {
      showError('Date must be YYYY-MM-DD or empty');
      return;
    }
    const ok = await confirm(
      'Recalculate Portfolio',
      `Reset and rescan signals${date ? ` from ${date}` : ' (all time)'}?`
    );
    if (!ok) return;
    setManageBusy(true);
    try {
      await recalculatePortfolio(date || undefined);
      await loadData();
      setShowManage(false);
    } catch (e: any) {
      showError(e?.message ?? 'Recalculate failed');
    }
    setManageBusy(false);
  };

  const handleReset = async () => {
    const ok = await confirm(
      'Reset Portfolio',
      'Wipe all positions and history? This cannot be undone.'
    );
    if (!ok) return;
    setManageBusy(true);
    try {
      await resetPortfolio();
      await loadData();
      setShowManage(false);
    } catch (e: any) {
      showError(e?.message ?? 'Reset failed');
    }
    setManageBusy(false);
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
          style={[styles.tabBtn, activeTab === 'skipped' && styles.tabBtnActive]}
          onPress={() => setActiveTab('skipped')}
        >
          <Text style={[styles.tabBtnText, activeTab === 'skipped' && styles.tabBtnTextActive]}>
            Skipped ({skipped.length})
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

      {/* Manage Portfolio toggle */}
      <Pressable
        style={[styles.manageToggleBtn, showManage && styles.manageToggleBtnActive]}
        onPress={() => setShowManage((v) => !v)}
      >
        <Text style={styles.manageToggleBtnText}>
          {showManage ? '✕ Close' : '⚙ Manage Portfolio'}
        </Text>
      </Pressable>

      {/* Manage Portfolio panel */}
      {showManage && (
        <View style={styles.managePanel}>
          <Text style={styles.managePanelTitle}>Manage Portfolio</Text>
          <Text style={styles.managePanelLabel}>From date (optional, YYYY-MM-DD)</Text>
          <TextInput
            style={styles.manageDateInput}
            placeholder="e.g. 2026-01-01"
            placeholderTextColor="#555"
            value={manageDate}
            onChangeText={setManageDate}
            autoCapitalize="none"
            keyboardType="numbers-and-punctuation"
          />
          <Pressable
            style={[styles.manageBtn, manageBusy && styles.manageBtnDisabled]}
            onPress={handleRecalculate}
            disabled={manageBusy}
          >
            <Text style={styles.manageBtnText}>
              {manageBusy ? 'Working…' : '♻️ Recalculate from date'}
            </Text>
          </Pressable>
          <Pressable
            style={[styles.manageBtn, styles.manageBtnDanger, manageBusy && styles.manageBtnDisabled]}
            onPress={handleReset}
            disabled={manageBusy}
          >
            <Text style={styles.manageBtnText}>🗑️ Reset Portfolio</Text>
          </Pressable>
        </View>
      )}

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
              <Pressable
                style={[styles.swapBtn, swapping && styles.swapBtnDisabled]}
                onPress={handleSwapAdvisor}
                disabled={swapping}
              >
                <Text style={styles.swapBtnText}>{swapping ? 'Analyzing...' : '⚡ Swap Advisor'}</Text>
              </Pressable>
              {swapResults !== null && (
                <View style={styles.swapResults}>
                  <Text style={styles.swapResultsTitle}>
                    {swapResults.length === 0 ? 'No swap opportunities found.' : `${swapResults.length} swap suggestion${swapResults.length > 1 ? 's' : ''}:`}
                  </Text>
                  {swapResults.map((s: any, i: number) => {
                    const upside = s.open_entry && s.open_target
                      ? ((s.open_target - s.open_entry) / s.open_entry * 100).toFixed(1)
                      : '?';
                    const key = `${s.close_symbol}→${s.open_symbol}`;
                    const busy = executingSwap === key;
                    return (
                      <View key={i} style={styles.swapResultRow}>
                        <Text style={styles.swapResultClose}>
                          Close <Text style={{ color: '#ef4444' }}>{s.close_symbol}</Text>
                          {' '}({s.close_pnl_pct?.toFixed(1)}%)
                        </Text>
                        <Text style={styles.swapResultOpen}>
                          Open <Text style={{ color: '#34d399' }}>{s.open_symbol}</Text>
                          {' '}[{s.open_quality}]{'  '}R:R {s.open_rr?.toFixed(2)}{'  '}{upside}% upside
                        </Text>
                        <Pressable
                          style={[styles.swapExecBtn, busy && styles.swapBtnDisabled]}
                          onPress={() => handleExecuteSwap(s.close_symbol, s.open_symbol)}
                          disabled={busy || executingSwap !== null}
                        >
                          <Text style={styles.swapExecBtnText}>{busy ? 'Executing…' : '⚡ Execute'}</Text>
                        </Pressable>
                      </View>
                    );
                  })}
                </View>
              )}
              {swapToast && (
                <View style={[styles.swapToast, swapToast.kind === 'err' && styles.swapToastErr]}>
                  <Text style={styles.swapToastText}>{swapToast.msg}</Text>
                  {swapToast.canUndo && (
                    <Pressable onPress={handleUndoSwap}>
                      <Text style={styles.swapToastUndo}>UNDO</Text>
                    </Pressable>
                  )}
                </View>
              )}
              <Pressable onPress={async () => { await clearToken(); router.replace('/login'); }}>
                <Text style={styles.logout}>Logout</Text>
              </Pressable>
            </View>
          }
        />
      ) : activeTab === 'history' ? (
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
      ) : (
        <FlatList
          data={skipped}
          keyExtractor={(item, i) => `${item.symbol}-${i}`}
          renderItem={({ item }) => <SkippedCard item={item} />}
          ListEmptyComponent={<Text style={styles.empty}>No skipped positions</Text>}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={handleRefresh} tintColor="#6366f1" />
          }
          ListHeaderComponent={
            <View>
              <View style={styles.statsBar}>
                <StatBox label="Skipped" value={`${skipped.length}`} color="#f59e0b" />
                <StatBox
                  label={`Total ($1k ea)\n${skippedTotalNorm >= 0 ? '+' : ''}$${skippedTotalNorm.toFixed(0)}`}
                  value={`${skippedTotalGainPct >= 0 ? '+' : ''}${skippedTotalGainPct.toFixed(1)}%`}
                  color={skippedTotalNorm >= 0 ? '#22c55e' : '#ef4444'}
                />
                <Pressable style={styles.csvBtn} onPress={() => exportSkippedCSV(skipped)}>
                  <Text style={styles.csvBtnText}>↓ CSV</Text>
                </Pressable>
              </View>
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

  skippedCard: {
    backgroundColor: '#1a1a2e', padding: 10, borderRadius: 8,
    marginHorizontal: 12, marginBottom: 6, borderLeftWidth: 3, borderLeftColor: '#f59e0b',
  },
  skippedCardStopped: { borderLeftColor: '#ef4444' },
  skippedHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  skippedSymbol: { color: '#fff', fontSize: 15, fontWeight: '700' },
  skippedDate: { color: '#555', fontSize: 10 },
  skippedRow: { flexDirection: 'row', gap: 10, alignItems: 'center', marginBottom: 3 },
  skippedPrice: { color: '#ccc', fontSize: 13, fontWeight: '600' },
  skippedUpside: { color: '#22c55e', fontSize: 11 },
  skippedRisk: { color: '#ef4444', fontSize: 11 },
  skippedCost: { color: '#666', fontSize: 10 },
  modeBadge: { fontSize: 9, fontWeight: '700', borderWidth: 1, borderRadius: 3, paddingHorizontal: 4, paddingVertical: 1 },
  qualityBadge: { fontSize: 9, fontWeight: '700', borderRadius: 3, paddingHorizontal: 4, paddingVertical: 1 },
  qualityGold: { backgroundColor: '#854d0e', color: '#fef08a' },
  qualityPremium: { backgroundColor: '#312e81', color: '#a5b4fc' },

  stoppedBadge: { fontSize: 9, fontWeight: '700', color: '#ef4444', borderWidth: 1, borderColor: '#ef4444', borderRadius: 3, paddingHorizontal: 4, paddingVertical: 1 },
  csvBtn: { marginLeft: 'auto', backgroundColor: '#1e3a5f', paddingHorizontal: 10, paddingVertical: 6, borderRadius: 6, borderWidth: 1, borderColor: '#3b82f6' },
  csvBtnText: { color: '#60a5fa', fontSize: 12, fontWeight: '700' },
  swapBtn: { backgroundColor: '#4c1d95', paddingHorizontal: 20, paddingVertical: 10, borderRadius: 8, borderWidth: 1, borderColor: '#7c3aed' },
  swapBtnDisabled: { opacity: 0.5 },
  swapBtnText: { color: '#c4b5fd', fontSize: 13, fontWeight: '700' },
  swapResults: { backgroundColor: '#1a1a2e', borderRadius: 8, padding: 12, width: '100%', borderLeftWidth: 3, borderLeftColor: '#7c3aed' },
  swapResultsTitle: { color: '#c4b5fd', fontSize: 12, fontWeight: '700', marginBottom: 8 },
  swapResultRow: { marginBottom: 10, gap: 2 },
  swapResultClose: { color: '#ccc', fontSize: 12 },
  swapResultOpen: { color: '#ccc', fontSize: 12 },
  swapExecBtn: { alignSelf: 'flex-start', marginTop: 4, backgroundColor: '#4c1d95', paddingHorizontal: 12, paddingVertical: 5, borderRadius: 6, borderWidth: 1, borderColor: '#7c3aed' },
  swapExecBtnText: { color: '#c4b5fd', fontSize: 11, fontWeight: '700' },
  swapToast: { marginTop: 10, backgroundColor: '#064e3b', borderRadius: 8, padding: 10, borderLeftWidth: 3, borderLeftColor: '#34d399', flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  swapToastErr: { backgroundColor: '#4c1d1d', borderLeftColor: '#ef4444' },
  swapToastText: { color: '#d1fae5', fontSize: 12, flex: 1 },
  swapToastUndo: { color: '#fde68a', fontSize: 12, fontWeight: '700', paddingHorizontal: 8 },
  skippedCols: { flexDirection: 'row', gap: 8, marginVertical: 6 },
  skippedCol: { flex: 1, alignItems: 'center' },
  skippedColLabel: { color: '#555', fontSize: 10, marginBottom: 2 },
  skippedColValue: { color: '#ccc', fontSize: 12, fontWeight: '600' },

  manageToggleBtn: {
    marginHorizontal: 12, marginBottom: 6, paddingVertical: 8, borderRadius: 8,
    backgroundColor: '#1a1a2e', borderWidth: 1, borderColor: '#374151', alignItems: 'center',
  },
  manageToggleBtnActive: { borderColor: '#6366f1', backgroundColor: '#2d2b55' },
  manageToggleBtnText: { color: '#9ca3af', fontSize: 13, fontWeight: '600' },

  managePanel: {
    backgroundColor: '#16213e', borderRadius: 10, marginHorizontal: 12, marginBottom: 8,
    padding: 16, borderWidth: 1, borderColor: '#374151',
  },
  managePanelTitle: { color: '#fff', fontSize: 15, fontWeight: '700', marginBottom: 12 },
  managePanelLabel: { color: '#888', fontSize: 12, marginBottom: 6 },
  manageDateInput: {
    backgroundColor: '#0f0f23', color: '#fff', borderRadius: 8,
    padding: 12, fontSize: 14, marginBottom: 12, borderWidth: 1, borderColor: '#374151',
  },
  manageBtn: {
    backgroundColor: '#4c1d95', borderRadius: 8, padding: 12, alignItems: 'center',
    marginBottom: 8, borderWidth: 1, borderColor: '#7c3aed',
  },
  manageBtnDanger: { backgroundColor: '#7f1d1d', borderColor: '#ef4444' },
  manageBtnDisabled: { opacity: 0.5 },
  manageBtnText: { color: '#fff', fontSize: 14, fontWeight: '600' },
});
