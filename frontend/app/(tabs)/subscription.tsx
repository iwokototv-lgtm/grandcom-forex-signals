import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  Alert,
  ActivityIndicator,
  Linking,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useAuth } from '../../contexts/AuthContext';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import api from '../../utils/api';

interface SubscriptionPackage {
  tier: string;
  name: string;
  price: number;
  currency: string;
  duration_days: number;
  features: string[];
}

interface PackagesResponse {
  packages: Record<string, SubscriptionPackage>;
  tier_features: Record<string, any>;
}

export default function SubscriptionScreen() {
  const { user, updateUser } = useAuth();
  const router = useRouter();
  const [packages, setPackages] = useState<Record<string, SubscriptionPackage>>({});
  const [loading, setLoading] = useState(true);
  const [processing, setProcessing] = useState<string | null>(null);
  const [currentSubscription, setCurrentSubscription] = useState<any>(null);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      const [packagesRes, subscriptionRes] = await Promise.all([
        api.get('/subscriptions/packages'),
        api.get('/subscriptions/current'),
      ]);

      if (packagesRes.data.success) {
        setPackages(packagesRes.data.packages);
      }
      if (subscriptionRes.data.success) {
        setCurrentSubscription(subscriptionRes.data);
      }
    } catch (error) {
      console.error('Error loading subscription data:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSubscribe = async (packageId: string) => {
    try {
      setProcessing(packageId);
      const response = await api.post('/subscriptions/create-checkout-session', {
        package_id: packageId,
      });

      if (response.data.success && response.data.checkout_url) {
        // Open Stripe checkout in browser
        const canOpen = await Linking.canOpenURL(response.data.checkout_url);
        if (canOpen) {
          await Linking.openURL(response.data.checkout_url);
          Alert.alert(
            'Complete Payment',
            'You will be redirected to complete your payment. Once done, return to the app.',
            [{ text: 'OK' }]
          );
        } else {
          Alert.alert('Error', 'Could not open payment page');
        }
      } else {
        Alert.alert('Error', response.data.error || 'Failed to create checkout session');
      }
    } catch (error: any) {
      console.error('Subscription error:', error);
      Alert.alert('Error', error.response?.data?.error || 'Failed to process subscription');
    } finally {
      setProcessing(null);
    }
  };

  const handleCancelSubscription = async () => {
    Alert.alert(
      'Cancel Subscription',
      'Are you sure you want to cancel your subscription? You will lose access to premium features.',
      [
        { text: 'No', style: 'cancel' },
        {
          text: 'Yes, Cancel',
          style: 'destructive',
          onPress: async () => {
            try {
              const response = await api.post('/subscriptions/cancel');
              if (response.data.success) {
                Alert.alert('Cancelled', 'Your subscription has been cancelled.');
                loadData();
              } else {
                Alert.alert('Error', response.data.error || 'Failed to cancel');
              }
            } catch (error: any) {
              Alert.alert('Error', error.response?.data?.error || 'Failed to cancel subscription');
            }
          },
        },
      ]
    );
  };

  const renderPackageCard = (packageId: string, pkg: SubscriptionPackage, isYearly: boolean) => {
    const isCurrentTier = currentSubscription?.tier === pkg.tier;
    const isProcessing = processing === packageId;

    return (
      <View
        key={packageId}
        style={[
          styles.packageCard,
          pkg.tier === 'premium' && styles.premiumCard,
          isCurrentTier && styles.currentCard,
        ]}
      >
        {pkg.tier === 'premium' && (
          <View style={styles.popularBadge}>
            <Text style={styles.popularText}>MOST POPULAR</Text>
          </View>
        )}

        <Text style={styles.packageName}>{pkg.name}</Text>
        <View style={styles.priceContainer}>
          <Text style={styles.price}>${pkg.price}</Text>
          <Text style={styles.period}>/{isYearly ? 'year' : 'month'}</Text>
        </View>

        {isYearly && (
          <View style={styles.savingsBadge}>
            <Text style={styles.savingsText}>Save 2 months!</Text>
          </View>
        )}

        <View style={styles.featuresContainer}>
          {pkg.features.map((feature, idx) => (
            <View key={idx} style={styles.featureItem}>
              <Ionicons name="checkmark-circle" size={18} color="#4CAF50" />
              <Text style={styles.featureText}>{feature}</Text>
            </View>
          ))}
        </View>

        {isCurrentTier ? (
          <View style={styles.currentPlanButton}>
            <Ionicons name="checkmark-circle" size={20} color="#4CAF50" />
            <Text style={styles.currentPlanText}>Current Plan</Text>
          </View>
        ) : (
          <TouchableOpacity
            style={[
              styles.subscribeButton,
              pkg.tier === 'premium' && styles.premiumButton,
              isProcessing && styles.disabledButton,
            ]}
            onPress={() => handleSubscribe(packageId)}
            disabled={isProcessing || processing !== null}
            data-testid={`subscribe-${packageId}-btn`}
          >
            {isProcessing ? (
              <ActivityIndicator color={pkg.tier === 'premium' ? '#0A0E27' : '#FFFFFF'} />
            ) : (
              <Text
                style={[
                  styles.subscribeButtonText,
                  pkg.tier === 'premium' && styles.premiumButtonText,
                ]}
              >
                Subscribe Now
              </Text>
            )}
          </TouchableOpacity>
        )}
      </View>
    );
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.loadingContainer}>
          <ActivityIndicator size="large" color="#FFD700" />
          <Text style={styles.loadingText}>Loading plans...</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container} data-testid="subscription-screen">
      <ScrollView contentContainerStyle={styles.scrollContent}>
        {/* Header */}
        <View style={styles.header}>
          <TouchableOpacity onPress={() => router.back()} style={styles.backButton}>
            <Ionicons name="arrow-back" size={24} color="#FFFFFF" />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>Subscription Plans</Text>
        </View>

        {/* Current Status */}
        <View style={styles.currentStatus}>
          <Ionicons
            name={currentSubscription?.tier === 'free' ? 'star-outline' : 'star'}
            size={32}
            color="#FFD700"
          />
          <View style={styles.currentStatusText}>
            <Text style={styles.currentTierLabel}>Current Plan</Text>
            <Text style={styles.currentTierValue}>
              {currentSubscription?.tier?.toUpperCase() || 'FREE'}
            </Text>
          </View>
        </View>

        {/* Plan Benefits Overview */}
        <View style={styles.benefitsOverview}>
          <Text style={styles.sectionTitle}>Why Upgrade?</Text>
          <View style={styles.benefitsList}>
            <View style={styles.benefitItem}>
              <Ionicons name="analytics" size={24} color="#FFD700" />
              <Text style={styles.benefitText}>Advanced ML Analytics</Text>
            </View>
            <View style={styles.benefitItem}>
              <Ionicons name="notifications" size={24} color="#FFD700" />
              <Text style={styles.benefitText}>Push Notifications</Text>
            </View>
            <View style={styles.benefitItem}>
              <Ionicons name="time" size={24} color="#FFD700" />
              <Text style={styles.benefitText}>Historical Backtesting</Text>
            </View>
            <View style={styles.benefitItem}>
              <Ionicons name="flash" size={24} color="#FFD700" />
              <Text style={styles.benefitText}>Priority Signals</Text>
            </View>
          </View>
        </View>

        {/* Monthly Plans */}
        <Text style={styles.sectionTitle}>Monthly Plans</Text>
        <View style={styles.packagesContainer}>
          {Object.entries(packages)
            .filter(([id]) => id.includes('monthly'))
            .map(([id, pkg]) => renderPackageCard(id, pkg, false))}
        </View>

        {/* Yearly Plans */}
        <Text style={styles.sectionTitle}>Yearly Plans (Save 17%)</Text>
        <View style={styles.packagesContainer}>
          {Object.entries(packages)
            .filter(([id]) => id.includes('yearly'))
            .map(([id, pkg]) => renderPackageCard(id, pkg, true))}
        </View>

        {/* Cancel Subscription */}
        {currentSubscription?.tier !== 'free' && (
          <TouchableOpacity
            style={styles.cancelButton}
            onPress={handleCancelSubscription}
            data-testid="cancel-subscription-btn"
          >
            <Text style={styles.cancelButtonText}>Cancel Subscription</Text>
          </TouchableOpacity>
        )}

        {/* Payment Info */}
        <View style={styles.paymentInfo}>
          <Ionicons name="shield-checkmark" size={20} color="#8B8FA8" />
          <Text style={styles.paymentInfoText}>
            Secure payments powered by Stripe. Cancel anytime.
          </Text>
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
    justifyContent: 'center',
    alignItems: 'center',
  },
  loadingText: {
    color: '#8B8FA8',
    marginTop: 12,
    fontSize: 16,
  },
  scrollContent: {
    padding: 16,
    paddingBottom: 40,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 24,
  },
  backButton: {
    padding: 8,
    marginRight: 12,
  },
  headerTitle: {
    fontSize: 24,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  currentStatus: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 16,
    marginBottom: 24,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  currentStatusText: {
    marginLeft: 16,
  },
  currentTierLabel: {
    color: '#8B8FA8',
    fontSize: 14,
  },
  currentTierValue: {
    color: '#FFD700',
    fontSize: 20,
    fontWeight: 'bold',
  },
  benefitsOverview: {
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 16,
    marginBottom: 24,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  benefitsList: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'space-between',
    marginTop: 12,
  },
  benefitItem: {
    width: '48%',
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 12,
    gap: 8,
  },
  benefitText: {
    color: '#FFFFFF',
    fontSize: 13,
    flex: 1,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#FFFFFF',
    marginBottom: 16,
    marginTop: 8,
  },
  packagesContainer: {
    gap: 16,
    marginBottom: 24,
  },
  packageCard: {
    backgroundColor: '#1A1F3A',
    borderRadius: 16,
    padding: 20,
    borderWidth: 1,
    borderColor: '#2A2F4A',
    position: 'relative',
    overflow: 'hidden',
  },
  premiumCard: {
    borderColor: '#FFD700',
    borderWidth: 2,
  },
  currentCard: {
    borderColor: '#4CAF50',
    borderWidth: 2,
  },
  popularBadge: {
    position: 'absolute',
    top: 0,
    right: 0,
    backgroundColor: '#FFD700',
    paddingHorizontal: 12,
    paddingVertical: 4,
    borderBottomLeftRadius: 8,
  },
  popularText: {
    color: '#0A0E27',
    fontSize: 10,
    fontWeight: 'bold',
  },
  packageName: {
    fontSize: 20,
    fontWeight: 'bold',
    color: '#FFFFFF',
    marginBottom: 8,
  },
  priceContainer: {
    flexDirection: 'row',
    alignItems: 'baseline',
    marginBottom: 8,
  },
  price: {
    fontSize: 36,
    fontWeight: 'bold',
    color: '#FFD700',
  },
  period: {
    fontSize: 16,
    color: '#8B8FA8',
    marginLeft: 4,
  },
  savingsBadge: {
    backgroundColor: 'rgba(76, 175, 80, 0.2)',
    paddingHorizontal: 12,
    paddingVertical: 4,
    borderRadius: 8,
    alignSelf: 'flex-start',
    marginBottom: 16,
  },
  savingsText: {
    color: '#4CAF50',
    fontSize: 12,
    fontWeight: 'bold',
  },
  featuresContainer: {
    marginTop: 16,
    marginBottom: 20,
    gap: 10,
  },
  featureItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  featureText: {
    color: '#FFFFFF',
    fontSize: 14,
  },
  subscribeButton: {
    backgroundColor: '#3A3F5A',
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
    marginTop: 8,
  },
  premiumButton: {
    backgroundColor: '#FFD700',
  },
  disabledButton: {
    opacity: 0.7,
  },
  subscribeButtonText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: 'bold',
  },
  premiumButtonText: {
    color: '#0A0E27',
  },
  currentPlanButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(76, 175, 80, 0.2)',
    borderRadius: 12,
    paddingVertical: 14,
    gap: 8,
    marginTop: 8,
  },
  currentPlanText: {
    color: '#4CAF50',
    fontSize: 16,
    fontWeight: 'bold',
  },
  cancelButton: {
    backgroundColor: 'rgba(244, 67, 54, 0.2)',
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
    marginBottom: 24,
    borderWidth: 1,
    borderColor: '#F44336',
  },
  cancelButtonText: {
    color: '#F44336',
    fontSize: 16,
    fontWeight: '600',
  },
  paymentInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingVertical: 16,
  },
  paymentInfoText: {
    color: '#8B8FA8',
    fontSize: 12,
    textAlign: 'center',
  },
});
