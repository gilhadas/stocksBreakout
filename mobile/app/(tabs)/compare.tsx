/**
 * Book comparison — control vs auto-swap.
 *
 * Deliberately a numeric table, not a chart: the app has no charting library and
 * adding one to answer a question the numbers already answer is not worth the
 * dependency. The per-swap attribution table is the primary readout anyway —
 * the equity deltas need months to separate, because both books trade the same
 * signals on the same days.
 */
import { useFocusEffect } from 'expo-router';
import React, { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { fetchBookComparison } from '../../lib/api';

function pct(v: any, digits = 2) {
  return v === null || v === undefined ? '—' : `${Number(v).toFixed(digits)}%`;
}
function money(v: any) {
  return v === null || v === undefined
    ? '—'
    : `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}
function num(v: any, digits = 2) {
  return v === null || v === undefined ? '—' : Number(v).toFixed(digits);
}
function signColor(v: any) {
  if (v === null || v === undefined) return '#888';
  return Number(v) >= 0 ? '#22c55e' : '#ef4444';
}

export default function CompareScreen() {
  const [report, setReport] = useState<any>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setReport(await fetchBookComparison());
      setError('');
    } catch (e: any) {
      setError(e?.message || 'Could not load comparison');
    }
    setLoading(false);
    setRefreshing(false);
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  if (loading) {
    return <View style={styles.container}><Text style={styles.dim}>Loading…</Text></View>;
  }
  if (error) {
    return <View style={styles.container}><Text style={styles.err}>{error}</Text></View>;
  }

  const books: Record<string, any> = report?.books || {};
  const names = Object.keys(books);
  const attribution: Record<string, any> = report?.attribution || {};
  const thin = Math.min(...names.map(n => books[n].equity_points || 0));

  const rows: [string, (m: any) => string, ((m: any) => string) | null][] = [
    ['Total value',      m => money(m.total_value), null],
    ['Return since fork', m => pct(m.return_pct),   m => signColor(m.return_pct)],
    ['Sharpe',           m => num(m.sharpe),        m => signColor(m.sharpe)],
    ['Max drawdown',     m => pct(m.max_drawdown_pct), null],
    ['Realized since fork', m => money(m.realized_since), m => signColor(m.realized_since)],
    ['Open positions',   m => String(m.open_positions ?? '—'), null],
    ['Closed since fork', m => String(m.closed_since ?? 0), null],
    ['Win rate',         m => pct(m.win_rate, 0),   null],
    ['Swap exits',       m => String(m.swap_exits ?? 0), null],
  ];

  return (
    <ScrollView
      style={styles.container}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor="#6366f1" />
      }
    >
      <Text style={styles.h1}>Does swapping pay?</Text>
      <Text style={styles.dim}>
        Two books, one signal stream. Control only suggests swaps; Auto-swap executes them.
        {report?.fork_date ? ` Forked ${report.fork_date}.` : ' Not forked yet.'}
      </Text>

      {thin < 20 && (
        <Text style={styles.warn}>
          ⚠ Only {isFinite(thin) ? thin : 0} equity point(s) since the fork. Both books trade
          the same signals on the same days, so return and Sharpe are not meaningful yet —
          read the per-swap table below instead.
        </Text>
      )}

      {/* Metric table */}
      <View style={styles.card}>
        <View style={styles.row}>
          <Text style={[styles.cell, styles.label]} />
          {names.map(n => (
            <Text key={n} style={[styles.cell, styles.head]} numberOfLines={2}>
              {books[n].label || n}
            </Text>
          ))}
        </View>
        {rows.map(([label, fmt, color]) => (
          <View key={label} style={styles.row}>
            <Text style={[styles.cell, styles.label]}>{label}</Text>
            {names.map(n => (
              <Text
                key={n}
                style={[styles.cell, styles.val, color ? { color: color(books[n]) } : null]}
              >
                {fmt(books[n])}
              </Text>
            ))}
          </View>
        ))}
      </View>

      {/* Per-swap attribution */}
      {Object.entries(attribution).map(([bookName, att]: [string, any]) => (
        <View key={bookName} style={styles.card}>
          <Text style={styles.h2}>Per-swap attribution — {bookName}</Text>
          <Text style={styles.dim}>
            For each executed swap: what the replacement did vs what the position it
            displaced would have done.
          </Text>
          {!att?.swaps?.length ? (
            <Text style={styles.dim}>No swaps executed yet.</Text>
          ) : (
            <>
              <View style={styles.statRow}>
                <View style={styles.stat}>
                  <Text style={styles.statVal}>{att.n_total}</Text>
                  <Text style={styles.statLbl}>executed</Text>
                </View>
                <View style={styles.stat}>
                  <Text style={styles.statVal}>{pct(att.hit_rate, 0)}</Text>
                  <Text style={styles.statLbl}>hit rate</Text>
                </View>
                <View style={styles.stat}>
                  <Text style={[styles.statVal, { color: signColor(att.avg_edge_pct) }]}>
                    {pct(att.avg_edge_pct)}
                  </Text>
                  <Text style={styles.statLbl}>avg edge</Text>
                </View>
              </View>
              {att.swaps.map((s: any, i: number) => (
                <View key={i} style={styles.swapRow}>
                  <Text style={styles.swapSym}>
                    {s.close_symbol} → {s.open_symbol}
                  </Text>
                  <Text style={styles.dim}>
                    if held {pct(s.held_return_pct)} · swapped {pct(s.swap_return_pct)}
                  </Text>
                  <Text style={[styles.swapEdge, { color: signColor(s.edge_pct) }]}>
                    {s.edge_pct === null || s.edge_pct === undefined
                      ? 'too fresh to score'
                      : `${pct(s.edge_pct)} ${s.verdict}`}
                  </Text>
                </View>
              ))}
            </>
          )}
        </View>
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f0f23', padding: 14 },
  h1: { color: '#fff', fontSize: 20, fontWeight: '700', marginBottom: 4 },
  h2: { color: '#fff', fontSize: 15, fontWeight: '600', marginBottom: 4 },
  dim: { color: '#888', fontSize: 12, marginBottom: 8 },
  warn: { color: '#c9a227', fontSize: 12, marginBottom: 10, lineHeight: 17 },
  err: { color: '#ef4444', fontSize: 13 },
  card: {
    backgroundColor: '#16162e',
    borderRadius: 10,
    padding: 12,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: '#2a2a4a',
  },
  row: { flexDirection: 'row', paddingVertical: 5, borderBottomWidth: 1, borderBottomColor: '#20203c' },
  cell: { flex: 1, fontSize: 12 },
  label: { color: '#888', flex: 1.3 },
  head: { color: '#fff', fontWeight: '700', textAlign: 'right' },
  val: { color: '#ddd', textAlign: 'right' },
  statRow: { flexDirection: 'row', marginVertical: 10 },
  stat: { flex: 1, alignItems: 'center' },
  statVal: { color: '#fff', fontSize: 17, fontWeight: '700' },
  statLbl: { color: '#888', fontSize: 11 },
  swapRow: { paddingVertical: 7, borderTopWidth: 1, borderTopColor: '#20203c' },
  swapSym: { color: '#fff', fontSize: 13, fontWeight: '600' },
  swapEdge: { fontSize: 12, fontWeight: '600' },
});
