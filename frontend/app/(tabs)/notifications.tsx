import React, { useState, useEffect, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  Switch,
  Alert,
  Platform,
  ActivityIndicator,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';
import Constants from 'expo-constants';
import api from '../../utils/api';

// Configure notification handler
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: true,
  }),
});

export default function NotificationsScreen() {
  const router = useRouter();
  const [pushEnabled, setPushEnabled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [registering, setRegistering] = useState(false);
  const [expoPushToken, setExpoPushToken] = useState<string | null>(null);
  const notificationListener = useRef<Notifications.EventSubscription>();
  const responseListener = useRef<Notifications.EventSubscription>();

  useEffect(() => {
    checkNotificationStatus();
    
    // Listen for incoming notifications
    notificationListener.current = Notifications.addNotificationReceivedListener(notification => {
      console.log('Notification received:', notification);
    });

    // Listen for notification taps
    responseListener.current = Notifications.addNotificationResponseReceivedListener(response => {
      console.log('Notification tapped:', response);
      const data = response.notification.request.content.data;
      
      // Navigate based on notification type
      if (data?.type === 'new_signal') {
        router.push('/(tabs)/home');
      } else if (data?.type === 'trade_closed') {
        router.push('/(tabs)/signals');
      }
    });

    return () => {
      if (notificationListener.current) {
        Notifications.removeNotificationSubscription(notificationListener.current);
      }
      if (responseListener.current) {
        Notifications.removeNotificationSubscription(responseListener.current);
      }
    };
  }, []);

  const checkNotificationStatus = async () => {
    try {
      const { status } = await Notifications.getPermissionsAsync();
      
      if (status === 'granted') {
        // Check if we have a token registered
        const token = await registerForPushNotificationsAsync();
        if (token) {
          setExpoPushToken(token);
          setPushEnabled(true);
        }
      }
    } catch (error) {
      console.error('Error checking notification status:', error);
    } finally {
      setLoading(false);
    }
  };

  const registerForPushNotificationsAsync = async (): Promise<string | null> => {
    let token: string | null = null;

    if (Platform.OS === 'android') {
      await Notifications.setNotificationChannelAsync('signals', {
        name: 'Trading Signals',
        importance: Notifications.AndroidImportance.MAX,
        vibrationPattern: [0, 250, 250, 250],
        lightColor: '#FFD700',
        sound: 'default',
      });
    }

    if (Device.isDevice) {
      const { status: existingStatus } = await Notifications.getPermissionsAsync();
      let finalStatus = existingStatus;
      
      if (existingStatus !== 'granted') {
        const { status } = await Notifications.requestPermissionsAsync();
        finalStatus = status;
      }
      
      if (finalStatus !== 'granted') {
        return null;
      }

      try {
        const projectId = Constants.expoConfig?.extra?.eas?.projectId ?? Constants.easConfig?.projectId;
        token = (await Notifications.getExpoPushTokenAsync({ projectId })).data;
      } catch (error) {
        console.error('Error getting push token:', error);
        // Fallback for development
        token = (await Notifications.getExpoPushTokenAsync()).data;
      }
    } else {
      Alert.alert('Notice', 'Push notifications require a physical device');
    }

    return token;
  };

  const handleTogglePush = async (value: boolean) => {
    setRegistering(true);
    
    try {
      if (value) {
        // Enable push notifications
        const token = await registerForPushNotificationsAsync();
        
        if (!token) {
          Alert.alert(
            'Permission Required',
            'Please enable notifications in your device settings to receive trading signals.',
            [
              { text: 'Cancel', style: 'cancel' },
              { text: 'Open Settings', onPress: () => Notifications.openSettings() },
            ]
          );
          setRegistering(false);
          return;
        }

        // Register token with backend
        const response = await api.post('/notifications/register', {
          push_token: token,
          device_type: Platform.OS,
        });

        if (response.data.success) {
          setExpoPushToken(token);
          setPushEnabled(true);
          Alert.alert('Success', 'Push notifications enabled! You will receive alerts for new signals.');
        } else {
          throw new Error('Failed to register token');
        }
      } else {
        // Disable push notifications
        await api.delete('/notifications/unregister');
        setExpoPushToken(null);
        setPushEnabled(false);
        Alert.alert('Disabled', 'Push notifications have been disabled.');
      }
    } catch (error) {
      console.error('Error toggling push notifications:', error);
      Alert.alert('Error', 'Failed to update notification settings. Please try again.');
    } finally {
      setRegistering(false);
    }
  };

  const handleTestNotification = async () => {
    if (!pushEnabled) {
      Alert.alert('Not Enabled', 'Please enable push notifications first.');
      return;
    }

    try {
      const response = await api.post('/notifications/test');
      if (response.data.success) {
        Alert.alert('Test Sent', 'A test notification has been sent to your device.');
      } else {
        Alert.alert('Failed', response.data.error || 'Failed to send test notification.');
      }
    } catch (error) {
      console.error('Error sending test notification:', error);
      Alert.alert('Error', 'Failed to send test notification.');
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color="#FFD700" />
          <Text style={styles.loadingText}>Checking notification settings...</Text>
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
        <Text style={styles.headerTitle}>Notifications</Text>
        <View style={styles.placeholder} />
      </View>

      <ScrollView contentContainerStyle={styles.scrollContent}>
        {/* Push Notifications Toggle */}
        <View style={styles.card}>
          <View style={styles.cardHeader}>
            <View style={styles.iconContainer}>
              <Ionicons name="notifications" size={28} color="#FFD700" />
            </View>
            <View style={styles.cardTitleContainer}>
              <Text style={styles.cardTitle}>Push Notifications</Text>
              <Text style={styles.cardSubtitle}>
                {pushEnabled ? 'Enabled' : 'Disabled'}
              </Text>
            </View>
            {registering ? (
              <ActivityIndicator size="small" color="#FFD700" />
            ) : (
              <Switch
                value={pushEnabled}
                onValueChange={handleTogglePush}
                trackColor={{ false: '#3A3F5A', true: '#FFD700' }}
                thumbColor={pushEnabled ? '#FFFFFF' : '#8B8FA8'}
              />
            )}
          </View>

          <Text style={styles.cardDescription}>
            Receive instant alerts when new trading signals are generated or when your trades hit take profit or stop loss levels.
          </Text>
        </View>

        {/* Notification Types */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>You will receive alerts for:</Text>
          
          <View style={styles.notificationTypeCard}>
            <View style={styles.typeItem}>
              <View style={[styles.typeBadge, { backgroundColor: '#4CAF50' }]}>
                <Ionicons name="trending-up" size={20} color="#FFFFFF" />
              </View>
              <View style={styles.typeContent}>
                <Text style={styles.typeTitle}>New Signals</Text>
                <Text style={styles.typeDescription}>
                  BUY/SELL signals with entry, TP, and SL levels
                </Text>
              </View>
            </View>

            <View style={styles.divider} />

            <View style={styles.typeItem}>
              <View style={[styles.typeBadge, { backgroundColor: '#2196F3' }]}>
                <Ionicons name="checkmark-circle" size={20} color="#FFFFFF" />
              </View>
              <View style={styles.typeContent}>
                <Text style={styles.typeTitle}>Trade Closed</Text>
                <Text style={styles.typeDescription}>
                  When your trades hit TP or SL
                </Text>
              </View>
            </View>

            <View style={styles.divider} />

            <View style={styles.typeItem}>
              <View style={[styles.typeBadge, { backgroundColor: '#FF9800' }]}>
                <Ionicons name="alert-circle" size={20} color="#FFFFFF" />
              </View>
              <View style={styles.typeContent}>
                <Text style={styles.typeTitle}>Market Alerts</Text>
                <Text style={styles.typeDescription}>
                  Important market regime changes
                </Text>
              </View>
            </View>
          </View>
        </View>

        {/* Test Notification Button */}
        {pushEnabled && (
          <TouchableOpacity 
            style={styles.testButton}
            onPress={handleTestNotification}
          >
            <Ionicons name="paper-plane" size={20} color="#0A0E27" />
            <Text style={styles.testButtonText}>Send Test Notification</Text>
          </TouchableOpacity>
        )}

        {/* Status Info */}
        <View style={styles.statusCard}>
          <Ionicons 
            name={pushEnabled ? "checkmark-circle" : "information-circle"} 
            size={24} 
            color={pushEnabled ? "#4CAF50" : "#8B8FA8"} 
          />
          <Text style={styles.statusText}>
            {pushEnabled 
              ? "You're all set! You'll receive notifications for new signals."
              : "Enable notifications to never miss a trading opportunity."
            }
          </Text>
        </View>

        {/* Token Debug (only in dev) */}
        {__DEV__ && expoPushToken && (
          <View style={styles.debugCard}>
            <Text style={styles.debugTitle}>Debug: Push Token</Text>
            <Text style={styles.debugToken} selectable>{expoPushToken}</Text>
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
  },
  card: {
    backgroundColor: '#1A1F3A',
    borderRadius: 16,
    padding: 20,
    marginBottom: 24,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  cardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
  },
  iconContainer: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: 'rgba(255, 215, 0, 0.1)',
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 12,
  },
  cardTitleContainer: {
    flex: 1,
  },
  cardTitle: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  cardSubtitle: {
    fontSize: 12,
    color: '#8B8FA8',
    marginTop: 2,
  },
  cardDescription: {
    fontSize: 14,
    color: '#8B8FA8',
    lineHeight: 20,
  },
  section: {
    marginBottom: 24,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
    marginBottom: 12,
  },
  notificationTypeCard: {
    backgroundColor: '#1A1F3A',
    borderRadius: 16,
    padding: 16,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  typeItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
  },
  typeBadge: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 12,
  },
  typeContent: {
    flex: 1,
  },
  typeTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  typeDescription: {
    fontSize: 13,
    color: '#8B8FA8',
    marginTop: 2,
  },
  divider: {
    height: 1,
    backgroundColor: '#2A2F4A',
  },
  testButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#FFD700',
    borderRadius: 12,
    paddingVertical: 14,
    gap: 8,
    marginBottom: 24,
  },
  testButtonText: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#0A0E27',
  },
  statusCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 16,
    gap: 12,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  statusText: {
    flex: 1,
    fontSize: 14,
    color: '#8B8FA8',
    lineHeight: 20,
  },
  debugCard: {
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 16,
    marginTop: 24,
    borderWidth: 1,
    borderColor: '#FF9800',
  },
  debugTitle: {
    fontSize: 12,
    fontWeight: 'bold',
    color: '#FF9800',
    marginBottom: 8,
  },
  debugToken: {
    fontSize: 10,
    color: '#8B8FA8',
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
  },
});
