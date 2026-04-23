import React, { useState, useEffect, useRef } from 'react';
import {
  View, Text, TextInput, Pressable, ScrollView, ActivityIndicator,
  Switch, StyleSheet, KeyboardAvoidingView, Platform,
} from 'react-native';
import { analyzeSymbol, analyzeChat, analyzeLlmStatus, fetchPortfolio, fetchManualPortfolio } from '../../lib/api';

const MODES = ['longterm', 'swing', 'daytrade', 'scalping'] as const;
const TIMEFRAMES = ['1 day', '1W', '15 mins', '5 mins', '1 min'] as const;

const QUALITY_COLOR: Record<string, string> = {
  GOLD: '#eab308',
  PREMIUM: '#6366f1',
  HIGH: '#22c55e',
  STANDARD: '#94a3b8',
};

type ChatTurn = { role: 'user' | 'assistant'; content: string };

export default function AnalyzeScreen() {
  const [symbol, setSymbol] = useState('');
  const [mode, setMode] = useState<typeof MODES[number]>('swing');
  const [timeframe, setTimeframe] = useState<typeof TIMEFRAMES[number]>('1 day');
  const [useLlm, setUseLlm] = useState(false);
  const [llmAvailable, setLlmAvailable] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<any | null>(null);
  const [chatHistory, setChatHistory] = useState<ChatTurn[]>([]);
  const [question, setQuestion] = useState('');
  const [chatSending, setChatSending] = useState(false);
  const [portfolioSymbols, setPortfolioSymbols] = useState<string[]>([]);
  const [pickMode, setPickMode] = useState<'portfolio' | 'custom'>('portfolio');
  const scrollRef = useRef<ScrollView>(null);

  useEffect(() => {
    analyzeLlmStatus().then(s => setLlmAvailable(s.enabled)).catch(() => setLlmAvailable(false));
  }, []);

  useEffect(() => {
    (async () => {
      const syms = new Set<string>();
      try {
        const auto = await fetchPortfolio();
        (auto?.positions || []).forEach((p: any) => p?.symbol && syms.add(p.symbol));
      } catch {}
      try {
        const manual = await fetchManualPortfolio();
        (manual?.positions || []).forEach((p: any) => p?.symbol && syms.add(p.symbol));
      } catch {}
      const list = Array.from(syms).sort();
      setPortfolioSymbols(list);
      if (list.length === 0) setPickMode('custom');
    })();
  }, []);

  const runAnalyze = async (sym: string) => {
    setLoading(true); setError(null); setReport(null); setChatHistory([]);
    try {
      const r = await analyzeSymbol(sym, mode, timeframe);
      setReport(r);
    } catch (e: any) {
      setError(e?.message || 'Failed to analyze');
    } finally {
      setLoading(false);
    }
  };

  const generate = async () => {
    const sym = symbol.trim().toUpperCase();
    if (!sym) { setError('Enter a ticker'); return; }
    await runAnalyze(sym);
  };

  const pickPortfolioSymbol = async (sym: string) => {
    setSymbol(sym);
    await runAnalyze(sym);
  };

  const sendQuestion = async () => {
    const q = question.trim();
    if (!q || !report) return;
    const newUser: ChatTurn = { role: 'user', content: q };
    const nextHistory = [...chatHistory, newUser];
    setChatHistory(nextHistory);
    setQuestion('');
    setChatSending(true);
    try {
      const res = await analyzeChat(report, chatHistory, q);
      setChatHistory([...nextHistory, { role: 'assistant', content: res.answer }]);
      setTimeout(() => scrollRef.current?.scrollToEnd({ animated: true }), 100);
    } catch (e: any) {
      setChatHistory([...nextHistory, { role: 'assistant', content: `[Error: ${e?.message || 'chat failed'}]` }]);
    } finally {
      setChatSending(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={90}
    >
      <ScrollView
        ref={scrollRef}
        style={styles.scroll}
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
      >
        {/* Input card */}
        <View style={styles.card}>
          {portfolioSymbols.length > 0 && (
            <>
              <Text style={styles.label}>Source</Text>
              <View style={styles.chipRow}>
                <Pressable
                  style={[styles.chip, pickMode === 'portfolio' && styles.chipActive]}
                  onPress={() => setPickMode('portfolio')}
                >
                  <Text style={[styles.chipText, pickMode === 'portfolio' && styles.chipTextActive]}>
                    My portfolio
                  </Text>
                </Pressable>
                <Pressable
                  style={[styles.chip, pickMode === 'custom' && styles.chipActive]}
                  onPress={() => setPickMode('custom')}
                >
                  <Text style={[styles.chipText, pickMode === 'custom' && styles.chipTextActive]}>
                    Any ticker
                  </Text>
                </Pressable>
              </View>
            </>
          )}

          {pickMode === 'portfolio' && portfolioSymbols.length > 0 ? (
            <>
              <Text style={styles.label}>Pick holding</Text>
              <View style={styles.chipRow}>
                {portfolioSymbols.map(sym => (
                  <Pressable
                    key={sym}
                    style={[styles.chip, symbol === sym && styles.chipActive]}
                    onPress={() => pickPortfolioSymbol(sym)}
                    disabled={loading}
                  >
                    <Text style={[styles.chipText, symbol === sym && styles.chipTextActive]}>{sym}</Text>
                  </Pressable>
                ))}
              </View>
            </>
          ) : (
            <>
              <Text style={styles.label}>Ticker</Text>
              <TextInput
                style={styles.input}
                value={symbol}
                onChangeText={setSymbol}
                placeholder="e.g. AAPL"
                placeholderTextColor="#555"
                autoCapitalize="characters"
                autoCorrect={false}
              />
            </>
          )}

          <Text style={styles.label}>Mode</Text>
          <View style={styles.chipRow}>
            {MODES.map(m => (
              <Pressable
                key={m}
                style={[styles.chip, mode === m && styles.chipActive]}
                onPress={() => setMode(m)}
              >
                <Text style={[styles.chipText, mode === m && styles.chipTextActive]}>{m}</Text>
              </Pressable>
            ))}
          </View>

          <Text style={styles.label}>Timeframe</Text>
          <View style={styles.chipRow}>
            {TIMEFRAMES.map(tf => (
              <Pressable
                key={tf}
                style={[styles.chip, timeframe === tf && styles.chipActive]}
                onPress={() => setTimeframe(tf)}
              >
                <Text style={[styles.chipText, timeframe === tf && styles.chipTextActive]}>{tf}</Text>
              </Pressable>
            ))}
          </View>

          {llmAvailable && (
            <View style={styles.llmRow}>
              <Text style={styles.label}>Use AI chat</Text>
              <Switch value={useLlm} onValueChange={setUseLlm} />
            </View>
          )}

          {pickMode === 'custom' ? (
            <Pressable style={styles.genBtn} onPress={generate} disabled={loading}>
              {loading ? <ActivityIndicator color="#fff" /> : <Text style={styles.genBtnText}>Generate report</Text>}
            </Pressable>
          ) : (
            loading && <ActivityIndicator color="#6366f1" style={{ marginTop: 14 }} />
          )}

          {error && <Text style={styles.error}>{error}</Text>}
        </View>

        {report && <ReportCard report={report} />}

        {report && useLlm && llmAvailable && (
          <View style={styles.card}>
            <Text style={styles.sectionTitle}>Chat</Text>
            {chatHistory.map((t, i) => (
              <View
                key={i}
                style={[styles.chatBubble, t.role === 'user' ? styles.bubbleUser : styles.bubbleAssistant]}
              >
                <Text style={styles.chatText}>{t.content}</Text>
              </View>
            ))}
            {chatSending && <ActivityIndicator style={{ marginTop: 8 }} color="#6366f1" />}
            <View style={styles.chatInputRow}>
              <TextInput
                style={[styles.input, { flex: 1, marginBottom: 0 }]}
                value={question}
                onChangeText={setQuestion}
                placeholder="Ask a follow-up..."
                placeholderTextColor="#555"
                multiline
              />
              <Pressable
                style={[styles.sendBtn, (!question.trim() || chatSending) && { opacity: 0.5 }]}
                onPress={sendQuestion}
                disabled={!question.trim() || chatSending}
              >
                <Text style={styles.sendBtnText}>Send</Text>
              </Pressable>
            </View>
          </View>
        )}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const PORTFOLIO_STATUS_COLOR: Record<string, string> = {
  SELL: '#ef4444',
  CAUTION: '#f97316',
  HOLD: '#22c55e',
  TARGET: '#6366f1',
  UNKNOWN: '#94a3b8',
};

function PortfolioBadge({ pf }: { pf: any }) {
  if (!pf) {
    return (
      <View style={[styles.portfolioBanner, { backgroundColor: '#0f0f23', borderColor: '#2a2a3e' }]}>
        <Text style={styles.portfolioBannerText}>Not in portfolio</Text>
      </View>
    );
  }
  const color = PORTFOLIO_STATUS_COLOR[pf.status] || '#94a3b8';
  const pnlColor = (pf.pnl_pct ?? 0) >= 0 ? '#22c55e' : '#ef4444';
  const isSell = pf.status === 'SELL' || pf.status === 'CAUTION';
  const icon = pf.status === 'SELL' ? '🚨' : pf.status === 'CAUTION' ? '⚠️' : pf.status === 'TARGET' ? '🎯' : '📈';
  return (
    <View style={[styles.portfolioBanner, { backgroundColor: color + '22', borderColor: color }]}>
      <View style={styles.portfolioBannerHeader}>
        <Text style={[styles.portfolioBannerTitle, { color }]}>
          {icon} {isSell ? 'SELL SIGNAL' : pf.status} · held in {pf.source}
        </Text>
        {pf.pnl_pct != null && (
          <Text style={[styles.portfolioPnl, { color: pnlColor }]}>
            {pf.pnl_pct > 0 ? '+' : ''}{pf.pnl_pct.toFixed(1)}%
          </Text>
        )}
      </View>
      <View style={styles.portfolioBannerRow}>
        {pf.entry_price != null && <Text style={styles.portfolioBannerSub}>Entry ${pf.entry_price.toFixed(2)}</Text>}
        {pf.stop != null && <Text style={styles.portfolioBannerSub}>Stop ${pf.stop.toFixed(2)}</Text>}
        {pf.target != null && <Text style={styles.portfolioBannerSub}>Target ${pf.target.toFixed(2)}</Text>}
        {pf.shares != null && <Text style={styles.portfolioBannerSub}>{pf.shares} sh</Text>}
      </View>
    </View>
  );
}

function ReportCard({ report }: { report: any }) {
  const sig = report.signal;
  const ind = report.indicators || {};

  return (
    <View style={styles.card}>
      <View style={styles.headerRow}>
        <Text style={styles.symbolBig}>{report.symbol}</Text>
        <Text style={styles.priceBig}>
          {report.price != null ? `$${report.price.toFixed(2)}` : '—'}
        </Text>
      </View>

      <PortfolioBadge pf={report.portfolio_status} />

      <View style={styles.row}>
        <Chip label={`Mode: ${report.mode}`} />
        <Chip label={`TF: ${report.timeframe}`} />
        <Chip label={`Regime: ${report.regime}`} />
      </View>

      {sig ? (
        <>
          <View style={[styles.qualityBadge, { backgroundColor: (QUALITY_COLOR[sig.Quality] || '#555') + '22', borderColor: QUALITY_COLOR[sig.Quality] || '#555' }]}>
            <Text style={[styles.qualityText, { color: QUALITY_COLOR[sig.Quality] || '#fff' }]}>
              {sig.Quality || 'SIGNAL'}
            </Text>
          </View>

          <Section title="Trade setup">
            <Kv k="Entry" v={fmtMoney(sig.Price)} />
            <Kv k="Stop" v={fmtMoney(sig.Stop)} color="#ef4444" />
            <Kv k="Target" v={fmtMoney(sig.Target)} color="#22c55e" />
            <Kv k="R:R" v={fmt(sig['R:R'])} />
            <Kv k="WinProb" v={sig.WinProb != null ? `${sig.WinProb}%` : '—'} />
            <Kv k="Grade" v={sig.WinGrade || '—'} />
          </Section>

          <Section title="Momentum & trend">
            <Kv k="RSI" v={fmt(sig.RSI)} />
            <Kv k="Vol" v={sig.Vol != null ? `${sig.Vol}×` : '—'} />
            <Kv k="ATR dist" v={fmt(sig.Dist)} />
            <Kv k="SMA dist%" v={sig['SMA_Dist%'] != null ? `${sig['SMA_Dist%']}%` : '—'} />
            <Kv k="Gap%" v={sig['Gap%'] != null ? `${sig['Gap%']}%` : '—'} />
            <Kv k="Minervini" v={fmt(sig.MinerviniScore)} />
          </Section>

          {(sig.Patterns || sig.VCP) && (
            <Section title="Patterns">
              {sig.Patterns ? <Text style={styles.bodyText}>{sig.Patterns}</Text> : null}
              {sig.VCP ? <Text style={styles.bodyText}>VCP quality {sig.VCP_Quality} (pivot ${sig.VCP_Pivot})</Text> : null}
              {sig.Near52wHigh ? <Text style={styles.bodyText}>Near 52-week high</Text> : null}
              {sig.RSI_BullDiv ? <Text style={styles.bodyText}>Bullish RSI divergence</Text> : null}
            </Section>
          )}

          {(sig.SR_Resistance || sig.SR_Support) && (
            <Section title="Support/Resistance">
              <Kv k="Resistance" v={fmtMoney(sig.SR_Resistance)} />
              <Kv k="Support" v={fmtMoney(sig.SR_Support)} />
              {sig.SR_Break ? <Text style={styles.bodyText}>Breaking resistance</Text> : null}
              {sig.SR_InChannel ? <Text style={styles.bodyText}>In {sig.SR_Channel_Dir} channel</Text> : null}
            </Section>
          )}

          {sig.Checks && Object.keys(sig.Checks).length > 0 && (
            <Section title="Checks">
              {Object.entries(sig.Checks).map(([k, v]) => (
                <View key={k} style={styles.checkRow}>
                  <Text style={styles.checkMark}>{v ? '✓' : '✗'}</Text>
                  <Text style={[styles.checkText, { color: v ? '#22c55e' : '#64748b' }]}>{k}</Text>
                </View>
              ))}
            </Section>
          )}
        </>
      ) : (
        <Section title="No signal">
          <Text style={styles.bodyText}>
            {report.rejection_reason || 'No breakout, bounce, or continuation detected.'}
          </Text>
        </Section>
      )}

      <Section title="Indicators (latest bar)">
        <Kv k="RSI" v={fmt(ind.RSI, 1)} />
        <Kv k="MACD" v={fmt(ind.MACD, 3)} />
        <Kv k="MACD sig" v={fmt(ind.MACD_Signal, 3)} />
        <Kv k="ADX" v={fmt(ind.ADX, 1)} />
        <Kv k="ATR" v={fmt(ind.ATR, 2)} />
        <Kv k="Vol ratio" v={fmt(ind.Vol_Ratio, 2)} />
        <Kv k="BB pos" v={fmt(ind.BB_Position, 2)} />
        <Kv k="SMA 50" v={fmtMoney(ind.SMA_50)} />
        <Kv k="SMA 150" v={fmtMoney(ind.SMA_150)} />
        <Kv k="SMA 200" v={fmtMoney(ind.SMA_200)} />
      </Section>
    </View>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      <View style={styles.sectionBody}>{children}</View>
    </View>
  );
}

function Chip({ label }: { label: string }) {
  return (
    <View style={styles.smallChip}>
      <Text style={styles.smallChipText}>{label}</Text>
    </View>
  );
}

function Kv({ k, v, color }: { k: string; v: any; color?: string }) {
  return (
    <View style={styles.kvRow}>
      <Text style={styles.kvKey}>{k}</Text>
      <Text style={[styles.kvVal, color ? { color } : null]}>{v == null ? '—' : v}</Text>
    </View>
  );
}

function fmt(v: any, digits = 2): string {
  if (v == null || v === '' || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  return n.toFixed(digits);
}

function fmtMoney(v: any): string {
  if (v == null || v === '' || Number.isNaN(Number(v))) return '—';
  return `$${Number(v).toFixed(2)}`;
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f0f23' },
  scroll: { flex: 1 },
  content: { padding: 12, paddingBottom: 40 },
  card: {
    backgroundColor: '#1a1a2e',
    borderRadius: 10,
    padding: 14,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: '#2a2a3e',
  },
  label: { color: '#94a3b8', fontSize: 12, marginTop: 8, marginBottom: 4, textTransform: 'uppercase' },
  input: {
    backgroundColor: '#0f0f23',
    borderWidth: 1,
    borderColor: '#333',
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 8,
    color: '#fff',
    fontSize: 14,
    marginBottom: 6,
  },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  chip: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 14,
    backgroundColor: '#0f0f23',
    borderWidth: 1,
    borderColor: '#333',
  },
  chipActive: { backgroundColor: '#6366f1', borderColor: '#6366f1' },
  chipText: { color: '#94a3b8', fontSize: 12 },
  chipTextActive: { color: '#fff', fontWeight: '600' },
  llmRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 10,
  },
  genBtn: {
    marginTop: 14,
    backgroundColor: '#6366f1',
    borderRadius: 6,
    padding: 12,
    alignItems: 'center',
  },
  genBtnText: { color: '#fff', fontWeight: '600', fontSize: 14 },
  error: { color: '#ef4444', marginTop: 8, fontSize: 13 },

  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' },
  symbolBig: { color: '#fff', fontSize: 24, fontWeight: '700' },
  priceBig: { color: '#fff', fontSize: 20, fontWeight: '600' },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 8 },
  smallChip: {
    backgroundColor: '#0f0f23',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#333',
  },
  smallChipText: { color: '#94a3b8', fontSize: 11 },
  portfolioBanner: {
    marginTop: 10,
    padding: 10,
    borderRadius: 8,
    borderWidth: 1,
  },
  portfolioBannerHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  portfolioBannerTitle: { fontSize: 13, fontWeight: '700' },
  portfolioBannerText: { color: '#94a3b8', fontSize: 12, fontStyle: 'italic' },
  portfolioBannerRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 6 },
  portfolioBannerSub: { color: '#e2e8f0', fontSize: 12 },
  portfolioPnl: { fontSize: 13, fontWeight: '700' },
  qualityBadge: {
    alignSelf: 'flex-start',
    paddingHorizontal: 12,
    paddingVertical: 5,
    borderRadius: 6,
    borderWidth: 1,
    marginTop: 12,
  },
  qualityText: { fontSize: 13, fontWeight: '700', letterSpacing: 1 },

  section: { marginTop: 14 },
  sectionTitle: { color: '#6366f1', fontSize: 12, fontWeight: '700', textTransform: 'uppercase', marginBottom: 6 },
  sectionBody: {},
  kvRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 3 },
  kvKey: { color: '#94a3b8', fontSize: 13 },
  kvVal: { color: '#fff', fontSize: 13, fontWeight: '500' },
  bodyText: { color: '#e2e8f0', fontSize: 13, marginTop: 2 },
  checkRow: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingVertical: 2 },
  checkMark: { color: '#94a3b8', fontSize: 13, width: 14 },
  checkText: { fontSize: 12 },

  chatBubble: {
    padding: 10,
    borderRadius: 8,
    marginTop: 6,
    maxWidth: '92%',
  },
  bubbleUser: { alignSelf: 'flex-end', backgroundColor: '#6366f1' },
  bubbleAssistant: { alignSelf: 'flex-start', backgroundColor: '#0f0f23', borderWidth: 1, borderColor: '#2a2a3e' },
  chatText: { color: '#fff', fontSize: 13 },
  chatInputRow: { flexDirection: 'row', gap: 8, marginTop: 10, alignItems: 'flex-end' },
  sendBtn: {
    backgroundColor: '#6366f1',
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 6,
  },
  sendBtnText: { color: '#fff', fontWeight: '600' },
});
