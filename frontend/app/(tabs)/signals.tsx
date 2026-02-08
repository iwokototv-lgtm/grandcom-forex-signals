import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  RefreshControl,
  ActivityIndicator,
  TouchableOpacity,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import api from '../../utils/api';
import { useAuth } from '../../contexts/AuthContext';

interface Signal {
  id: string;
  pair: string;
  type: string;
  entry_price: number;
  current_price?: number;
  tp_levels: number[];
  sl_price: number;
  confidence: number;
  analysis: string;
  timeframe: string;
  risk_reward: number;
  status: string;
  created_at: string;
  is_premium: boolean;
}

export default function SignalsScreen() {
  const { user } = useAuth();
  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [selectedSignal, setSelectedSignal] = useState<string | null>(null);

  useEffect(() => {
    loadSignals();
  }, []);

  const loadSignals = async () => {
    try {
      const response = await api.get('/signals?limit=50');
      setSignals(response.data);
    } catch (error) {
      console.error('Error loading signals:', error);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  const onRefresh = () => {
    setRefreshing(true);
    loadSignals();
  };

  const renderSignal = ({ item }: { item: Signal }) => {
    const isExpanded = selectedSignal === item.id;

    return (
      <TouchableOpacity
        style={styles.signalCard}
        onPress={() => setSelectedSignal(isExpanded ? null : item.id)}
        activeOpacity={0.8}
      >
        <View style={styles.cardHeader}>
          <View style={styles.pairContainer}>
            <Text style={styles.pairText}>{item.pair}</Text>
            {item.is_premium && (
              <View style={styles.premiumBadge}>
                <Ionicons name="star" size={12} color="#FFD700" />
                <Text style={styles.premiumText}>PREMIUM</Text>
              </View>
            )}
          </View>
          <View style={[styles.typeBadge, item.type === 'BUY' ? styles.buyBadge : styles.sellBadge]}>
            <Text style={styles.typeText}>{item.type}</Text>
          </View>
        </View>

        <View style={styles.priceRow}>
          <View style={styles.priceItem}>
            <Text style={styles.priceLabel}>Entry</Text>
            <Text style={styles.priceValue}>{item.entry_price.toFixed(2)}</Text>
          </View>
          <View style={styles.priceItem}>
            <Text style={styles.priceLabel}>TP1</Text>
            <Text style={[styles.priceValue, styles.tpValue]}>{item.tp_levels[0].toFixed(2)}</Text>
          </View>
          <View style={styles.priceItem}>
            <Text style={styles.priceLabel}>SL</Text>
            <Text style={[styles.priceValue, styles.slValue]}>{item.sl_price.toFixed(2)}</Text>
          </View>
        </View>

        <View style={styles.metaRow}>
          <View style={styles.metaItem}>
            <Ionicons name="shield-checkmark" size={16} color="#FFD700" />
            <Text style={styles.metaText}>{item.confidence}%</Text>
          </View>
          <View style={styles.metaItem}>
            <Ionicons name="time" size={16} color="#8B8FA8" />
            <Text style={styles.metaText}>{item.timeframe}</Text>
          </View>
          <View style={styles.metaItem}>
            <Ionicons name="analytics" size={16} color="#8B8FA8" />
            <Text style={styles.metaText}>R/R: {item.risk_reward.toFixed(1)}</Text>
          </View>
        </View>

        {isExpanded && (
          <View style={styles.expandedContent}>
            <View style={styles.divider} />
            <Text style={styles.analysisTitle}>Analysis</Text>
            <Text style={styles.analysisText}>{item.analysis}</Text>
            
            <Text style={styles.tpTitle}>Take Profit Levels</Text>
            {item.tp_levels.map((tp, index) => (
              <View key={index} style={styles.tpLevel}>
                <Text style={styles.tpLevelLabel}>TP{index + 1}</Text>
                <Text style={styles.tpLevelValue}>{tp.toFixed(2)}</Text>
              </View>
            ))}
            
            <Text style={styles.timestamp}>
              {new Date(item.created_at).toLocaleString()}
            </Text>
          </View>
        )}

        <View style={styles.expandIcon}>
          <Ionicons 
            name={isExpanded ? 'chevron-up' : 'chevron-down'} 
            size={20} 
            color="#8B8FA8" 
          />
        </View>
      </TouchableOpacity>
    );
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
      <View style={styles.header}>
        <Text style={styles.title}>Trading Signals</Text>
        <Text style={styles.subtitle}>
          {user?.subscription_tier === 'FREE' ? 'Free Signals Only' : 'All Signals'}
        </Text>
      </View>

      <FlatList
        data={signals}
        renderItem={renderSignal}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.listContent}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#FFD700" />
        }
        ListEmptyComponent={
          <View style={styles.emptyState}>
            <Ionicons name="bar-chart-outline" size={64} color="#8B8FA8" />
            <Text style={styles.emptyText}>No signals available</Text>
            <Text style={styles.emptySubtext}>Pull down to refresh</Text>
          </View>
        }
      />
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
  header: {
    padding: 16,
    paddingBottom: 8,
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
  listContent: {
    padding: 16,
    paddingTop: 8,
  },
  signalCard: {
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  pairContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  pairText: {
    fontSize: 20,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  premiumBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 215, 0, 0.2)',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
    gap: 4,
  },
  premiumText: {
    color: '#FFD700',
    fontSize: 10,
    fontWeight: 'bold',
  },
  typeBadge: {
    paddingHorizontal: 16,
    paddingVertical: 6,
    borderRadius: 8,
  },
  buyBadge: {
    backgroundColor: '#4CAF50',
  },
  sellBadge: {
    backgroundColor: '#F44336',
  },
  typeText: {
    color: '#FFFFFF',
    fontWeight: 'bold',
    fontSize: 14,
  },
  priceRow: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    marginBottom: 16,
  },
  priceItem: {
    alignItems: 'center',
  },
  priceLabel: {
    fontSize: 12,
    color: '#8B8FA8',
    marginBottom: 4,
  },
  priceValue: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  tpValue: {
    color: '#4CAF50',
  },
  slValue: {
    color: '#F44336',
  },
  metaRow: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: '#2A2F4A',
  },
  metaItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  metaText: {
    fontSize: 12,
    color: '#8B8FA8',
  },
  expandedContent: {
    marginTop: 16,
  },
  divider: {
    height: 1,
    backgroundColor: '#2A2F4A',
    marginBottom: 16,
  },
  analysisTitle: {
    fontSize: 14,
    fontWeight: 'bold',
    color: '#FFD700',
    marginBottom: 8,
  },
  analysisText: {
    fontSize: 14,
    color: '#FFFFFF',
    lineHeight: 20,
    marginBottom: 16,
  },
  tpTitle: {
    fontSize: 14,
    fontWeight: 'bold',
    color: '#FFD700',
    marginBottom: 8,
  },
  tpLevel: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 6,
  },
  tpLevelLabel: {
    fontSize: 14,
    color: '#8B8FA8',
  },
  tpLevelValue: {
    fontSize: 14,
    fontWeight: '600',
    color: '#4CAF50',
  },
  timestamp: {
    fontSize: 12,
    color: '#8B8FA8',
    marginTop: 12,
    textAlign: 'center',
  },
  expandIcon: {
    position: 'absolute',
    bottom: 8,
    right: 12,
  },
  emptyState: {
    alignItems: 'center',
    padding: 48,
    marginTop: 64,
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
  },
});
