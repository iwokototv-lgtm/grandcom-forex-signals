import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  RefreshControl,
  ActivityIndicator,
  Dimensions,
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

const { width } = Dimensions.get('window');

export default function AnalyticsScreen() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    loadStats();
  }, []);

  const loadStats = async () => {
    try {
      const response = await api.get('/stats');
      setStats(response.data);
    } catch (error) {
      console.error('Error loading stats:', error);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  const onRefresh = () => {
    setRefreshing(true);
    loadStats();
  };

  if (loading) {
    return (
      <View style={styles.loadingContainer}>
        <ActivityIndicator size="large" color="#FFD700" />
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
          <Text style={styles.title}>Performance Analytics</Text>
          <Text style={styles.subtitle}>Track your trading performance</Text>
        </View>

        {/* Main Stats Card */}
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

        {/* Stats Grid */}
        <View style={styles.statsGrid}>
          <View style={styles.statCard}>
            <View style={styles.statIconContainer}>
              <Ionicons name="flash" size={32} color="#FFD700" />
            </View>
            <Text style={styles.statValue}>{stats?.total_signals || 0}</Text>
            <Text style={styles.statLabel}>Total Signals</Text>
            <Text style={styles.statChange}>All time</Text>
          </View>

          <View style={styles.statCard}>
            <View style={styles.statIconContainer}>
              <Ionicons name="pulse" size={32} color="#4CAF50" />
            </View>
            <Text style={styles.statValue}>{stats?.active_signals || 0}</Text>
            <Text style={styles.statLabel}>Active Signals</Text>
            <Text style={styles.statChange}>Currently open</Text>
          </View>

          <View style={styles.statCard}>
            <View style={styles.statIconContainer}>
              <Ionicons name="trending-up" size={32} color="#2196F3" />
            </View>
            <Text style={styles.statValue}>{stats?.avg_pips.toFixed(0) || 0}</Text>
            <Text style={styles.statLabel}>Average Pips</Text>
            <Text style={styles.statChange}>Per signal</Text>
          </View>

          <View style={styles.statCard}>
            <View style={styles.statIconContainer}>
              <Ionicons name="checkmark-circle" size={32} color="#FF9800" />
            </View>
            <Text style={styles.statValue}>{stats?.total_closed || 0}</Text>
            <Text style={styles.statLabel}>Closed Signals</Text>
            <Text style={styles.statChange}>Completed</Text>
          </View>
        </View>

        {/* Performance Indicators */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Performance Indicators</Text>
          
          <View style={styles.indicatorCard}>
            <View style={styles.indicatorHeader}>
              <Ionicons name="analytics" size={24} color="#FFD700" />
              <Text style={styles.indicatorTitle}>Signal Quality</Text>
            </View>
            <View style={styles.indicatorBar}>
              <View style={[styles.indicatorFill, { width: `${winRate}%`, backgroundColor: '#4CAF50' }]} />
            </View>
            <Text style={styles.indicatorValue}>{winRate.toFixed(1)}% success rate</Text>
          </View>

          <View style={styles.indicatorCard}>
            <View style={styles.indicatorHeader}>
              <Ionicons name="speedometer" size={24} color="#2196F3" />
            </View>
              <Text style={styles.indicatorTitle}>Average Performance</Text>
            </View>
            <View style={styles.indicatorBar}>
              <View
                style={[
                  styles.indicatorFill,
                  {
                    width: `${Math.min((stats?.avg_pips || 0) * 2, 100)}%`,
                    backgroundColor: '#2196F3',
                  },
                ]}
              />
            </View>
            <Text style={styles.indicatorValue}>{stats?.avg_pips.toFixed(1)} pips average</Text>
          </View>

          <View style={styles.indicatorCard}>
            <View style={styles.indicatorHeader}>
              <Ionicons name="flag" size={24} color="#FF9800" />
              <Text style={styles.indicatorTitle}>Completion Rate</Text>
            </View>
            <View style={styles.indicatorBar}>
              <View
                style={[
                  styles.indicatorFill,
                  {
                    width: `${((stats?.total_closed || 0) / Math.max(stats?.total_signals || 1, 1)) * 100}%`,
                    backgroundColor: '#FF9800',
                  },
                ]}
              />
            </View>
            <Text style={styles.indicatorValue}>
              {(((stats?.total_closed || 0) / Math.max(stats?.total_signals || 1, 1)) * 100).toFixed(1)}% signals closed
            </Text>
          </View>
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
    marginBottom: 24,
  },
  title: {
    fontSize: 28,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  subtitle: {
    fontSize: 14,
    color: '#8B8FA8',
    marginTop: 4,
  },
  mainCard: {
    backgroundColor: '#1A1F3A',
    borderRadius: 16,
    padding: 24,
    marginBottom: 24,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  mainStatContainer: {
    alignItems: 'center',
    marginBottom: 24,
  },
  mainStatValue: {
    fontSize: 48,
    fontWeight: 'bold',
    color: '#FFD700',
    marginTop: 12,
  },
  mainStatLabel: {
    fontSize: 16,
    color: '#8B8FA8',
    marginTop: 4,
  },
  progressBarContainer: {
    width: '100%',
  },
  progressBar: {
    height: 12,
    backgroundColor: '#0A0E27',
    borderRadius: 6,
    overflow: 'hidden',
    marginBottom: 12,
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
    gap: 8,
  },
  legendDot: {
    width: 12,
    height: 12,
    borderRadius: 6,
  },
  legendText: {
    fontSize: 12,
    color: '#8B8FA8',
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
  statIconContainer: {
    marginBottom: 8,
  },
  statValue: {
    fontSize: 28,
    fontWeight: 'bold',
    color: '#FFFFFF',
    marginTop: 4,
  },
  statLabel: {
    fontSize: 12,
    color: '#FFFFFF',
    marginTop: 4,
    textAlign: 'center',
  },
  statChange: {
    fontSize: 10,
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
  indicatorCard: {
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  indicatorHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    marginBottom: 12,
  },
  indicatorTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  indicatorBar: {
    height: 8,
    backgroundColor: '#0A0E27',
    borderRadius: 4,
    overflow: 'hidden',
    marginBottom: 8,
  },
  indicatorFill: {
    height: '100%',
  },
  indicatorValue: {
    fontSize: 12,
    color: '#8B8FA8',
  },
});
