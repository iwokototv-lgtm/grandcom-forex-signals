import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  RefreshControl,
  TouchableOpacity,
  ActivityIndicator,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useAuth } from '../../contexts/AuthContext';
import { Ionicons } from '@expo/vector-icons';
import api from '../../utils/api';

interface Stats {
  total_signals: number;
  active_signals: number;
  win_rate: number;
  avg_pips: number;
  total_closed: number;
}

interface Signal {
  id: string;
  pair: string;
  type: string;
  entry_price: number;
  current_price?: number;
  tp_levels: number[];
  sl_price: number;
  confidence: number;
  status: string;
  created_at: string;
  is_premium: boolean;
}

export default function HomeScreen() {
  const { user } = useAuth();
  const [stats, setStats] = useState<Stats | null>(null);
  const [recentSignals, setRecentSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      const [statsRes, signalsRes] = await Promise.all([
        api.get('/stats'),
        api.get('/signals?limit=5'),
      ]);
      setStats(statsRes.data);
      setRecentSignals(signalsRes.data);
    } catch (error) {
      console.error('Error loading data:', error);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  const onRefresh = () => {
    setRefreshing(true);
    loadData();
  };

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#FFD700" />
      </View>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#FFD700" />}
      >
        <View style={styles.header}>
          <View>
            <Text style={styles.greeting}>Welcome back,</Text>
            <Text style={styles.userName}>{user?.full_name || user?.email}</Text>
          </View>
          <View style={styles.subscriptionBadge}>
            <Text style={styles.subscriptionText}>{user?.subscription_tier}</Text>
          </View>
        </View>

        <View style={styles.statsGrid}>
          <View style={styles.statCard}>
            <Ionicons name="flash" size={24} color="#FFD700" />
            <Text style={styles.statValue}>{stats?.total_signals || 0}</Text>
            <Text style={styles.statLabel}>Total Signals</Text>
          </View>
          <View style={styles.statCard}>
            <Ionicons name="pulse" size={24} color="#4CAF50" />
            <Text style={styles.statValue}>{stats?.active_signals || 0}</Text>
            <Text style={styles.statLabel}>Active</Text>
          </View>
          <View style={styles.statCard}>
            <Ionicons name="trophy" size={24} color="#2196F3" />
            <Text style={styles.statValue}>{stats?.win_rate.toFixed(1) || 0}%</Text>
            <Text style={styles.statLabel}>Win Rate</Text>
          </View>
          <View style={styles.statCard}>
            <Ionicons name="trending-up" size={24} color="#FF9800" />
            <Text style={styles.statValue}>{stats?.avg_pips.toFixed(0) || 0}</Text>
            <Text style={styles.statLabel}>Avg Pips</Text>
          </View>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Recent Signals</Text>
          {recentSignals.length === 0 ? (
            <View style={styles.emptyState}>
              <Ionicons name="bar-chart-outline" size={48} color="#8B8FA8" />
              <Text style={styles.emptyText}>No signals available yet</Text>
              <Text style={styles.emptySubtext}>Check back soon for new trading opportunities</Text>
            </View>
          ) : (
            recentSignals.map((signal) => (
              <View key={signal.id} style={styles.signalCard}>
                <View style={styles.signalHeader}>
                  <View style={styles.signalPair}>
                    <Text style={styles.pairText}>{signal.pair}</Text>
                    {signal.is_premium && (
                      <View style={styles.premiumBadge}>
                        <Ionicons name="star" size={12} color="#FFD700" />
                      </View>
                    )}
                  </View>
                  <View style={[styles.typeBadge, signal.type === 'BUY' ? styles.buyBadge : styles.sellBadge]}>
                    <Text style={styles.typeText}>{signal.type}</Text>
                  </View>
                </View>
                <View style={styles.signalDetails}>
                  <View style={styles.detailRow}>
                    <Text style={styles.detailLabel}>Entry</Text>
                    <Text style={styles.detailValue}>{signal.entry_price.toFixed(2)}</Text>
                  </View>
                  <View style={styles.detailRow}>
                    <Text style={styles.detailLabel}>TP1</Text>
                    <Text style={[styles.detailValue, styles.tpValue]}>{signal.tp_levels[0].toFixed(2)}</Text>
                  </View>
                  <View style={styles.detailRow}>
                    <Text style={styles.detailLabel}>TP2</Text>
                    <Text style={[styles.detailValue, styles.tpValue]}>{signal.tp_levels[1].toFixed(2)}</Text>
                  </View>
                  <View style={styles.detailRow}>
                    <Text style={styles.detailLabel}>TP3</Text>
                    <Text style={[styles.detailValue, styles.tpValue]}>{signal.tp_levels[2].toFixed(2)}</Text>
                  </View>
                  <View style={styles.detailRow}>
                    <Text style={styles.detailLabel}>SL</Text>
                    <Text style={[styles.detailValue, styles.slValue]}>{signal.sl_price.toFixed(2)}</Text>
                  </View>
                  <View style={styles.detailRow}>
                    <Text style={styles.detailLabel}>Confidence</Text>
                    <Text style={[styles.detailValue, styles.confidenceValue]}>{signal.confidence}%</Text>
                  </View>
                </View>
              </View>
            ))
          )}
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0A0E27',
  },
  loadingContainer: {
    flex: 1,
    backgroundColor: '#0A0E27',
    alignItems: 'center',
    justifyContent: 'center',
  },
  scrollContent: {
    padding: 16,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 24,
  },
  greeting: {
    fontSize: 16,
    color: '#8B8FA8',
  },
  userName: {
    fontSize: 24,
    fontWeight: 'bold',
    color: '#FFFFFF',
    marginTop: 4,
  },
  subscriptionBadge: {
    backgroundColor: '#FFD700',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
  },
  subscriptionText: {
    color: '#0A0E27',
    fontWeight: 'bold',
    fontSize: 12,
  },
  statsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
    marginBottom: 24,
  },
  statCard: {
    flex: 1,
    minWidth: '47%',
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 16,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  statValue: {
    fontSize: 24,
    fontWeight: 'bold',
    color: '#FFFFFF',
    marginTop: 8,
  },
  statLabel: {
    fontSize: 12,
    color: '#8B8FA8',
    marginTop: 4,
  },
  section: {
    marginBottom: 24,
  },
  sectionTitle: {
    fontSize: 20,
    fontWeight: 'bold',
    color: '#FFFFFF',
    marginBottom: 16,
  },
  signalCard: {
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  signalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  signalPair: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  pairText: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  premiumBadge: {
    backgroundColor: 'rgba(255, 215, 0, 0.2)',
    borderRadius: 12,
    padding: 4,
  },
  typeBadge: {
    paddingHorizontal: 12,
    paddingVertical: 4,
    borderRadius: 8,
  },
  buyBadge: {
    backgroundColor: 'rgba(76, 175, 80, 0.2)',
  },
  sellBadge: {
    backgroundColor: 'rgba(244, 67, 54, 0.2)',
  },
  typeText: {
    color: '#FFFFFF',
    fontWeight: 'bold',
    fontSize: 12,
  },
  signalDetails: {
    gap: 8,
  },
  detailRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  detailLabel: {
    fontSize: 14,
    color: '#8B8FA8',
  },
  detailValue: {
    fontSize: 14,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  confidenceValue: {
    color: '#FFD700',
  },
  tpValue: {
    color: '#4CAF50',
  },
  slValue: {
    color: '#F44336',
  },
  emptyState: {
    alignItems: 'center',
    padding: 48,
  },
  emptyText: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#FFFFFF',
    marginTop: 16,
  },
  emptySubtext: {
    fontSize: 14,
    color: '#8B8FA8',
    marginTop: 8,
    textAlign: 'center',
  },
});
