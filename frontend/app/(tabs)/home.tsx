import React, { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  RefreshControl,
  TouchableOpacity,
  ActivityIndicator,
  Animated,
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
  analysis?: string;
  regime?: string;
  risk_multiplier?: number;
}

// Live price ticker data
const PRICE_PAIRS = [
  { symbol: 'XAUUSD', name: 'Gold' },
  { symbol: 'BTCUSD', name: 'Bitcoin' },
  { symbol: 'EURUSD', name: 'EUR/USD' },
  { symbol: 'GBPUSD', name: 'GBP/USD' },
];

export default function HomeScreen() {
  const { user } = useAuth();
  const [stats, setStats] = useState<Stats | null>(null);
  const [recentSignals, setRecentSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [mlStatus, setMlStatus] = useState<any>(null);
  const scrollX = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    loadData();
    // Auto-refresh every 30 seconds
    const interval = setInterval(loadData, 30000);
    return () => clearInterval(interval);
  }, []);

  const loadData = async () => {
    try {
      const [statsRes, signalsRes, mlRes] = await Promise.all([
        api.get('/stats'),
        api.get('/signals?limit=10'),
        api.get('/ml/risk').catch(() => null),
      ]);
      setStats(statsRes.data);
      setRecentSignals(signalsRes.data);
      if (mlRes?.data) setMlStatus(mlRes.data);
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

  const getRegimeFromAnalysis = (analysis?: string): string => {
    if (!analysis) return 'UNKNOWN';
    const match = analysis.match(/\[([A-Z_]+)\]/);
    return match ? match[1] : 'UNKNOWN';
  };

  const getRegimeColor = (regime: string): string => {
    switch (regime) {
      case 'TREND_UP': return '#4CAF50';
      case 'TREND_DOWN': return '#F44336';
      case 'RANGE': return '#2196F3';
      case 'HIGH_VOL': return '#FF9800';
      case 'LOW_VOL': return '#9C27B0';
      case 'CHAOS': return '#F44336';
      default: return '#8B8FA8';
    }
  };

  const getRegimeIcon = (regime: string): string => {
    switch (regime) {
      case 'TREND_UP': return 'trending-up';
      case 'TREND_DOWN': return 'trending-down';
      case 'RANGE': return 'swap-horizontal';
      case 'HIGH_VOL': return 'flash';
      case 'LOW_VOL': return 'remove';
      case 'CHAOS': return 'warning';
      default: return 'help-circle';
    }
  };

  const formatPrice = (pair: string, price: number): string => {
    if (pair.includes('XAU')) return price.toFixed(2);
    if (pair.includes('BTC')) return price.toFixed(2);
    if (pair.includes('JPY')) return price.toFixed(3);
    return price.toFixed(5);
  };

  const getTimeAgo = (dateString: string): string => {
    const now = new Date();
    const date = new Date(dateString);
    const diff = Math.floor((now.getTime() - date.getTime()) / 1000);
    
    if (diff < 60) return 'Just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  };

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#FFD700" />
        <Text style={styles.loadingText}>Loading signals...</Text>
      </View>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#FFD700" />}
      >
        {/* Header */}
        <View style={styles.header}>
          <View>
            <Text style={styles.greeting}>Welcome back,</Text>
            <Text style={styles.userName}>{user?.full_name || user?.email?.split('@')[0]}</Text>
          </View>
          <View style={styles.headerRight}>
            {mlStatus?.trading_allowed !== undefined && (
              <View style={[
                styles.tradingStatusBadge,
                { backgroundColor: mlStatus.trading_allowed ? '#4CAF5020' : '#F4433620' }
              ]}>
                <View style={[
                  styles.statusDot,
                  { backgroundColor: mlStatus.trading_allowed ? '#4CAF50' : '#F44336' }
                ]} />
                <Text style={[
                  styles.tradingStatusText,
                  { color: mlStatus.trading_allowed ? '#4CAF50' : '#F44336' }
                ]}>
                  {mlStatus.trading_allowed ? 'TRADING' : 'PAUSED'}
                </Text>
              </View>
            )}
          </View>
        </View>

        {/* ML System Status Card */}
        <View style={styles.mlStatusCard}>
          <View style={styles.mlStatusHeader}>
            <Ionicons name="hardware-chip" size={20} color="#FFD700" />
            <Text style={styles.mlStatusTitle}>ML Engine Active</Text>
          </View>
          <View style={styles.mlStatusGrid}>
            <View style={styles.mlStatusItem}>
              <Text style={styles.mlStatusValue}>10</Text>
              <Text style={styles.mlStatusLabel}>Pairs</Text>
            </View>
            <View style={styles.mlStatusItem}>
              <Text style={styles.mlStatusValue}>6</Text>
              <Text style={styles.mlStatusLabel}>Regimes</Text>
            </View>
            <View style={styles.mlStatusItem}>
              <Text style={styles.mlStatusValue}>3</Text>
              <Text style={styles.mlStatusLabel}>Timeframes</Text>
            </View>
            <View style={styles.mlStatusItem}>
              <Text style={styles.mlStatusValue}>15m</Text>
              <Text style={styles.mlStatusLabel}>Interval</Text>
            </View>
          </View>
        </View>

        {/* Stats Grid */}
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
            <Text style={styles.statValue}>{stats?.win_rate?.toFixed(1) || 0}%</Text>
            <Text style={styles.statLabel}>Win Rate</Text>
          </View>
          <View style={styles.statCard}>
            <Ionicons name="trending-up" size={24} color="#FF9800" />
            <Text style={styles.statValue}>{stats?.avg_pips?.toFixed(0) || 0}</Text>
            <Text style={styles.statLabel}>Avg Pips</Text>
          </View>
        </View>

        {/* Recent Signals */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Recent Signals</Text>
            <TouchableOpacity>
              <Text style={styles.viewAllText}>View All</Text>
            </TouchableOpacity>
          </View>
          
          {recentSignals.length === 0 ? (
            <View style={styles.emptyState}>
              <Ionicons name="bar-chart-outline" size={48} color="#8B8FA8" />
              <Text style={styles.emptyText}>No signals available yet</Text>
              <Text style={styles.emptySubtext}>ML engine analyzing markets...</Text>
            </View>
          ) : (
            recentSignals.map((signal) => {
              const regime = getRegimeFromAnalysis(signal.analysis);
              return (
                <View key={signal.id} style={styles.signalCard}>
                  <View style={styles.signalHeader}>
                    <View style={styles.signalPairRow}>
                      <Text style={styles.pairText}>{signal.pair}</Text>
                      {signal.is_premium && (
                        <View style={styles.premiumBadge}>
                          <Ionicons name="star" size={10} color="#FFD700" />
                        </View>
                      )}
                      <View style={[
                        styles.regimeBadge,
                        { backgroundColor: getRegimeColor(regime) + '20' }
                      ]}>
                        <Ionicons 
                          name={getRegimeIcon(regime) as any} 
                          size={10} 
                          color={getRegimeColor(regime)} 
                        />
                        <Text style={[styles.regimeText, { color: getRegimeColor(regime) }]}>
                          {regime}
                        </Text>
                      </View>
                    </View>
                    <View style={styles.signalMeta}>
                      <View style={[styles.typeBadge, signal.type === 'BUY' ? styles.buyBadge : styles.sellBadge]}>
                        <Ionicons 
                          name={signal.type === 'BUY' ? 'arrow-up' : 'arrow-down'} 
                          size={12} 
                          color="#FFF" 
                        />
                        <Text style={styles.typeText}>{signal.type}</Text>
                      </View>
                      <Text style={styles.timeAgo}>{getTimeAgo(signal.created_at)}</Text>
                    </View>
                  </View>

                  <View style={styles.signalBody}>
                    <View style={styles.priceRow}>
                      <View style={styles.priceItem}>
                        <Text style={styles.priceLabel}>Entry</Text>
                        <Text style={styles.priceValue}>{formatPrice(signal.pair, signal.entry_price)}</Text>
                      </View>
                      <View style={styles.priceItem}>
                        <Text style={styles.priceLabel}>SL</Text>
                        <Text style={[styles.priceValue, styles.slColor]}>{formatPrice(signal.pair, signal.sl_price)}</Text>
                      </View>
                      <View style={styles.priceItem}>
                        <Text style={styles.priceLabel}>Conf.</Text>
                        <Text style={[styles.priceValue, styles.confColor]}>{signal.confidence}%</Text>
                      </View>
                    </View>

                    <View style={styles.tpRow}>
                      <View style={styles.tpItem}>
                        <Text style={styles.tpLabel}>TP1</Text>
                        <Text style={styles.tpValue}>{formatPrice(signal.pair, signal.tp_levels[0])}</Text>
                      </View>
                      <View style={styles.tpItem}>
                        <Text style={styles.tpLabel}>TP2</Text>
                        <Text style={styles.tpValue}>{formatPrice(signal.pair, signal.tp_levels[1])}</Text>
                      </View>
                      <View style={styles.tpItem}>
                        <Text style={styles.tpLabel}>TP3</Text>
                        <Text style={styles.tpValue}>{formatPrice(signal.pair, signal.tp_levels[2])}</Text>
                      </View>
                    </View>
                  </View>
                </View>
              );
            })
          )}
        </View>

        {/* Supported Pairs */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Active Trading Pairs</Text>
          <View style={styles.pairsContainer}>
            {['XAUUSD', 'XAUEUR', 'BTCUSD', 'EURUSD', 'GBPUSD', 'USDJPY', 'EURJPY', 'GBPJPY', 'AUDUSD', 'USDCAD', 'USDCHF'].map((pair) => (
              <View key={pair} style={[
                styles.pairChip,
                pair.includes('XAU') && styles.pairChipGold,
                pair.includes('BTC') && styles.pairChipCrypto,
              ]}>
                <Text style={styles.pairChipText}>{pair}</Text>
              </View>
            ))}
          </View>
        </View>

        {/* Footer Info */}
        <View style={styles.footerInfo}>
          <Ionicons name="shield-checkmark" size={16} color="#4CAF50" />
          <Text style={styles.footerText}>Signals auto-generated every 15 minutes</Text>
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
  loadingText: {
    color: '#8B8FA8',
    marginTop: 12,
    fontSize: 14,
  },
  scrollContent: {
    padding: 16,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  greeting: {
    fontSize: 14,
    color: '#8B8FA8',
  },
  userName: {
    fontSize: 22,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  headerRight: {
    alignItems: 'flex-end',
  },
  tradingStatusBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 12,
    gap: 6,
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  tradingStatusText: {
    fontSize: 11,
    fontWeight: '700',
  },
  mlStatusCard: {
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 14,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: '#FFD70030',
  },
  mlStatusHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 12,
  },
  mlStatusTitle: {
    color: '#FFD700',
    fontSize: 14,
    fontWeight: '600',
  },
  mlStatusGrid: {
    flexDirection: 'row',
    justifyContent: 'space-around',
  },
  mlStatusItem: {
    alignItems: 'center',
  },
  mlStatusValue: {
    color: '#FFFFFF',
    fontSize: 18,
    fontWeight: 'bold',
  },
  mlStatusLabel: {
    color: '#8B8FA8',
    fontSize: 10,
    marginTop: 2,
  },
  statsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
    marginBottom: 20,
  },
  statCard: {
    flex: 1,
    minWidth: '22%',
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 12,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  statValue: {
    fontSize: 20,
    fontWeight: 'bold',
    color: '#FFFFFF',
    marginTop: 6,
  },
  statLabel: {
    fontSize: 10,
    color: '#8B8FA8',
    marginTop: 2,
  },
  section: {
    marginBottom: 20,
  },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  viewAllText: {
    color: '#FFD700',
    fontSize: 13,
  },
  emptyState: {
    alignItems: 'center',
    padding: 40,
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
  },
  emptyText: {
    color: '#FFFFFF',
    fontSize: 16,
    marginTop: 12,
  },
  emptySubtext: {
    color: '#8B8FA8',
    fontSize: 12,
    marginTop: 4,
  },
  signalCard: {
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  signalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: 12,
  },
  signalPairRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    flexWrap: 'wrap',
  },
  pairText: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  premiumBadge: {
    backgroundColor: '#FFD70020',
    padding: 3,
    borderRadius: 4,
  },
  regimeBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 6,
    paddingVertical: 3,
    borderRadius: 4,
    gap: 4,
  },
  regimeText: {
    fontSize: 9,
    fontWeight: '600',
  },
  signalMeta: {
    alignItems: 'flex-end',
    gap: 4,
  },
  typeBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 6,
    gap: 4,
  },
  buyBadge: {
    backgroundColor: '#4CAF50',
  },
  sellBadge: {
    backgroundColor: '#F44336',
  },
  typeText: {
    color: '#FFFFFF',
    fontSize: 11,
    fontWeight: '700',
  },
  timeAgo: {
    color: '#8B8FA8',
    fontSize: 10,
  },
  signalBody: {
    gap: 10,
  },
  priceRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  priceItem: {
    flex: 1,
    alignItems: 'center',
  },
  priceLabel: {
    color: '#8B8FA8',
    fontSize: 10,
    marginBottom: 2,
  },
  priceValue: {
    color: '#FFFFFF',
    fontSize: 13,
    fontWeight: '600',
  },
  slColor: {
    color: '#F44336',
  },
  confColor: {
    color: '#FFD700',
  },
  tpRow: {
    flexDirection: 'row',
    backgroundColor: '#0A0E27',
    borderRadius: 8,
    padding: 10,
  },
  tpItem: {
    flex: 1,
    alignItems: 'center',
  },
  tpLabel: {
    color: '#4CAF50',
    fontSize: 10,
    marginBottom: 2,
  },
  tpValue: {
    color: '#4CAF50',
    fontSize: 12,
    fontWeight: '600',
  },
  pairsContainer: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  pairChip: {
    backgroundColor: '#2196F320',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: '#2196F340',
  },
  pairChipGold: {
    backgroundColor: '#FFD70020',
    borderColor: '#FFD70040',
  },
  pairChipCrypto: {
    backgroundColor: '#F7931A20',
    borderColor: '#F7931A40',
  },
  pairChipText: {
    color: '#FFFFFF',
    fontSize: 11,
    fontWeight: '600',
  },
  footerInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 16,
  },
  footerText: {
    color: '#8B8FA8',
    fontSize: 12,
  },
});
