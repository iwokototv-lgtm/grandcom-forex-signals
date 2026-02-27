import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  RefreshControl,
  ActivityIndicator,
  Dimensions,
  TouchableOpacity,
  Modal,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import api from '../../utils/api';

interface Stats {
  total_signals: number;
  active_signals: number;
  win_rate: number;
  avg_pips: number;
  total_closed: number;
}

interface RegimeInfo {
  name: string;
  confidence: number;
  risk_multiplier: number;
}

interface MTFAnalysis {
  symbol: string;
  h4_bias: {
    direction: string;
    strength: number;
    adx: number;
    trending: boolean;
  } | null;
  h1_structure: {
    bias: string;
    position_in_range: number;
    rsi: number;
  } | null;
  m15_trigger: {
    trigger: string;
    confidence: number;
  } | null;
  confluence_score: number;
  trade_direction: string;
  valid_setup: boolean;
}

const { width } = Dimensions.get('window');

const ALL_PAIRS = ["XAUUSD", "XAUEUR", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY", "EURJPY", "GBPJPY", "AUDUSD", "USDCAD"];

export default function AnalyticsScreen() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [mlStats, setMlStats] = useState<any>(null);
  const [selectedPair, setSelectedPair] = useState<string | null>(null);
  const [mtfAnalysis, setMtfAnalysis] = useState<MTFAnalysis | null>(null);
  const [mtfLoading, setMtfLoading] = useState(false);
  const [showMtfModal, setShowMtfModal] = useState(false);
  const [regimeData, setRegimeData] = useState<{[key: string]: RegimeInfo}>({});

  useEffect(() => {
    loadAllData();
  }, []);

  const loadAllData = async () => {
    try {
      await Promise.all([loadStats(), loadMlStats()]);
    } catch (error) {
      console.error('Error loading data:', error);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  const loadStats = async () => {
    try {
      const response = await api.get('/stats');
      setStats(response.data);
    } catch (error) {
      console.error('Error loading stats:', error);
    }
  };

  const loadMlStats = async () => {
    try {
      const response = await api.get('/ml/stats');
      if (response.data.success) {
        setMlStats(response.data.stats);
      }
    } catch (error) {
      console.error('Error loading ML stats:', error);
    }
  };

  const loadMtfForPair = async (pair: string) => {
    setSelectedPair(pair);
    setMtfLoading(true);
    setShowMtfModal(true);
    
    try {
      const response = await api.get(`/ml/mtf/${pair}`);
      if (response.data.success) {
        setMtfAnalysis(response.data.analysis);
      }
    } catch (error) {
      console.error(`Error loading MTF for ${pair}:`, error);
    } finally {
      setMtfLoading(false);
    }
  };

  const onRefresh = () => {
    setRefreshing(true);
    loadAllData();
  };

  const getDirectionColor = (direction: string) => {
    switch (direction?.toUpperCase()) {
      case 'BULLISH':
      case 'BUY':
        return '#4CAF50';
      case 'BEARISH':
      case 'SELL':
        return '#F44336';
      default:
        return '#8B8FA8';
    }
  };

  const getRegimeColor = (regime: string) => {
    switch (regime?.toUpperCase()) {
      case 'TREND_UP':
        return '#4CAF50';
      case 'TREND_DOWN':
        return '#F44336';
      case 'RANGE':
        return '#2196F3';
      case 'HIGH_VOL':
        return '#FF9800';
      case 'LOW_VOL':
        return '#9C27B0';
      case 'CHAOS':
        return '#F44336';
      default:
        return '#8B8FA8';
    }
  };

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#FFD700" />
        <Text style={styles.loadingText}>Loading Analytics...</Text>
      </View>
    );
  }

  const winRate = stats?.win_rate || 0;
  const lossRate = 100 - winRate;

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#FFD700" />}
      >
        <View style={styles.header}>
          <Text style={styles.title}>ML Analytics Dashboard</Text>
          <Text style={styles.subtitle}>AI-Powered Market Intelligence</Text>
        </View>

        {/* Main Win Rate Card */}
        <View style={styles.mainCard}>
          <View style={styles.mainStatContainer}>
            <Ionicons name="trophy" size={48} color="#FFD700" />
            <Text style={styles.mainStatValue}>{winRate.toFixed(1)}%</Text>
            <Text style={styles.mainStatLabel}>Win Rate</Text>
          </View>
          
          <View style={styles.progressBarContainer}>
            <View style={styles.progressBar}>
              <View style={[styles.winBar, { width: `${winRate}%` }]} />
            </View>
            <View style={styles.progressLegend}>
              <View style={styles.legendItem}>
                <View style={[styles.legendDot, { backgroundColor: '#4CAF50' }]} />
                <Text style={styles.legendText}>Wins: {winRate.toFixed(0)}%</Text>
              </View>
              <View style={styles.legendItem}>
                <View style={[styles.legendDot, { backgroundColor: '#F44336' }]} />
                <Text style={styles.legendText}>Losses: {lossRate.toFixed(0)}%</Text>
              </View>
            </View>
          </View>
        </View>

        {/* Quick Stats Grid */}
        <View style={styles.statsGrid}>
          <View style={styles.statCard}>
            <Ionicons name="flash" size={28} color="#FFD700" />
            <Text style={styles.statValue}>{stats?.total_signals || 0}</Text>
            <Text style={styles.statLabel}>Total Signals</Text>
          </View>
          <View style={styles.statCard}>
            <Ionicons name="pulse" size={28} color="#4CAF50" />
            <Text style={styles.statValue}>{stats?.active_signals || 0}</Text>
            <Text style={styles.statLabel}>Active</Text>
          </View>
          <View style={styles.statCard}>
            <Ionicons name="trending-up" size={28} color="#2196F3" />
            <Text style={styles.statValue}>{stats?.avg_pips?.toFixed(0) || 0}</Text>
            <Text style={styles.statLabel}>Avg Pips</Text>
          </View>
          <View style={styles.statCard}>
            <Ionicons name="checkmark-circle" size={28} color="#FF9800" />
            <Text style={styles.statValue}>{stats?.total_closed || 0}</Text>
            <Text style={styles.statLabel}>Closed</Text>
          </View>
        </View>

        {/* ML Risk Status */}
        {mlStats?.risk_metrics && (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Risk Management</Text>
            <View style={styles.riskCard}>
              <View style={styles.riskRow}>
                <Text style={styles.riskLabel}>Consecutive Losses</Text>
                <Text style={[
                  styles.riskValue,
                  { color: mlStats.risk_metrics.consecutive_losses > 2 ? '#F44336' : '#4CAF50' }
                ]}>
                  {mlStats.risk_metrics.consecutive_losses || 0}
                </Text>
              </View>
              <View style={styles.riskRow}>
                <Text style={styles.riskLabel}>Drawdown</Text>
                <Text style={[
                  styles.riskValue,
                  { color: mlStats.risk_metrics.drawdown_pct > 5 ? '#FF9800' : '#4CAF50' }
                ]}>
                  {mlStats.risk_metrics.drawdown_pct?.toFixed(2) || 0}%
                </Text>
              </View>
              <View style={styles.riskRow}>
                <Text style={styles.riskLabel}>Open Positions</Text>
                <Text style={styles.riskValue}>
                  {mlStats.risk_metrics.open_positions || 0}
                </Text>
              </View>
            </View>
          </View>
        )}

        {/* Multi-Timeframe Analysis Section */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Multi-Timeframe Analysis</Text>
          <Text style={styles.sectionSubtitle}>Tap a pair for H4/H1/M15 breakdown</Text>
          
          <View style={styles.pairsGrid}>
            {ALL_PAIRS.map((pair) => (
              <TouchableOpacity
                key={pair}
                style={[
                  styles.pairButton,
                  pair.includes('XAU') && styles.pairButtonGold,
                  pair.includes('BTC') && styles.pairButtonCrypto,
                ]}
                onPress={() => loadMtfForPair(pair)}
              >
                <Text style={styles.pairButtonText}>{pair}</Text>
                <Ionicons name="chevron-forward" size={16} color="#8B8FA8" />
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* Regime Stats */}
        {mlStats?.regime_stats && (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Regime Distribution</Text>
            <View style={styles.regimeCard}>
              <View style={styles.regimeRow}>
                <Text style={styles.regimeLabel}>Current Regime</Text>
                <View style={[
                  styles.regimeBadge,
                  { backgroundColor: getRegimeColor(mlStats.regime_stats.current_regime) + '20' }
                ]}>
                  <Text style={[
                    styles.regimeBadgeText,
                    { color: getRegimeColor(mlStats.regime_stats.current_regime) }
                  ]}>
                    {mlStats.regime_stats.current_regime || 'UNKNOWN'}
                  </Text>
                </View>
              </View>
              <View style={styles.regimeRow}>
                <Text style={styles.regimeLabel}>History Length</Text>
                <Text style={styles.regimeValue}>
                  {mlStats.regime_stats.history_length || 0} samples
                </Text>
              </View>
            </View>
          </View>
        )}

        {/* Strategy Performance */}
        {mlStats && Object.keys(mlStats).filter(k => !['risk_metrics', 'regime_stats'].includes(k)).length > 0 && (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Strategy Performance</Text>
            {Object.entries(mlStats)
              .filter(([key]) => !['risk_metrics', 'regime_stats'].includes(key))
              .map(([strategy, data]: [string, any]) => (
                <View key={strategy} style={styles.strategyCard}>
                  <View style={styles.strategyHeader}>
                    <Text style={styles.strategyName}>{strategy.toUpperCase()}</Text>
                    <Text style={[
                      styles.strategyWinRate,
                      { color: (data.win_rate || 0) >= 50 ? '#4CAF50' : '#F44336' }
                    ]}>
                      {data.win_rate?.toFixed(1) || 0}%
                    </Text>
                  </View>
                  <View style={styles.strategyStats}>
                    <Text style={styles.strategyStatText}>
                      Wins: {data.wins || 0} | Losses: {data.losses || 0} | Total: {data.total || 0}
                    </Text>
                  </View>
                </View>
              ))}
          </View>
        )}
      </ScrollView>

      {/* MTF Analysis Modal */}
      <Modal
        visible={showMtfModal}
        animationType="slide"
        transparent={true}
        onRequestClose={() => setShowMtfModal(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>{selectedPair} Analysis</Text>
              <TouchableOpacity onPress={() => setShowMtfModal(false)}>
                <Ionicons name="close" size={24} color="#FFFFFF" />
              </TouchableOpacity>
            </View>

            {mtfLoading ? (
              <View style={styles.modalLoading}>
                <ActivityIndicator size="large" color="#FFD700" />
                <Text style={styles.loadingText}>Analyzing H4/H1/M15...</Text>
              </View>
            ) : mtfAnalysis ? (
              <ScrollView style={styles.modalScroll}>
                {/* Confluence Score */}
                <View style={styles.confluenceCard}>
                  <Text style={styles.confluenceLabel}>Confluence Score</Text>
                  <View style={styles.confluenceScoreRow}>
                    {[1, 2, 3].map((i) => (
                      <View
                        key={i}
                        style={[
                          styles.confluenceDot,
                          i <= mtfAnalysis.confluence_score && styles.confluenceDotActive
                        ]}
                      />
                    ))}
                  </View>
                  <Text style={[
                    styles.confluenceDirection,
                    { color: getDirectionColor(mtfAnalysis.trade_direction) }
                  ]}>
                    {mtfAnalysis.trade_direction}
                  </Text>
                  {mtfAnalysis.valid_setup && (
                    <View style={styles.validSetupBadge}>
                      <Ionicons name="checkmark-circle" size={16} color="#4CAF50" />
                      <Text style={styles.validSetupText}>Valid Setup</Text>
                    </View>
                  )}
                </View>

                {/* H4 Bias */}
                <View style={styles.timeframeCard}>
                  <View style={styles.timeframeHeader}>
                    <Text style={styles.timeframeTitle}>H4 BIAS</Text>
                    {mtfAnalysis.h4_bias && (
                      <View style={[
                        styles.directionBadge,
                        { backgroundColor: getDirectionColor(mtfAnalysis.h4_bias.direction) + '20' }
                      ]}>
                        <Text style={[
                          styles.directionText,
                          { color: getDirectionColor(mtfAnalysis.h4_bias.direction) }
                        ]}>
                          {mtfAnalysis.h4_bias.direction}
                        </Text>
                      </View>
                    )}
                  </View>
                  {mtfAnalysis.h4_bias ? (
                    <View style={styles.timeframeDetails}>
                      <Text style={styles.detailText}>ADX: {mtfAnalysis.h4_bias.adx}</Text>
                      <Text style={styles.detailText}>Strength: {(mtfAnalysis.h4_bias.strength * 100).toFixed(0)}%</Text>
                      <Text style={styles.detailText}>Trending: {mtfAnalysis.h4_bias.trending ? 'Yes' : 'No'}</Text>
                    </View>
                  ) : (
                    <Text style={styles.noDataText}>No H4 data available</Text>
                  )}
                </View>

                {/* H1 Structure */}
                <View style={styles.timeframeCard}>
                  <View style={styles.timeframeHeader}>
                    <Text style={styles.timeframeTitle}>H1 STRUCTURE</Text>
                    {mtfAnalysis.h1_structure && (
                      <View style={[
                        styles.directionBadge,
                        { backgroundColor: getDirectionColor(mtfAnalysis.h1_structure.bias) + '20' }
                      ]}>
                        <Text style={[
                          styles.directionText,
                          { color: getDirectionColor(mtfAnalysis.h1_structure.bias) }
                        ]}>
                          {mtfAnalysis.h1_structure.bias}
                        </Text>
                      </View>
                    )}
                  </View>
                  {mtfAnalysis.h1_structure ? (
                    <View style={styles.timeframeDetails}>
                      <Text style={styles.detailText}>RSI: {mtfAnalysis.h1_structure.rsi}</Text>
                      <Text style={styles.detailText}>Range Position: {(mtfAnalysis.h1_structure.position_in_range * 100).toFixed(0)}%</Text>
                    </View>
                  ) : (
                    <Text style={styles.noDataText}>No H1 data available</Text>
                  )}
                </View>

                {/* M15 Trigger */}
                <View style={styles.timeframeCard}>
                  <View style={styles.timeframeHeader}>
                    <Text style={styles.timeframeTitle}>M15 TRIGGER</Text>
                    {mtfAnalysis.m15_trigger && (
                      <View style={[
                        styles.directionBadge,
                        { backgroundColor: getDirectionColor(mtfAnalysis.m15_trigger.trigger) + '20' }
                      ]}>
                        <Text style={[
                          styles.directionText,
                          { color: getDirectionColor(mtfAnalysis.m15_trigger.trigger) }
                        ]}>
                          {mtfAnalysis.m15_trigger.trigger}
                        </Text>
                      </View>
                    )}
                  </View>
                  {mtfAnalysis.m15_trigger ? (
                    <View style={styles.timeframeDetails}>
                      <Text style={styles.detailText}>Confidence: {(mtfAnalysis.m15_trigger.confidence * 100).toFixed(0)}%</Text>
                    </View>
                  ) : (
                    <Text style={styles.noDataText}>No M15 data available</Text>
                  )}
                </View>
              </ScrollView>
            ) : (
              <Text style={styles.noDataText}>Failed to load analysis</Text>
            )}
          </View>
        </View>
      </Modal>
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
    marginBottom: 20,
  },
  title: {
    fontSize: 26,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  subtitle: {
    fontSize: 13,
    color: '#8B8FA8',
    marginTop: 4,
  },
  mainCard: {
    backgroundColor: '#1A1F3A',
    borderRadius: 16,
    padding: 20,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  mainStatContainer: {
    alignItems: 'center',
    marginBottom: 20,
  },
  mainStatValue: {
    fontSize: 44,
    fontWeight: 'bold',
    color: '#FFD700',
    marginTop: 8,
  },
  mainStatLabel: {
    fontSize: 14,
    color: '#8B8FA8',
    marginTop: 2,
  },
  progressBarContainer: {
    width: '100%',
  },
  progressBar: {
    height: 10,
    backgroundColor: '#0A0E27',
    borderRadius: 5,
    overflow: 'hidden',
    marginBottom: 10,
  },
  winBar: {
    height: '100%',
    backgroundColor: '#4CAF50',
  },
  progressLegend: {
    flexDirection: 'row',
    justifyContent: 'space-around',
  },
  legendItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  legendDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  legendText: {
    fontSize: 11,
    color: '#8B8FA8',
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
    borderRadius: 10,
    padding: 12,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  statValue: {
    fontSize: 22,
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
  sectionTitle: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#FFFFFF',
    marginBottom: 8,
  },
  sectionSubtitle: {
    fontSize: 12,
    color: '#8B8FA8',
    marginBottom: 12,
  },
  pairsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  pairButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#1A1F3A',
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#2A2F4A',
    minWidth: '48%',
  },
  pairButtonGold: {
    borderColor: '#FFD70040',
  },
  pairButtonCrypto: {
    borderColor: '#F7931A40',
  },
  pairButtonText: {
    color: '#FFFFFF',
    fontWeight: '600',
    fontSize: 13,
  },
  riskCard: {
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  riskRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#2A2F4A',
  },
  riskLabel: {
    color: '#8B8FA8',
    fontSize: 13,
  },
  riskValue: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '600',
  },
  regimeCard: {
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  regimeRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 8,
  },
  regimeLabel: {
    color: '#8B8FA8',
    fontSize: 13,
  },
  regimeValue: {
    color: '#FFFFFF',
    fontSize: 14,
  },
  regimeBadge: {
    paddingHorizontal: 12,
    paddingVertical: 4,
    borderRadius: 6,
  },
  regimeBadgeText: {
    fontSize: 12,
    fontWeight: '600',
  },
  strategyCard: {
    backgroundColor: '#1A1F3A',
    borderRadius: 10,
    padding: 14,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  strategyHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  strategyName: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '600',
  },
  strategyWinRate: {
    fontSize: 16,
    fontWeight: 'bold',
  },
  strategyStats: {
    marginTop: 6,
  },
  strategyStatText: {
    color: '#8B8FA8',
    fontSize: 11,
  },
  // Modal styles
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.8)',
    justifyContent: 'flex-end',
  },
  modalContent: {
    backgroundColor: '#1A1F3A',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    maxHeight: '80%',
    padding: 20,
  },
  modalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 20,
  },
  modalTitle: {
    fontSize: 20,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  modalLoading: {
    alignItems: 'center',
    padding: 40,
  },
  modalScroll: {
    maxHeight: '100%',
  },
  confluenceCard: {
    backgroundColor: '#0A0E27',
    borderRadius: 12,
    padding: 20,
    alignItems: 'center',
    marginBottom: 16,
  },
  confluenceLabel: {
    color: '#8B8FA8',
    fontSize: 12,
    marginBottom: 12,
  },
  confluenceScoreRow: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 12,
  },
  confluenceDot: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: '#2A2F4A',
  },
  confluenceDotActive: {
    backgroundColor: '#FFD700',
  },
  confluenceDirection: {
    fontSize: 24,
    fontWeight: 'bold',
  },
  validSetupBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginTop: 8,
    backgroundColor: '#4CAF5020',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 6,
  },
  validSetupText: {
    color: '#4CAF50',
    fontSize: 12,
    fontWeight: '600',
  },
  timeframeCard: {
    backgroundColor: '#0A0E27',
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
  },
  timeframeHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  timeframeTitle: {
    color: '#FFD700',
    fontSize: 14,
    fontWeight: 'bold',
  },
  directionBadge: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 6,
  },
  directionText: {
    fontSize: 12,
    fontWeight: '600',
  },
  timeframeDetails: {
    gap: 6,
  },
  detailText: {
    color: '#8B8FA8',
    fontSize: 13,
  },
  noDataText: {
    color: '#8B8FA8',
    fontSize: 13,
    textAlign: 'center',
    padding: 20,
  },
});
