import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { Picker } from '@react-native-picker/picker';
import api from '../../utils/api';

interface BacktestConfig {
  pairs: Array<{ symbol: string; name: string; type: string }>;
  timeframes: Array<{ value: string; label: string }>;
  year_range: { min: number; max: number };
}

interface BacktestResult {
  summary: {
    total_trades: number;
    winning_trades: number;
    losing_trades: number;
    win_rate: number;
    total_pips: number;
    average_pips_per_trade: number;
    max_consecutive_wins: number;
    max_consecutive_losses: number;
    max_drawdown_percent: number;
    profit_factor: number;
    sharpe_ratio: number;
    final_balance: number;
    return_percent: number;
  };
  yearly_performance: Record<string, { pips: number; trades: number; win_rate: number }>;
}

export default function BacktestScreen() {
  const router = useRouter();
  const [config, setConfig] = useState<BacktestConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);
  
  // Form state
  const [selectedPair, setSelectedPair] = useState('EURUSD');
  const [startYear, setStartYear] = useState(2020);
  const [endYear, setEndYear] = useState(2025);
  const [timeframe, setTimeframe] = useState('1h');
  const [tp1Pips, setTp1Pips] = useState(5);
  const [tp2Pips, setTp2Pips] = useState(10);
  const [tp3Pips, setTp3Pips] = useState(15);
  const [slPips, setSlPips] = useState(15);

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    try {
      const response = await api.get('/backtest/pairs');
      if (response.data.success) {
        setConfig(response.data);
      }
    } catch (error) {
      console.error('Error loading backtest config:', error);
    } finally {
      setLoading(false);
    }
  };

  const runBacktest = async () => {
    setRunning(true);
    setResult(null);
    
    try {
      const response = await api.post('/backtest/run', {
        pair: selectedPair,
        start_year: startYear,
        end_year: endYear,
        timeframe: timeframe,
        tp1_pips: tp1Pips,
        tp2_pips: tp2Pips,
        tp3_pips: tp3Pips,
        sl_pips: slPips,
        use_atr_for_sl: false,
        initial_balance: 10000,
        risk_per_trade: 0.02,
      });
      
      if (response.data.success) {
        setResult(response.data.results);
      } else {
        Alert.alert('Error', response.data.error || 'Failed to run backtest');
      }
    } catch (error: any) {
      console.error('Backtest error:', error);
      Alert.alert('Error', error.message || 'Failed to run backtest');
    } finally {
      setRunning(false);
    }
  };

  const getYears = () => {
    const years = [];
    for (let y = 2015; y <= 2025; y++) {
      years.push(y);
    }
    return years;
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color="#FFD700" />
          <Text style={styles.loadingText}>Loading backtest configuration...</Text>
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
        <Text style={styles.headerTitle}>Historical Backtest</Text>
        <View style={styles.placeholder} />
      </View>

      <ScrollView contentContainerStyle={styles.scrollContent}>
        {/* Info Card */}
        <View style={styles.infoCard}>
          <Ionicons name="analytics" size={24} color="#FFD700" />
          <Text style={styles.infoText}>
            Test your TP/SL configuration against 3-10 years of historical data to optimize your strategy.
          </Text>
        </View>

        {/* Configuration Card */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Configuration</Text>

          {/* Pair Selection */}
          <View style={styles.formGroup}>
            <Text style={styles.label}>Trading Pair</Text>
            <View style={styles.pickerContainer}>
              <Picker
                selectedValue={selectedPair}
                onValueChange={(value) => setSelectedPair(value)}
                style={styles.picker}
                dropdownIconColor="#FFD700"
              >
                {config?.pairs.map((pair) => (
                  <Picker.Item
                    key={pair.symbol}
                    label={`${pair.symbol} - ${pair.name}`}
                    value={pair.symbol}
                    color="#FFFFFF"
                  />
                ))}
              </Picker>
            </View>
          </View>

          {/* Date Range */}
          <View style={styles.rowGroup}>
            <View style={[styles.formGroup, { flex: 1, marginRight: 8 }]}>
              <Text style={styles.label}>Start Year</Text>
              <View style={styles.pickerContainer}>
                <Picker
                  selectedValue={startYear}
                  onValueChange={(value) => setStartYear(value)}
                  style={styles.picker}
                  dropdownIconColor="#FFD700"
                >
                  {getYears().map((year) => (
                    <Picker.Item key={year} label={year.toString()} value={year} color="#FFFFFF" />
                  ))}
                </Picker>
              </View>
            </View>
            <View style={[styles.formGroup, { flex: 1, marginLeft: 8 }]}>
              <Text style={styles.label}>End Year</Text>
              <View style={styles.pickerContainer}>
                <Picker
                  selectedValue={endYear}
                  onValueChange={(value) => setEndYear(value)}
                  style={styles.picker}
                  dropdownIconColor="#FFD700"
                >
                  {getYears().map((year) => (
                    <Picker.Item key={year} label={year.toString()} value={year} color="#FFFFFF" />
                  ))}
                </Picker>
              </View>
            </View>
          </View>

          {/* Timeframe */}
          <View style={styles.formGroup}>
            <Text style={styles.label}>Timeframe</Text>
            <View style={styles.pickerContainer}>
              <Picker
                selectedValue={timeframe}
                onValueChange={(value) => setTimeframe(value)}
                style={styles.picker}
                dropdownIconColor="#FFD700"
              >
                {config?.timeframes.map((tf) => (
                  <Picker.Item key={tf.value} label={tf.label} value={tf.value} color="#FFFFFF" />
                ))}
              </Picker>
            </View>
          </View>
        </View>

        {/* TP/SL Configuration */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Take Profit & Stop Loss (Pips)</Text>

          <View style={styles.rowGroup}>
            <View style={[styles.formGroup, { flex: 1 }]}>
              <Text style={styles.label}>TP1</Text>
              <View style={styles.pickerContainer}>
                <Picker
                  selectedValue={tp1Pips}
                  onValueChange={(value) => setTp1Pips(value)}
                  style={styles.picker}
                  dropdownIconColor="#4CAF50"
                >
                  {[3, 4, 5, 6, 7, 8, 10, 12, 15].map((v) => (
                    <Picker.Item key={v} label={`${v} pips`} value={v} color="#FFFFFF" />
                  ))}
                </Picker>
              </View>
            </View>
            <View style={[styles.formGroup, { flex: 1, marginHorizontal: 8 }]}>
              <Text style={styles.label}>TP2</Text>
              <View style={styles.pickerContainer}>
                <Picker
                  selectedValue={tp2Pips}
                  onValueChange={(value) => setTp2Pips(value)}
                  style={styles.picker}
                  dropdownIconColor="#4CAF50"
                >
                  {[6, 8, 10, 12, 15, 18, 20, 25, 30].map((v) => (
                    <Picker.Item key={v} label={`${v} pips`} value={v} color="#FFFFFF" />
                  ))}
                </Picker>
              </View>
            </View>
            <View style={[styles.formGroup, { flex: 1 }]}>
              <Text style={styles.label}>TP3</Text>
              <View style={styles.pickerContainer}>
                <Picker
                  selectedValue={tp3Pips}
                  onValueChange={(value) => setTp3Pips(value)}
                  style={styles.picker}
                  dropdownIconColor="#4CAF50"
                >
                  {[10, 12, 15, 18, 20, 25, 30, 40, 50].map((v) => (
                    <Picker.Item key={v} label={`${v} pips`} value={v} color="#FFFFFF" />
                  ))}
                </Picker>
              </View>
            </View>
          </View>

          <View style={styles.formGroup}>
            <Text style={styles.label}>Stop Loss</Text>
            <View style={styles.pickerContainer}>
              <Picker
                selectedValue={slPips}
                onValueChange={(value) => setSlPips(value)}
                style={styles.picker}
                dropdownIconColor="#F44336"
              >
                {[5, 8, 10, 12, 15, 18, 20, 25, 30, 35, 40].map((v) => (
                  <Picker.Item key={v} label={`${v} pips`} value={v} color="#FFFFFF" />
                ))}
              </Picker>
            </View>
          </View>
        </View>

        {/* Run Backtest Button */}
        <TouchableOpacity
          style={[styles.runButton, running && styles.runButtonDisabled]}
          onPress={runBacktest}
          disabled={running}
        >
          {running ? (
            <>
              <ActivityIndicator size="small" color="#0A0E27" />
              <Text style={styles.runButtonText}>Running Backtest...</Text>
            </>
          ) : (
            <>
              <Ionicons name="play-circle" size={24} color="#0A0E27" />
              <Text style={styles.runButtonText}>Run Backtest ({endYear - startYear} years)</Text>
            </>
          )}
        </TouchableOpacity>

        {/* Results */}
        {result && (
          <View style={styles.resultsCard}>
            <Text style={styles.resultsTitle}>Backtest Results</Text>
            
            {/* Summary Stats */}
            <View style={styles.statsGrid}>
              <View style={styles.statBox}>
                <Text style={styles.statValue}>{result.summary.total_trades}</Text>
                <Text style={styles.statLabel}>Total Trades</Text>
              </View>
              <View style={styles.statBox}>
                <Text style={[styles.statValue, { color: '#4CAF50' }]}>
                  {result.summary.win_rate}%
                </Text>
                <Text style={styles.statLabel}>Win Rate</Text>
              </View>
              <View style={styles.statBox}>
                <Text style={[
                  styles.statValue,
                  { color: result.summary.total_pips >= 0 ? '#4CAF50' : '#F44336' }
                ]}>
                  {result.summary.total_pips >= 0 ? '+' : ''}{result.summary.total_pips}
                </Text>
                <Text style={styles.statLabel}>Total Pips</Text>
              </View>
              <View style={styles.statBox}>
                <Text style={styles.statValue}>{result.summary.profit_factor}</Text>
                <Text style={styles.statLabel}>Profit Factor</Text>
              </View>
            </View>

            <View style={styles.divider} />

            {/* Additional Stats */}
            <View style={styles.detailsGrid}>
              <View style={styles.detailRow}>
                <Text style={styles.detailLabel}>Winning Trades</Text>
                <Text style={[styles.detailValue, { color: '#4CAF50' }]}>
                  {result.summary.winning_trades}
                </Text>
              </View>
              <View style={styles.detailRow}>
                <Text style={styles.detailLabel}>Losing Trades</Text>
                <Text style={[styles.detailValue, { color: '#F44336' }]}>
                  {result.summary.losing_trades}
                </Text>
              </View>
              <View style={styles.detailRow}>
                <Text style={styles.detailLabel}>Avg Pips/Trade</Text>
                <Text style={styles.detailValue}>{result.summary.average_pips_per_trade}</Text>
              </View>
              <View style={styles.detailRow}>
                <Text style={styles.detailLabel}>Max Drawdown</Text>
                <Text style={[styles.detailValue, { color: '#F44336' }]}>
                  {result.summary.max_drawdown_percent}%
                </Text>
              </View>
              <View style={styles.detailRow}>
                <Text style={styles.detailLabel}>Max Consecutive Wins</Text>
                <Text style={[styles.detailValue, { color: '#4CAF50' }]}>
                  {result.summary.max_consecutive_wins}
                </Text>
              </View>
              <View style={styles.detailRow}>
                <Text style={styles.detailLabel}>Max Consecutive Losses</Text>
                <Text style={[styles.detailValue, { color: '#F44336' }]}>
                  {result.summary.max_consecutive_losses}
                </Text>
              </View>
              <View style={styles.detailRow}>
                <Text style={styles.detailLabel}>Sharpe Ratio</Text>
                <Text style={styles.detailValue}>{result.summary.sharpe_ratio}</Text>
              </View>
              <View style={styles.detailRow}>
                <Text style={styles.detailLabel}>Return</Text>
                <Text style={[
                  styles.detailValue,
                  { color: result.summary.return_percent >= 0 ? '#4CAF50' : '#F44336' }
                ]}>
                  {result.summary.return_percent >= 0 ? '+' : ''}{result.summary.return_percent}%
                </Text>
              </View>
            </View>

            {/* Yearly Performance */}
            {Object.keys(result.yearly_performance).length > 0 && (
              <>
                <View style={styles.divider} />
                <Text style={styles.sectionTitle}>Yearly Performance</Text>
                {Object.entries(result.yearly_performance)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([year, data]) => (
                    <View key={year} style={styles.yearRow}>
                      <Text style={styles.yearLabel}>{year}</Text>
                      <View style={styles.yearStats}>
                        <Text style={[
                          styles.yearPips,
                          { color: data.pips >= 0 ? '#4CAF50' : '#F44336' }
                        ]}>
                          {data.pips >= 0 ? '+' : ''}{data.pips} pips
                        </Text>
                        <Text style={styles.yearTrades}>{data.trades} trades</Text>
                        <Text style={styles.yearWinRate}>{data.win_rate}%</Text>
                      </View>
                    </View>
                  ))}
              </>
            )}
          </View>
        )}
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
  placeholder: {
    width: 40,
  },
  scrollContent: {
    padding: 16,
    paddingBottom: 40,
  },
  infoCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255, 215, 0, 0.1)',
    borderRadius: 12,
    padding: 16,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: 'rgba(255, 215, 0, 0.3)',
    gap: 12,
  },
  infoText: {
    flex: 1,
    color: '#FFD700',
    fontSize: 14,
    lineHeight: 20,
  },
  card: {
    backgroundColor: '#1A1F3A',
    borderRadius: 16,
    padding: 20,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  cardTitle: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#FFFFFF',
    marginBottom: 16,
  },
  formGroup: {
    marginBottom: 16,
  },
  rowGroup: {
    flexDirection: 'row',
    marginBottom: 16,
  },
  label: {
    fontSize: 12,
    color: '#8B8FA8',
    marginBottom: 8,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  pickerContainer: {
    backgroundColor: '#0A0E27',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#2A2F4A',
    overflow: 'hidden',
  },
  picker: {
    color: '#FFFFFF',
    height: 50,
  },
  runButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#FFD700',
    borderRadius: 12,
    paddingVertical: 16,
    gap: 8,
    marginBottom: 24,
  },
  runButtonDisabled: {
    opacity: 0.7,
  },
  runButtonText: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#0A0E27',
  },
  resultsCard: {
    backgroundColor: '#1A1F3A',
    borderRadius: 16,
    padding: 20,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  resultsTitle: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#FFD700',
    marginBottom: 20,
    textAlign: 'center',
  },
  statsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'space-between',
    marginBottom: 16,
  },
  statBox: {
    width: '48%',
    backgroundColor: '#0A0E27',
    borderRadius: 12,
    padding: 16,
    alignItems: 'center',
    marginBottom: 12,
  },
  statValue: {
    fontSize: 24,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  statLabel: {
    fontSize: 12,
    color: '#8B8FA8',
    marginTop: 4,
    textTransform: 'uppercase',
  },
  divider: {
    height: 1,
    backgroundColor: '#2A2F4A',
    marginVertical: 16,
  },
  detailsGrid: {
    gap: 8,
  },
  detailRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 8,
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
  sectionTitle: {
    fontSize: 14,
    fontWeight: 'bold',
    color: '#FFFFFF',
    marginBottom: 12,
    textTransform: 'uppercase',
  },
  yearRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#2A2F4A',
  },
  yearLabel: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  yearStats: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 16,
  },
  yearPips: {
    fontSize: 14,
    fontWeight: '600',
  },
  yearTrades: {
    fontSize: 12,
    color: '#8B8FA8',
  },
  yearWinRate: {
    fontSize: 12,
    color: '#FFD700',
    fontWeight: '600',
  },
});
