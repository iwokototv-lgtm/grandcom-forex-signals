import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useAuth } from '../../contexts/AuthContext';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import api from '../../utils/api';

export default function ProfileScreen() {
  const { user, logout, updateUser } = useAuth();
  const router = useRouter();
  const [upgrading, setUpgrading] = useState(false);

  const handleLogout = () => {
    Alert.alert('Logout', 'Are you sure you want to logout?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Logout',
        style: 'destructive',
        onPress: async () => {
          await logout();
          router.replace('/(auth)/login');
        },
      },
    ]);
  };

  const handleUpgrade = async () => {
    if (user?.subscription_tier === 'PREMIUM') {
      Alert.alert('Already Premium', 'You already have premium access!');
      return;
    }

    Alert.alert(
      'Upgrade to Premium',
      'Get unlimited access to all premium signals with higher confidence and better analysis!\n\nPrice: $49.99/month',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Upgrade',
          onPress: async () => {
            setUpgrading(true);
            try {
              const response = await api.put('/subscription', { tier: 'PREMIUM' });
              updateUser(response.data);
              Alert.alert('Success', 'You are now a premium member!');
            } catch (error) {
              Alert.alert('Error', 'Failed to upgrade subscription');
            } finally {
              setUpgrading(false);
            }
          },
        },
      ]
    );
  };

  const handleDowngrade = async () => {
    Alert.alert(
      'Downgrade to Free',
      'Are you sure you want to downgrade to the free tier? You will lose access to premium signals.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Downgrade',
          style: 'destructive',
          onPress: async () => {
            try {
              const response = await api.put('/subscription', { tier: 'FREE' });
              updateUser(response.data);
              Alert.alert('Downgraded', 'You are now on the free tier');
            } catch (error) {
              Alert.alert('Error', 'Failed to downgrade subscription');
            }
          },
        },
      ]
    );
  };

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView contentContainerStyle={styles.scrollContent}>
        <View style={styles.header}>
          <View style={styles.avatarContainer}>
            <Ionicons name="person" size={48} color="#FFD700" />
          </View>
          <Text style={styles.userName}>{user?.full_name || 'Trader'}</Text>
          <Text style={styles.userEmail}>{user?.email}</Text>
        </View>

        {/* Subscription Card */}
        <View style={styles.subscriptionCard}>
          <View style={styles.subscriptionHeader}>
            <Ionicons
              name={user?.subscription_tier === 'PREMIUM' ? 'star' : 'star-outline'}
              size={32}
              color="#FFD700"
            />
            <Text style={styles.subscriptionTitle}>{user?.subscription_tier} Plan</Text>
          </View>

          {user?.subscription_tier === 'FREE' ? (
            <View style={styles.subscriptionContent}>
              <Text style={styles.subscriptionDescription}>
                You are on the free plan. Upgrade to premium for:
              </Text>
              <View style={styles.featureList}>
                <View style={styles.featureItem}>
                  <Ionicons name="checkmark-circle" size={20} color="#4CAF50" />
                  <Text style={styles.featureText}>Unlimited premium signals</Text>
                </View>
                <View style={styles.featureItem}>
                  <Ionicons name="checkmark-circle" size={20} color="#4CAF50" />
                  <Text style={styles.featureText}>Higher confidence analysis</Text>
                </View>
                <View style={styles.featureItem}>
                  <Ionicons name="checkmark-circle" size={20} color="#4CAF50" />
                  <Text style={styles.featureText}>Advanced technical indicators</Text>
                </View>
                <View style={styles.featureItem}>
                  <Ionicons name="checkmark-circle" size={20} color="#4CAF50" />
                  <Text style={styles.featureText}>Priority support</Text>
                </View>
              </View>
              <TouchableOpacity
                style={styles.upgradeButton}
                onPress={handleUpgrade}
                disabled={upgrading}
              >
                <Text style={styles.upgradeButtonText}>
                  {upgrading ? 'Processing...' : 'Upgrade to Premium'}
                </Text>
              </TouchableOpacity>
            </View>
          ) : (
            <View style={styles.subscriptionContent}>
              <Text style={styles.subscriptionDescription}>
                You have full access to all premium features!
              </Text>
              <TouchableOpacity style={styles.downgradeButton} onPress={handleDowngrade}>
                <Text style={styles.downgradeButtonText}>Downgrade to Free</Text>
              </TouchableOpacity>
            </View>
          )}
        </View>

        {/* Account Options */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Account</Text>

          <TouchableOpacity style={styles.menuItem}>
            <View style={styles.menuItemLeft}>
              <Ionicons name="person-outline" size={24} color="#8B8FA8" />
              <Text style={styles.menuItemText}>Edit Profile</Text>
            </View>
            <Ionicons name="chevron-forward" size={24} color="#8B8FA8" />
          </TouchableOpacity>

          <TouchableOpacity style={styles.menuItem}>
            <View style={styles.menuItemLeft}>
              <Ionicons name="notifications-outline" size={24} color="#8B8FA8" />
              <Text style={styles.menuItemText}>Notifications</Text>
            </View>
            <Ionicons name="chevron-forward" size={24} color="#8B8FA8" />
          </TouchableOpacity>

          <TouchableOpacity style={styles.menuItem}>
            <View style={styles.menuItemLeft}>
              <Ionicons name="logo-telegram" size={24} color="#8B8FA8" />
              <Text style={styles.menuItemText}>Connect Telegram</Text>
            </View>
            <Ionicons name="chevron-forward" size={24} color="#8B8FA8" />
          </TouchableOpacity>
        </View>

        {/* Settings */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Settings</Text>

          <TouchableOpacity style={styles.menuItem} onPress={() => router.push('/(tabs)/help')}>
            <View style={styles.menuItemLeft}>
              <Ionicons name="help-circle-outline" size={24} color="#8B8FA8" />
              <Text style={styles.menuItemText}>Help & Support</Text>
            </View>
            <Ionicons name="chevron-forward" size={24} color="#8B8FA8" />
          </TouchableOpacity>

          <TouchableOpacity style={styles.menuItem} onPress={() => router.push('/(tabs)/privacy')}>
            <View style={styles.menuItemLeft}>
              <Ionicons name="shield-outline" size={24} color="#8B8FA8" />
              <Text style={styles.menuItemText}>Privacy Policy</Text>
            </View>
            <Ionicons name="chevron-forward" size={24} color="#8B8FA8" />
          </TouchableOpacity>

          <TouchableOpacity style={styles.menuItem} onPress={() => router.push('/(tabs)/terms')}>
            <View style={styles.menuItemLeft}>
              <Ionicons name="document-text-outline" size={24} color="#8B8FA8" />
              <Text style={styles.menuItemText}>Terms of Service</Text>
            </View>
            <Ionicons name="chevron-forward" size={24} color="#8B8FA8" />
          </TouchableOpacity>
        </View>

        <TouchableOpacity style={styles.logoutButton} onPress={handleLogout}>
          <Ionicons name="log-out-outline" size={24} color="#F44336" />
          <Text style={styles.logoutButtonText}>Logout</Text>
        </TouchableOpacity>

        <Text style={styles.version}>Version 1.0.0</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0A0E27',
  },
  scrollContent: {
    padding: 16,
  },
  header: {
    alignItems: 'center',
    marginBottom: 32,
  },
  avatarContainer: {
    width: 96,
    height: 96,
    borderRadius: 48,
    backgroundColor: '#1A1F3A',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 16,
    borderWidth: 2,
    borderColor: '#FFD700',
  },
  userName: {
    fontSize: 24,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  userEmail: {
    fontSize: 14,
    color: '#8B8FA8',
    marginTop: 4,
  },
  subscriptionCard: {
    backgroundColor: '#1A1F3A',
    borderRadius: 16,
    padding: 24,
    marginBottom: 24,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  subscriptionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    marginBottom: 16,
  },
  subscriptionTitle: {
    fontSize: 20,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  subscriptionContent: {
    marginTop: 8,
  },
  subscriptionDescription: {
    fontSize: 14,
    color: '#8B8FA8',
    marginBottom: 16,
  },
  featureList: {
    gap: 12,
    marginBottom: 24,
  },
  featureItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  featureText: {
    fontSize: 14,
    color: '#FFFFFF',
  },
  upgradeButton: {
    backgroundColor: '#FFD700',
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
  },
  upgradeButtonText: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#0A0E27',
  },
  downgradeButton: {
    backgroundColor: 'rgba(244, 67, 54, 0.2)',
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#F44336',
  },
  downgradeButtonText: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#F44336',
  },
  section: {
    marginBottom: 24,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#FFFFFF',
    marginBottom: 12,
  },
  menuItem: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 16,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  menuItemLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  menuItemText: {
    fontSize: 16,
    color: '#FFFFFF',
  },
  logoutButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    backgroundColor: 'rgba(244, 67, 54, 0.1)',
    borderRadius: 12,
    paddingVertical: 16,
    marginTop: 16,
    marginBottom: 24,
    borderWidth: 1,
    borderColor: '#F44336',
  },
  logoutButtonText: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#F44336',
  },
  version: {
    textAlign: 'center',
    fontSize: 12,
    color: '#8B8FA8',
    marginBottom: 16,
  },
});
