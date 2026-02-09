import React from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  Linking,
  Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';

export default function HelpScreen() {
  const router = useRouter();

  const handleEmail = () => {
    Linking.openURL('mailto:support@forexsignals.com?subject=Support Request');
  };

  const handleTelegram = () => {
    Linking.openURL('https://t.me/agbaakin_bot');
  };

  const handleWhatsApp = () => {
    Alert.alert('WhatsApp Support', 'Contact us at: +1234567890\n\nOpening WhatsApp...', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Open', onPress: () => Linking.openURL('https://wa.me/1234567890') },
    ]);
  };

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView contentContainerStyle={styles.scrollContent}>
        <View style={styles.header}>
          <TouchableOpacity onPress={() => router.back()} style={styles.backButton}>
            <Ionicons name="arrow-back" size={24} color="#FFFFFF" />
          </TouchableOpacity>
          <Text style={styles.title}>Help & Support</Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Contact Us</Text>
          <Text style={styles.sectionDescription}>
            Need help? Our support team is here for you 24/7.
          </Text>

          <TouchableOpacity style={styles.contactCard} onPress={handleEmail}>
            <View style={styles.contactIcon}>
              <Ionicons name="mail" size={24} color="#FFD700" />
            </View>
            <View style={styles.contactInfo}>
              <Text style={styles.contactTitle}>Email Support</Text>
              <Text style={styles.contactDetail}>support@forexsignals.com</Text>
            </View>
            <Ionicons name="chevron-forward" size={24} color="#8B8FA8" />
          </TouchableOpacity>

          <TouchableOpacity style={styles.contactCard} onPress={handleTelegram}>
            <View style={styles.contactIcon}>
              <Ionicons name="logo-telegram" size={24} color="#0088cc" />
            </View>
            <View style={styles.contactInfo}>
              <Text style={styles.contactTitle}>Telegram Support</Text>
              <Text style={styles.contactDetail}>@agbaakin_bot</Text>
            </View>
            <Ionicons name="chevron-forward" size={24} color="#8B8FA8" />
          </TouchableOpacity>

          <TouchableOpacity style={styles.contactCard} onPress={handleWhatsApp}>
            <View style={styles.contactIcon}>
              <Ionicons name="logo-whatsapp" size={24} color="#25D366" />
            </View>
            <View style={styles.contactInfo}>
              <Text style={styles.contactTitle}>WhatsApp</Text>
              <Text style={styles.contactDetail}>Quick response</Text>
            </View>
            <Ionicons name="chevron-forward" size={24} color="#8B8FA8" />
          </TouchableOpacity>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Frequently Asked Questions</Text>

          <View style={styles.faqCard}>
            <Text style={styles.faqQuestion}>How accurate are the signals?</Text>
            <Text style={styles.faqAnswer}>
              Our AI-powered signals have a 98% success rate based on historical performance.
              Premium signals receive deeper analysis and higher confidence scores.
            </Text>
          </View>

          <View style={styles.faqCard}>
            <Text style={styles.faqQuestion}>What's included in Premium?</Text>
            <Text style={styles.faqAnswer}>
              Premium members get unlimited access to all signals, including high-confidence
              premium signals (>75%), advanced technical analysis, priority support, and early
              access to new features.
            </Text>
          </View>

          <View style={styles.faqCard}>
            <Text style={styles.faqQuestion}>How do I connect my Telegram?</Text>
            <Text style={styles.faqAnswer}>
              1. Open Telegram and search for @agbaakin_bot{"\n"}
              2. Send /start command{"\n"}
              3. Bot will send you your Telegram ID{"\n"}
              4. Enter that ID in Profile → Connect Telegram
            </Text>
          </View>

          <View style={styles.faqCard}>
            <Text style={styles.faqQuestion}>How do I cancel my subscription?</Text>
            <Text style={styles.faqAnswer}>
              Go to Profile → Downgrade to Free. You can cancel anytime with no penalties.
              You'll keep premium access until the end of your billing period.
            </Text>
          </View>

          <View style={styles.faqCard}>
            <Text style={styles.faqQuestion}>What pairs do you cover?</Text>
            <Text style={styles.faqAnswer}>
              We provide signals for XAUUSD (Gold), EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD,
              and more. New pairs are added regularly based on market opportunities.
            </Text>
          </View>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>App Information</Text>
          <View style={styles.infoCard}>
            <Text style={styles.infoLabel}>Version</Text>
            <Text style={styles.infoValue}>1.0.0</Text>
          </View>
          <View style={styles.infoCard}>
            <Text style={styles.infoLabel}>Win Rate</Text>
            <Text style={styles.infoValue}>98.0%</Text>
          </View>
          <View style={styles.infoCard}>
            <Text style={styles.infoLabel}>Active Users</Text>
            <Text style={styles.infoValue}>1,000+</Text>
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
  scrollContent: {
    padding: 16,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 24,
  },
  backButton: {
    marginRight: 16,
  },
  title: {
    fontSize: 28,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  section: {
    marginBottom: 32,
  },
  sectionTitle: {
    fontSize: 20,
    fontWeight: 'bold',
    color: '#FFFFFF',
    marginBottom: 8,
  },
  sectionDescription: {
    fontSize: 14,
    color: '#8B8FA8',
    marginBottom: 16,
  },
  contactCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  contactIcon: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: 'rgba(255, 215, 0, 0.1)',
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 16,
  },
  contactInfo: {
    flex: 1,
  },
  contactTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFFFFF',
    marginBottom: 4,
  },
  contactDetail: {
    fontSize: 14,
    color: '#8B8FA8',
  },
  faqCard: {
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  faqQuestion: {
    fontSize: 16,
    fontWeight: '600',
    color: '#FFD700',
    marginBottom: 8,
  },
  faqAnswer: {
    fontSize: 14,
    color: '#FFFFFF',
    lineHeight: 20,
  },
  infoCard: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    backgroundColor: '#1A1F3A',
    borderRadius: 12,
    padding: 16,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: '#2A2F4A',
  },
  infoLabel: {
    fontSize: 14,
    color: '#8B8FA8',
  },
  infoValue: {
    fontSize: 14,
    fontWeight: '600',
    color: '#FFFFFF',
  },
});
