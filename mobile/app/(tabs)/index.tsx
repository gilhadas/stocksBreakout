import React, { useState, useCallback } from 'react';
import { View, FlatList, Text, StyleSheet, RefreshControl, Pressable } from 'react-native';
import { useFocusEffect, router } from 'expo-router';
import { fetchPortfolio, refreshPortfolio, getToken, clearToken } from '../../lib/api';
import SummaryBar from '../../components/SummaryBar';
import PositionCard from '../../components/PositionCard';

export default function PortfolioScreen() {
  const [positions, setPositions] = useState<any[]>([]);
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
      setSummary(data.summary);
      setLastUpdated(data.last_updated || '');
      setError('');
    } catch (e: any) {
      if (e.message === 'Session expired') router.replace('/login');
      else setError(e.message);
    }
  }, []);

  useFocusEffect(
    useCallback(() => { loadData(); }, [loadData])
  );

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      const data = await refreshPortfolio();
      setPositions(data.positions || []);
      setSummary(data.summary);
      setLastUpdated(data.last_updated || '');
    } catch (e: any) {
      setError(e.message);
    }
    setRefreshing(false);
  };

  const handleLogout = async () => {
    await clearToken();
    router.replace('/login');
  };

  return (
    <View style={styles.container}>
      {summary && <SummaryBar summary={summary} />}
      {error ? <Text style={styles.error}>{error}</Text> : null}
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
            <Pressable onPress={handleLogout}>
              <Text style={styles.logout}>Logout</Text>
            </Pressable>
          </View>
        }
      />
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
});
