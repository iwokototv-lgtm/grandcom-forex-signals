import React from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';

export default function TermsScreen() {
  const router = useRouter();

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView contentContainerStyle={styles.scrollContent}>
        <View style={styles.header}>
          <TouchableOpacity onPress={() => router.back()} style={styles.backButton}>
            <Ionicons name="arrow-back" size={24} color="#FFFFFF" />
          </TouchableOpacity>
          <Text style={styles.title}>Terms of Service</Text>
        </View>

        <Text style={styles.lastUpdated}>Last Updated: February 8, 2026</Text>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>1. Acceptance of Terms</Text>
          <Text style={styles.content}>
            By accessing and using Forex Signals Pro, you accept and agree to be bound by
            these Terms of Service. If you do not agree, please discontinue use immediately.
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>2. Service Description</Text>
          <Text style={styles.content}>
            Forex Signals Pro provides AI-powered trading signals for forex and gold markets.
            Signals are provided for informational purposes only and do not constitute financial advice.
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>3. Risk Disclaimer</Text>
          <Text style={styles.warningContent}>
            ⚠️ IMPORTANT: Trading forex and gold involves substantial risk of loss.{"\n\n"}
            • Past performance does not guarantee future results{"\n"}
            • You may lose some or all of your investment{"\n"}
            • Never invest more than you can afford to lose{"\n"}
            • Consult a licensed financial advisor{"\n"}
            • We are not responsible for trading losses{"\n"}
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>4. Subscription Terms</Text>
          <Text style={styles.content}>
            • Free tier: Limited access to basic signals{"\n"}
            • Premium: $49.99/month, billed monthly{"\n"}
            • Auto-renewal unless canceled{"\n"}
            • Cancel anytime with no penalties{"\n"}
            • No refunds for partial months{"\n"}
            • Prices subject to change with 30 days notice{"\n"}
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>5. User Responsibilities</Text>
          <Text style={styles.content}>
            You agree to:{"\n\n"}
            • Provide accurate account information{"\n"}
            • Keep your password secure{"\n"}
            • Not share your account{"\n"}
            • Not resell or redistribute signals{"\n"}
            • Use the service for personal use only{"\n"}
            • Comply with all applicable laws{"\n"}
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>6. Prohibited Activities</Text>
          <Text style={styles.content}>
            You may NOT:{"\n\n"}
            • Use automated systems to access signals{"\n"}
            • Reverse engineer our algorithms{"\n"}
            • Share signals publicly or commercially{"\n"}
            • Violate intellectual property rights{"\n"}
            • Attempt to hack or disrupt the service{"\n"}
            • Create multiple accounts to abuse free trials{"\n"}
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>7. Intellectual Property</Text>
          <Text style={styles.content}>
            All content, including signals, analysis, and algorithms, is proprietary and
            protected by copyright. You receive a limited, non-transferable license to use
            the service.
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>8. Service Availability</Text>
          <Text style={styles.content}>
            We strive for 99.9% uptime but cannot guarantee uninterrupted service. We may:
            {"\n\n"}
            • Perform maintenance with notice{"\n"}
            • Suspend service for violations{"\n"}
            • Discontinue features with notice{"\n"}
            • Modify the service at our discretion{"\n"}
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>9. Limitation of Liability</Text>
          <Text style={styles.content}>
            TO THE MAXIMUM EXTENT PERMITTED BY LAW:{"\n\n"}
            • We are not liable for trading losses{"\n"}
            • No warranty of signal accuracy{"\n"}
            • Liability limited to subscription fees paid{"\n"}
            • No liability for indirect or consequential damages{"\n"}
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>10. Termination</Text>
          <Text style={styles.content}>
            We may terminate your account for:{"\n\n"}
            • Violation of these terms{"\n"}
            • Fraudulent activity{"\n"}
            • Non-payment{"\n"}
            • At our discretion with notice{"\n\n"}
            You may terminate by deleting your account.
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>11. Governing Law</Text>
          <Text style={styles.content}>
            These terms are governed by the laws of [Your Jurisdiction]. Disputes will be
            resolved through binding arbitration.
          </Text>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>12. Contact Information</Text>
          <Text style={styles.content}>
            For terms-related questions:{"\n\n"}
            Email: legal@forexsignals.com{"\n"}
            Telegram: @agbaakin_bot{"\n"}
          </Text>
        </View>

        <View style={styles.warningBox}>
          <Ionicons name="warning" size={48} color="#FF9800" />
          <Text style={styles.warningBoxText}>
            Trading involves significant risk. Only use signals as part of your research.
            Always do your own analysis before trading.
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
  warningContent: {
    fontSize: 14,
    color: '#FF9800',
    lineHeight: 22,
    backgroundColor: 'rgba(255, 152, 0, 0.1)',
    padding: 16,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#FF9800',
  },
  warningBox: {
    backgroundColor: 'rgba(255, 152, 0, 0.1)',
    borderRadius: 12,
    padding: 24,
    alignItems: 'center',
    marginTop: 16,
    marginBottom: 32,
    borderWidth: 1,
    borderColor: '#FF9800',
  },
  warningBoxText: {
    fontSize: 14,
    color: '#FFFFFF',
    textAlign: 'center',
    marginTop: 12,
    lineHeight: 20,
  },
});
