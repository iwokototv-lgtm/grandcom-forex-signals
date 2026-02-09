import React from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';

export default function PrivacyPolicyScreen() {
  const router = useRouter();

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView contentContainerStyle={styles.scrollContent}>
        <View style={styles.header}>
          <TouchableOpacity onPress={() => router.back()} style={styles.backButton}>
            <Ionicons name="arrow-back" size={24} color="#FFFFFF" />
          </TouchableOpacity>
          <Text style={styles.title}>Privacy Policy</Text>
        </View>

        <Text style={styles.lastUpdated}>Last Updated: February 8, 2026</Text>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>1. Information We Collect</Text>
          <Text style={styles.content}>
            We collect information that you provide directly to us, including:{"\n\n"}
            • Account information (email, name){"\n"}
            • Trading preferences and settings{"\n"}
            • Telegram ID (if you connect){"\n"}
            • Usage data and analytics{"\n"}
            • Device information{"\n"}
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>2. How We Use Your Information</Text>
          <Text style={styles.content}>
            We use the information we collect to:{"\n\n"}
            • Provide and improve our trading signals service{"\n"}
            • Send you signal notifications{"\n"}
            • Process your subscription payments{"\n"}
            • Communicate with you about updates{"\n"}
            • Analyze app performance and user behavior{"\n"}
            • Ensure security and prevent fraud{"\n"}
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>3. Information Sharing</Text>
          <Text style={styles.content}>
            We do NOT sell your personal information. We may share your information with:{"\n\n"}
            • Service providers (payment processors, analytics){"\n"}
            • Legal authorities when required by law{"\n"}
            • With your consent for specific purposes{"\n"}
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>4. Data Security</Text>
          <Text style={styles.content}>
            We implement industry-standard security measures to protect your data:{"\n\n"}
            • Encrypted data transmission (HTTPS/TLS){"\n"}
            • Secure password hashing{"\n"}
            • Regular security audits{"\n"}
            • Limited employee access to personal data{"\n"}
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>5. Your Rights</Text>
          <Text style={styles.content}>
            You have the right to:{"\n\n"}
            • Access your personal data{"\n"}
            • Correct inaccurate data{"\n"}
            • Delete your account and data{"\n"}
            • Export your data{"\n"}
            • Opt-out of marketing communications{"\n"}
            • Withdraw consent at any time{"\n"}
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>6. Cookies and Tracking</Text>
          <Text style={styles.content}>
            We use cookies and similar technologies to:{"\n\n"}
            • Remember your preferences{"\n"}
            • Analyze app usage{"\n"}
            • Improve user experience{"\n\n"}
            You can control cookie settings in your device preferences.
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>7. Children's Privacy</Text>
          <Text style={styles.content}>
            Our service is not intended for users under 18 years of age. We do not
            knowingly collect data from children.
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>8. International Data Transfers</Text>
          <Text style={styles.content}>
            Your data may be transferred and processed in countries outside your residence.
            We ensure appropriate safeguards are in place for such transfers.
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>9. Changes to Privacy Policy</Text>
          <Text style={styles.content}>
            We may update this policy periodically. We will notify you of significant
            changes via email or app notification.
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>10. Contact Us</Text>
          <Text style={styles.content}>
            For privacy-related questions or to exercise your rights:{"\n\n"}
            Email: privacy@forexsignals.com{"\n"}
            Telegram: @agbaakin_bot{"\n\n"}
            Response time: Within 48 hours
          </Text>
        </View>

        <View style={styles.acceptanceBox}>
          <Ionicons name="shield-checkmark" size={48} color="#4CAF50" />
          <Text style={styles.acceptanceText}>
            By using Forex Signals Pro, you agree to this Privacy Policy.
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
  scrollContent: {
    padding: 16,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 16,
  },
  backButton: {
    marginRight: 16,
  },
  title: {
    fontSize: 28,
    fontWeight: 'bold',
    color: '#FFFFFF',
  },
  lastUpdated: {
    fontSize: 14,
    color: '#8B8FA8',
    marginBottom: 24,
  },
  section: {
    marginBottom: 24,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#FFD700',
    marginBottom: 12,
  },
  content: {
    fontSize: 14,
    color: '#FFFFFF',
    lineHeight: 22,
  },
  acceptanceBox: {
    backgroundColor: 'rgba(76, 175, 80, 0.1)',
    borderRadius: 12,
    padding: 24,
    alignItems: 'center',
    marginTop: 16,
    marginBottom: 32,
    borderWidth: 1,
    borderColor: '#4CAF50',
  },
  acceptanceText: {
    fontSize: 14,
    color: '#FFFFFF',
    textAlign: 'center',
    marginTop: 12,
  },
});
