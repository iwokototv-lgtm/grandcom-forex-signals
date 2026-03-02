import { Tabs } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';

export default function TabsLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: {
          backgroundColor: '#1A1F3A',
          borderTopWidth: 1,
          borderTopColor: '#2A2F4A',
          height: 60,
          paddingBottom: 8,
          paddingTop: 8,
        },
        tabBarActiveTintColor: '#FFD700',
        tabBarInactiveTintColor: '#8B8FA8',
      }}
    >
      <Tabs.Screen
        name="home"
        options={{
          title: 'Home',
          tabBarIcon: ({ color, size }) => <Ionicons name="home" size={size} color={color} />,
        }}
      />
      <Tabs.Screen
        name="signals"
        options={{
          title: 'Signals',
          tabBarIcon: ({ color, size }) => <Ionicons name="stats-chart" size={size} color={color} />,
        }}
      />
      <Tabs.Screen
        name="analytics"
        options={{
          title: 'Analytics',
          tabBarIcon: ({ color, size }) => <Ionicons name="trending-up" size={size} color={color} />,
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: 'Profile',
          tabBarIcon: ({ color, size }) => <Ionicons name="person" size={size} color={color} />,
        }}
      />
      {/* Hidden screens - accessible via navigation but not in tab bar */}
      <Tabs.Screen
        name="notifications"
        options={{
          href: null, // Hide from tab bar
          title: 'Notifications',
        }}
      />
      <Tabs.Screen
        name="backtest"
        options={{
          href: null, // Hide from tab bar
          title: 'Backtest',
        }}
      />
      <Tabs.Screen
        name="help"
        options={{
          href: null,
          title: 'Help',
        }}
      />
      <Tabs.Screen
        name="privacy"
        options={{
          href: null,
          title: 'Privacy',
        }}
      />
      <Tabs.Screen
        name="terms"
        options={{
          href: null,
          title: 'Terms',
        }}
      />
    </Tabs>
  );
}
