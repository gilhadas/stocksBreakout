import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

interface Position {
  symbol: string;
  entry_price: number;
  current_price?: number;
  shares: number;
  cost: number;
  stop: number;
  trail_stop?: number;
  target: number;
  quality?: string;
  sector?: string;
  date_added?: string;
  mode?: string;
}

const MODE_COLORS: Record<string, string> = {
  longterm:  '#818cf8',
  swing:     '#34d399',
  daytrade:  '#fb923c',
  scalping:  '#f472b6',
};

const QUALITY_STYLES: Record<string, { bg: string; fg: string }> = {
  GOLD:     { bg: '#854d0e', fg: '#fef08a' },
  PREMIUM:  { bg: '#312e81', fg: '#a5b4fc' },
  HIGH:     { bg: '#14532d', fg: '#86efac' },
};

export default function PositionCard({ pos }: { pos: Position }) {
  const priceIsStale = pos.current_price == null || pos.current_price <= 0;
  const current = priceIsStale ? pos.entry_price : pos.current_price!;

  const pnl    = (current - pos.entry_price) * pos.shares;
  const pnlPct = ((current / pos.entry_price) - 1) * 100;
  const pnlColor = priceIsStale ? '#9ca3af' : (pnl >= 0 ? '#22c55e' : '#ef4444');

  const daysHeld = pos.date_added
    ? Math.floor((Date.now() - new Date(pos.date_added).getTime()) / 86_400_000)
    : null;

  const modeColor = pos.mode ? (MODE_COLORS[pos.mode] ?? '#888') : null;
  const qualityStyle = pos.quality ? (QUALITY_STYLES[pos.quality] ?? null) : null;

  return (
    <View style={styles.card}>

      {/* ── Header: symbol + mode badge ── */}
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          <Text style={styles.symbol}>{pos.symbol}</Text>
          {pos.mode && modeColor && (
            <Text style={[styles.modeBadge, { color: modeColor, borderColor: modeColor }]}>
              {pos.mode.toUpperCase()}
            </Text>
          )}
        </View>
        {pos.quality && qualityStyle && (
          <Text style={[styles.qualityBadge, { backgroundColor: qualityStyle.bg, color: qualityStyle.fg }]}>
            {pos.quality}
          </Text>
        )}
      </View>

      {/* ── P&L row: labeled, split ── */}
      <View style={styles.pnlRow}>
        <View style={styles.pnlBlock}>
          <Text style={styles.pnlLabel}>Return</Text>
          <Text style={[styles.pnlPct, { color: pnlColor }]}>
            {priceIsStale ? '—' : `${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`}
          </Text>
        </View>
        <View style={styles.pnlBlock}>
          <Text style={styles.pnlLabel}>P&L</Text>
          <Text style={[styles.pnlAbs, { color: pnlColor }]}>
            {priceIsStale
              ? '(no quote)'
              : `${pnl >= 0 ? '+' : '-'}$${Math.abs(pnl).toFixed(0)}`}
          </Text>
        </View>
        {priceIsStale && (
          <Text style={styles.staleNote}>stale</Text>
        )}
      </View>

      {/* ── Details grid ── */}
      <View style={styles.details}>
        <Detail label="Entry"   value={`$${pos.entry_price.toFixed(2)}`} />
        <Detail label="Current" value={priceIsStale ? '—' : `$${current.toFixed(2)}`} />
        <Detail label="Shares"  value={`${pos.shares}`} />
        <Detail
          label="Stop"
          value={`$${pos.stop.toFixed(2)}`}
          valueColor={pos.trail_stop ? '#9ca3af' : '#ef4444'}
        />
        {pos.trail_stop != null && pos.trail_stop > 0 && (
          <Detail
            label="Trail ▲"
            value={`$${pos.trail_stop.toFixed(2)}`}
            valueColor="#f59e0b"
          />
        )}
        {pos.date_added && (
          <Detail
            label="Entry Date"
            value={`${pos.date_added.slice(0, 10)}${daysHeld != null ? `  (${daysHeld}d)` : ''}`}
          />
        )}
      </View>

      {/* ── Footer: sector ── */}
      {pos.sector && (
        <Text style={styles.sector}>{pos.sector}</Text>
      )}
    </View>
  );
}

function Detail({
  label,
  value,
  valueColor,
}: {
  label: string;
  value: string;
  valueColor?: string;
}) {
  return (
    <View style={styles.detailItem}>
      <Text style={styles.detailLabel}>{label}</Text>
      <Text style={[styles.detailValue, valueColor ? { color: valueColor } : null]}>
        {value}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#16213e',
    padding: 12,
    borderRadius: 8,
    marginHorizontal: 12,
    marginBottom: 8,
  },

  // Header
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  headerLeft: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  symbol: { color: '#fff', fontSize: 18, fontWeight: '700' },
  modeBadge: {
    fontSize: 10, fontWeight: '700',
    borderWidth: 1, borderRadius: 4,
    paddingHorizontal: 5, paddingVertical: 1,
  },
  qualityBadge: {
    fontSize: 10, fontWeight: '700',
    borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2,
  },

  // P&L
  pnlRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 24,
    marginBottom: 8,
    paddingBottom: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#1e2d4a',
  },
  pnlBlock: { alignItems: 'flex-start' },
  pnlLabel: { color: '#555', fontSize: 10, marginBottom: 1 },
  pnlPct:  { color: '#22c55e', fontSize: 18, fontWeight: '700' },
  pnlAbs:  { color: '#22c55e', fontSize: 14, fontWeight: '600' },
  staleNote: { color: '#6b7280', fontSize: 10, fontStyle: 'italic', marginLeft: 'auto' },

  // Details grid
  details: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
    marginBottom: 6,
  },
  detailItem: { minWidth: '28%' },
  detailLabel: { color: '#555', fontSize: 10, marginBottom: 1 },
  detailValue: { color: '#ccc', fontSize: 13, fontWeight: '500' },

  // Footer
  sector: { color: '#555', fontSize: 11, marginTop: 2 },
});
