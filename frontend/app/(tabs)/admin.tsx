import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  Alert,
  TextInput,
  Modal,
  RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import api from '../../utils/api';

interface Signal {
  id: string;
  pair: string;
  type: string;
  entry_price: number;
  tp_levels: number[];
  sl_price: number;
  status: string;
  result?: string;
  pips?: number;
  created_at: string;
  regime?: string;
}

interface User {
  id: string;
  email: string;
  role: string;
  created_at: string;
  subscription_status?: string;
}

interface SystemStats {
  total_signals: number;
  active_signals: number;
  total_users: number;
  signals_today: number;
  win_rate: number;
}

export default function AdminScreen() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [activeTab, setActiveTab] = useState<'overview' | 'signals' | 'users' | 'settings'>('overview');
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [showSignalModal, setShowSignalModal] = useState(false);
  const [selectedSignal, setSelectedSignal] = useState<Signal | null>(null);

  useEffect(() => {
    loadAdminData();
  }, []);

  const loadAdminData = async () => {
    try {
      await Promise.all([loadStats(), loadSignals(), loadUsers()]);
    } catch (error) {
      console.error('Error loading admin data:', error);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  const loadStats = async () => {
    try {
      const [statsRes, trackerRes] = await Promise.all([
        api.get('/stats'),
        api.get('/signals/tracker-status')
      ]);
      
      setStats({
        total_signals: statsRes.data.total_signals || 0,
        active_signals: statsRes.data.active_signals || 0,
        total_users: 0, // Will be updated from users endpoint
        signals_today: trackerRes.data.closed_today || 0,
        win_rate: statsRes.data.win_rate || 0,
      });
    } catch (error) {
      console.error('Error loading stats:', error);
    }
  };

  const loadSignals = async () => {
    try {
      const response = await api.get('/signals?limit=50');
      if (Array.isArray(response.data)) {
        setSignals(response.data);
      }
    } catch (error) {
      console.error('Error loading signals:', error);
    }
  };

  const loadUsers = async () => {
    try {
      const response = await api.get('/admin/users');
      if (response.data.success) {
        setUsers(response.data.users);
        setStats(prev => prev ? { ...prev, total_users: response.data.users.length } : null);
      }
    } catch (error) {
      console.error('Error loading users:', error);
    }
  };

  const handleCloseSignal = async (signalId: string, status: string) => {
    try {
      const response = await api.post(`/admin/signals/${signalId}/close`, { status });
      if (response.data.success) {
        Alert.alert('Success', 'Signal closed successfully');
        loadSignals();
        setShowSignalModal(false);
      }
    } catch (error) {
      Alert.alert('Error', 'Failed to close signal');
    }
  };

  const handleDeleteSignal = async (signalId: string) => {
    Alert.alert(
      'Confirm Delete',
      'Are you sure you want to delete this signal?',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            try {
              const response = await api.delete(`/admin/signals/${signalId}`);
              if (response.data.success) {
                Alert.alert('Success', 'Signal deleted');
                loadSignals();
                setShowSignalModal(false);
              }
            } catch (error) {
              Alert.alert('Error', 'Failed to delete signal');
            }
          }
        }
      ]
    );
  };

  const renderOverview = () => (
    <View>
      {/* Stats Cards */}
      <View style={styles.statsGrid}>
        <View style={[styles.statsCard, { backgroundColor: '#1E3A5F' }]}>
          <Ionicons name="stats-chart" size={32} color="#4CAF50" />
          <Text style={styles.statsValue}>{stats?.total_signals || 0}</Text>
          <Text style={styles.statsLabel}>Total Signals</Text>
        </View>
        <View style={[styles.statsCard, { backgroundColor: '#1E3A5F' }]}>
          <Ionicons name="pulse" size={32} color="#FFD700" />
          <Text style={styles.statsValue}>{stats?.active_signals || 0}</Text>
          <Text style={styles.statsLabel}>Active</Text>
        </View>
        <View style={[styles.statsCard, { backgroundColor: '#1E3A5F' }]}>
          <Ionicons name="people" size={32} color="#2196F3" />
          <Text style={styles.statsValue}>{stats?.total_users || 0}</Text>
          <Text style={styles.statsLabel}>Users</Text>
        </View>
        <View style={[styles.statsCard, { backgroundColor: '#1E3A5F' }]}>
          <Ionicons name="trending-up" size={32} color="#4CAF50" />
          <Text style={styles.statsValue}>{stats?.win_rate || 0}%</Text>
          <Text style={styles.statsLabel}>Win Rate</Text>
        </View>
      </View>

      {/* Quick Actions */}
      <Text style={styles.sectionTitle}>Quick Actions</Text>
      <View style={styles.actionsContainer}>
        <TouchableOpacity 
          style={styles.actionButton}
          onPress={() => api.post('/signals/check-outcomes').then(() => Alert.alert('Done', 'Outcome check triggered'))}
        >
          <Ionicons name="refresh" size={24} color="#FFD700" />
          <Text style={styles.actionText}>Check Outcomes</Text>
        </TouchableOpacity>
        <TouchableOpacity 
          style={styles.actionButton}
          onPress={() => router.push('/(tabs)/backtest')}
        >
          <Ionicons name="analytics" size={24} color="#FFD700" />
          <Text style={styles.actionText}>Run Backtest</Text>
        </TouchableOpacity>
      </View>

      {/* Recent Activity */}
      <Text style={styles.sectionTitle}>Recent Signals</Text>
      {signals.slice(0, 5).map((signal) => (
        <TouchableOpacity 
          key={signal.id} 
          style={styles.signalRow}
          onPress={() => {
            setSelectedSignal(signal);
            setShowSignalModal(true);
          }}
        >
          <View style={styles.signalInfo}>
            <Text style={styles.signalPair}>{signal.pair}</Text>
            <Text style={[
              styles.signalType,
              { color: signal.type === 'BUY' ? '#4CAF50' : '#F44336' }
            ]}>
              {signal.type}
            </Text>
          </View>
          <View style={styles.signalMeta}>
            <Text style={[
              styles.signalStatus,
              { color: signal.status === 'ACTIVE' ? '#FFD700' : '#8B8FA8' }
            ]}>
              {signal.status}
            </Text>
            {signal.pips !== undefined && (
              <Text style={[
                styles.signalPips,
                { color: signal.pips >= 0 ? '#4CAF50' : '#F44336' }
              ]}>
                {signal.pips >= 0 ? '+' : ''}{signal.pips?.toFixed(1)} pips
              </Text>
            )}
          </View>
        </TouchableOpacity>
      ))}
    </View>
  );

  const renderSignals = () => (
    <View>
      <Text style={styles.sectionTitle}>All Signals ({signals.length})</Text>
      {signals.map((signal) => (
        <TouchableOpacity 
          key={signal.id} 
          style={styles.signalCard}
          onPress={() => {
            setSelectedSignal(signal);
            setShowSignalModal(true);
          }}
        >
          <View style={styles.signalHeader}>
            <View style={styles.signalPairContainer}>
              <Text style={styles.signalPairLarge}>{signal.pair}</Text>
              <View style={[
                styles.typeBadge,
                { backgroundColor: signal.type === 'BUY' ? '#4CAF50' : '#F44336' }
              ]}>
                <Text style={styles.typeBadgeText}>{signal.type}</Text>
              </View>
            </View>
            <View style={[
              styles.statusBadge,
              { backgroundColor: signal.status === 'ACTIVE' ? '#FFD700' : '#2A2F4A' }
            ]}>
              <Text style={[
                styles.statusBadgeText,
                { color: signal.status === 'ACTIVE' ? '#0A0E27' : '#8B8FA8' }
              ]}>
                {signal.status}
              </Text>
            </View>
          </View>
          <View style={styles.signalDetails}>
            <View style={styles.detailItem}>
              <Text style={styles.detailLabel}>Entry</Text>
              <Text style={styles.detailValue}>{signal.entry_price}</Text>
            </View>
            <View style={styles.detailItem}>
              <Text style={styles.detailLabel}>TP1/TP2/TP3</Text>
              <Text style={styles.detailValue}>
                {signal.tp_levels?.join(' / ') || 'N/A'}
              </Text>
            </View>
            <View style={styles.detailItem}>
              <Text style={styles.detailLabel}>SL</Text>
              <Text style={styles.detailValue}>{signal.sl_price}</Text>
            </View>
          </View>
          {signal.pips !== undefined && (
            <View style={styles.pipsContainer}>
              <Text style={[
                styles.pipsValue,
                { color: signal.pips >= 0 ? '#4CAF50' : '#F44336' }
              ]}>
                {signal.pips >= 0 ? '+' : ''}{signal.pips?.toFixed(1)} pips
              </Text>
              <Text style={styles.resultText}>{signal.result}</Text>
            </View>
          )}
        </TouchableOpacity>
      ))}
    </View>
  );

  const renderUsers = () => (
    <View>
      <Text style={styles.sectionTitle}>Users ({users.length})</Text>
      {users.map((user) => (
        <View key={user.id} style={styles.userCard}>
          <View style={styles.userInfo}>
            <Ionicons name="person-circle" size={40} color="#FFD700" />
            <View style={styles.userDetails}>
              <Text style={styles.userEmail}>{user.email}</Text>
              <Text style={styles.userRole}>{user.role}</Text>
            </View>
          </View>
          <View style={[
            styles.subscriptionBadge,
            { backgroundColor: user.subscription_status === 'active' ? '#4CAF50' : '#2A2F4A' }
          ]}>
            <Text style={styles.subscriptionText}>
              {user.subscription_status || 'Free'}
            </Text>
          </View>
        </View>
      ))}
    </View>
  );

  const renderSettings = () => (
    <View>
      <Text style={styles.sectionTitle}>System Settings</Text>
      
      <View style={styles.settingCard}>
        <Text style={styles.settingTitle}>Signal Generation</Text>
        <View style={styles.settingRow}>
          <Text style={styles.settingLabel}>Interval</Text>
          <Text style={styles.settingValue}>Every 15 minutes</Text>
        </View>
        <View style={styles.settingRow}>
          <Text style={styles.settingLabel}>Pairs</Text>
          <Text style={styles.settingValue}>11 active</Text>
        </View>
      </View>

      <View style={styles.settingCard}>
        <Text style={styles.settingTitle}>TP/SL Configuration</Text>
        <View style={styles.settingRow}>
          <Text style={styles.settingLabel}>TP1</Text>
          <Text style={styles.settingValue}>5 pips (33% close)</Text>
        </View>
        <View style={styles.settingRow}>
          <Text style={styles.settingLabel}>TP2</Text>
          <Text style={styles.settingValue}>10 pips (33% close)</Text>
        </View>
        <View style={styles.settingRow}>
          <Text style={styles.settingLabel}>TP3</Text>
          <Text style={styles.settingValue}>15 pips (34% close)</Text>
        </View>
        <View style={styles.settingRow}>
          <Text style={styles.settingLabel}>SL</Text>
          <Text style={styles.settingValue}>ATR-based</Text>
        </View>
      </View>

      <View style={styles.settingCard}>
        <Text style={styles.settingTitle}>Outcome Tracker</Text>
        <View style={styles.settingRow}>
          <Text style={styles.settingLabel}>Status</Text>
          <Text style={[styles.settingValue, { color: '#4CAF50' }]}>Running</Text>
        </View>
        <View style={styles.settingRow}>
          <Text style={styles.settingLabel}>Check Interval</Text>
          <Text style={styles.settingValue}>60 seconds</Text>
        </View>
      </View>
    </View>
  );

  if (loading) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color="#FFD700" />
          <Text style={styles.loadingText}>Loading admin panel...</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backButton}>
          <Ionicons name="arrow-back" size={24} color="#FFFFFF" />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Admin Panel</Text>
        <TouchableOpacity onPress={loadAdminData}>
          <Ionicons name="refresh" size={24} color="#FFD700" />
        </TouchableOpacity>
      </View>

      {/* Tab Bar */}
      <View style={styles.tabBar}>
        {(['overview', 'signals', 'users', 'settings'] as const).map((tab) => (
          <TouchableOpacity
            key={tab}
            style={[styles.tab, activeTab === tab && styles.tabActive]}
            onPress={() => setActiveTab(tab)}
          >
            <Ionicons 
              name={
                tab === 'overview' ? 'grid' :
                tab === 'signals' ? 'stats-chart' :
                tab === 'users' ? 'people' : 'settings'
              } 
              size={20} 
              color={activeTab === tab ? '#FFD700' : '#8B8FA8'} 
            />
            <Text style={[styles.tabText, activeTab === tab && styles.tabTextActive]}>
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      <ScrollView 
        contentContainerStyle={styles.scrollContent}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={loadAdminData} tintColor="#FFD700" />
        }
      >
        {activeTab === 'overview' && renderOverview()}
        {activeTab === 'signals' && renderSignals()}
        {activeTab === 'users' && renderUsers()}
        {activeTab === 'settings' && renderSettings()}
      </ScrollView>

      {/* Signal Detail Modal */}
      <Modal visible={showSignalModal} transparent animationType="slide">
        <View style={styles.modalOverlay}>
          <View style={styles.modalContent}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>
                {selectedSignal?.pair} {selectedSignal?.type}
              </Text>
              <TouchableOpacity onPress={() => setShowSignalModal(false)}>
                <Ionicons name="close" size={24} color="#FFFFFF" />
              </TouchableOpacity>
            </View>
            
            {selectedSignal && (
              <>
                <View style={styles.modalDetails}>
                  <View style={styles.modalRow}>
                    <Text style={styles.modalLabel}>Status</Text>
                    <Text style={styles.modalValue}>{selectedSignal.status}</Text>
                  </View>
                  <View style={styles.modalRow}>
                    <Text style={styles.modalLabel}>Entry</Text>
                    <Text style={styles.modalValue}>{selectedSignal.entry_price}</Text>
                  </View>
                  <View style={styles.modalRow}>
                    <Text style={styles.modalLabel}>TP Levels</Text>
                    <Text style={styles.modalValue}>{selectedSignal.tp_levels?.join(' / ')}</Text>
                  </View>
                  <View style={styles.modalRow}>
                    <Text style={styles.modalLabel}>Stop Loss</Text>
                    <Text style={styles.modalValue}>{selectedSignal.sl_price}</Text>
                  </View>
                  {selectedSignal.pips !== undefined && (
                    <View style={styles.modalRow}>
                      <Text style={styles.modalLabel}>Pips</Text>
                      <Text style={[
                        styles.modalValue,
                        { color: selectedSignal.pips >= 0 ? '#4CAF50' : '#F44336' }
                      ]}>
                        {selectedSignal.pips >= 0 ? '+' : ''}{selectedSignal.pips?.toFixed(1)}
                      </Text>
                    </View>
                  )}
                </View>

                {selectedSignal.status === 'ACTIVE' && (
                  <View style={styles.modalActions}>
                    <TouchableOpacity 
                      style={[styles.modalButton, { backgroundColor: '#4CAF50' }]}
                      onPress={() => handleCloseSignal(selectedSignal.id, 'CLOSED_MANUAL_WIN')}
                    >
                      <Text style={styles.modalButtonText}>Close as Win</Text>
                    </TouchableOpacity>
                    <TouchableOpacity 
                      style={[styles.modalButton, { backgroundColor: '#F44336' }]}
                      onPress={() => handleCloseSignal(selectedSignal.id, 'CLOSED_MANUAL_LOSS')}
                    >
                      <Text style={styles.modalButtonText}>Close as Loss</Text>
                    </TouchableOpacity>
                  </View>
                )}

                <TouchableOpacity 
                  style={[styles.modalButton, { backgroundColor: '#FF5722', marginTop: 12 }]}
                  onPress={() => handleDeleteSignal(selectedSignal.id)}
                >
                  <Text style={styles.modalButtonText}>Delete Signal</Text>
                </TouchableOpacity>
              </>
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
    justifyContent: 'center',
    alignItems: 'center',
  },
  loadingText: {
    marginTop: 16,
    color: '#8B8FA8',
    fontSize: 14,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#1A1F3A',
  },
  backButton: {
    padding: 8,
  },
  headerTitle: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  tabBar: {
    flexDirection: 'row',
    backgroundColor: '#1A1F3A',
    paddingVertical: 8,
  },
  tab: {
    flex: 1,
    alignItems: 'center',
    paddingVertical: 8,
    gap: 4,
  },
  tabActive: {
    borderBottomWidth: 2,
    borderBottomColor: '#FFD700',
  },
  tabText: {
    fontSize: 12,
    color: '#8B8FA8',
  },
  tabTextActive: {
    color: '#FFD700',
  },
  scrollContent: {
    padding: 16,
    paddingBottom: 40,
  },
  statsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
    marginBottom: 24,
  },
  statsCard: {
    flex: 1,
    minWidth: '45%',
    borderRadius: 12,
    padding: 16,
    alignItems: 'center',
  },
  statsValue: {
    fontSize: 28,
    fontWeight: 'bold',
    color: '#FFFFFF',
    marginTop: 8,
  },
  statsLabel: {
    fontSize: 12,
    color: '#8B8FA8',
    marginTop: 4,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#FFFFFF',
    marginBottom: 12,
    marginTop: 8,
  },
  actionsContainer: {
    flexDirection: 'row',
    gap: 12,
    marginBottom: 24,
  },
  actionButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 16,
    gap: 8,
    borderWidth: 1,
    borderColor: '#FFD700',
  },
  actionText: {
    color: '#FFD700',
    fontWeight: '600',
  },
  signalRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: '#1A1F3A',
    borderRadius: 8,
    padding: 12,
    marginBottom: 8,
  },
  signalInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  signalPair: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  signalType: {
    fontSize: 14,
    fontWeight: 'bold',
  },
  signalMeta: {
    alignItems: 'flex-end',
  },
  signalStatus: {
    fontSize: 12,
    fontWeight: '600',
  },
  signalPips: {
    fontSize: 14,
    fontWeight: 'bold',
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
  signalPairContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  signalPairLarge: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  typeBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 4,
  },
  typeBadgeText: {
    color: '#FFFFFF',
    fontSize: 12,
    fontWeight: 'bold',
  },
  statusBadge: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  statusBadgeText: {
    fontSize: 12,
    fontWeight: '600',
  },
  signalDetails: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  detailItem: {
    flex: 1,
  },
  detailLabel: {
    fontSize: 10,
    color: '#8B8FA8',
    marginBottom: 2,
  },
  detailValue: {
    fontSize: 12,
    color: '#FFFFFF',
  },
  pipsContainer: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 12,
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: '#2A2F4A',
  },
  pipsValue: {
    fontSize: 18,
    fontWeight: 'bold',
  },
  resultText: {
    fontSize: 14,
    color: '#8B8FA8',
  },
  userCard: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
  },
  userInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  userDetails: {},
  userEmail: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  userRole: {
    fontSize: 12,
    color: '#8B8FA8',
    marginTop: 2,
  },
  subscriptionBadge: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 12,
  },
  subscriptionText: {
    fontSize: 12,
    color: '#FFFFFF',
    fontWeight: '600',
  },
  settingCard: {
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 16,
    marginBottom: 16,
  },
  settingTitle: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#FFD700',
    marginBottom: 12,
  },
  settingRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#2A2F4A',
  },
  settingLabel: {
    fontSize: 14,
    color: '#8B8FA8',
  },
  settingValue: {
    fontSize: 14,
    color: '#FFFFFF',
    fontWeight: '600',
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.8)',
    justifyContent: 'flex-end',
  },
  modalContent: {
    backgroundColor: '#1A1F3A',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 20,
    maxHeight: '80%',
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
  modalDetails: {
    marginBottom: 20,
  },
  modalRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#2A2F4A',
  },
  modalLabel: {
    fontSize: 14,
    color: '#8B8FA8',
  },
  modalValue: {
    fontSize: 14,
    color: '#FFFFFF',
    fontWeight: '600',
  },
  modalActions: {
    flexDirection: 'row',
    gap: 12,
  },
  modalButton: {
    flex: 1,
    paddingVertical: 14,
    borderRadius: 8,
    alignItems: 'center',
  },
  modalButtonText: {
    color: '#FFFFFF',
    fontWeight: 'bold',
    fontSize: 14,
  },
});
