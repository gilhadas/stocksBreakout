import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

interface Summary {
  capital: number;
  cash: number;
  total_value: number;
  total_pnl: number;
  unrealized: number;
  realized: number;
  open_count: number;
  closed_count: number;
  win_count: number;
}

export default function SummaryBar({ summary }: { summary: Summary }) {
  const pnlColor = summary.total_pnl >= 0 ? '#22c55e' : '#ef4444';
  const winRate = summary.closed_count > 0
    ? ((summary.win_count / summary.closed_count) * 100).toFixed(1)
    : '0.0';

  return (
    <View style={styles.container}>
      <View style={styles.row}>
        <Stat label="Total Value" value={`$${summary.total_value.toLocaleString()}`} />
        <Stat label="P&L" value={`${summary.total_pnl >= 0 ? '+' : ''}$${summary.total_pnl.toFixed(0)}`} color={pnlColor} />
        <Stat label="Cash" value={`$${summary.cash.toLocaleString()}`} />
      </View>
      <View style={styles.row}>
        <Stat label="Open" value={`${summary.open_count}`} />
        <Stat label="Closed" value={`${summary.closed_count}`} />
        <Stat label="Win Rate" value={`${winRate}%`} />
        <Stat label="Realized" value={`$${summary.realized.toFixed(0)}`} color={summary.realized >= 0 ? '#22c55e' : '#ef4444'} />
      </View>
    </View>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.label}>{label}</Text>
      <Text style={[styles.value, color ? { color } : null]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { backgroundColor: '#1a1a2e', padding: 12, borderRadius: 8, margin: 12, gap: 8 },
  row: { flexDirection: 'row', justifyContent: 'space-around' },
  stat: { alignItems: 'center', minWidth: 70 },
  label: { color: '#888', fontSize: 11, marginBottom: 2 },
  value: { color: '#fff', fontSize: 15, fontWeight: '600' },
});
