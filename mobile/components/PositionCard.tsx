import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

interface Position {
  symbol: string;
  entry_price: number;
  current_price?: number;
  shares: number;
  cost: number;
  stop: number;
  target: number;
  quality?: string;
  sector?: string;
  date_added?: string;
}

export default function PositionCard({ pos }: { pos: Position }) {
  const current = pos.current_price || pos.entry_price;
  const pnl = (current - pos.entry_price) * pos.shares;
  const pnlPct = ((current / pos.entry_price) - 1) * 100;
  const pnlColor = pnl >= 0 ? '#22c55e' : '#ef4444';

  return (
    <View style={styles.card}>
      <View style={styles.header}>
        <Text style={styles.symbol}>{pos.symbol}</Text>
        <Text style={[styles.pnl, { color: pnlColor }]}>
          {pnl >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%  ${pnl.toFixed(0)}
        </Text>
      </View>
      <View style={styles.details}>
        <Detail label="Entry" value={`$${pos.entry_price.toFixed(2)}`} />
        <Detail label="Current" value={`$${current.toFixed(2)}`} />
        <Detail label="Shares" value={`${pos.shares}`} />
        <Detail label="Stop" value={`$${pos.stop.toFixed(2)}`} />
      </View>
      <View style={styles.footer}>
        {pos.quality && <Text style={styles.badge}>{pos.quality}</Text>}
        {pos.sector && <Text style={styles.sector}>{pos.sector}</Text>}
      </View>
    </View>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <View>
      <Text style={styles.detailLabel}>{label}</Text>
      <Text style={styles.detailValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: '#16213e', padding: 12, borderRadius: 8, marginHorizontal: 12, marginBottom: 8 },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 },
  symbol: { color: '#fff', fontSize: 18, fontWeight: '700' },
  pnl: { fontSize: 14, fontWeight: '600' },
  details: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 },
  detailLabel: { color: '#666', fontSize: 10 },
  detailValue: { color: '#ccc', fontSize: 13 },
  footer: { flexDirection: 'row', gap: 8, marginTop: 4 },
  badge: { color: '#fbbf24', fontSize: 11, fontWeight: '600' },
  sector: { color: '#666', fontSize: 11 },
});
