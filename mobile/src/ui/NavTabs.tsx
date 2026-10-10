import { Pressable, StyleSheet, Text, View } from 'react-native';
import { colors, radius, spacing } from './theme';

export type ActiveTab = 'voice' | 'devices';

type Props = {
  activeTab: ActiveTab;
  onTabChange: (tab: ActiveTab) => void;
  onDeviceCount: number;
};

export function NavTabs({ activeTab, onTabChange, onDeviceCount }: Props) {
  return (
    <View style={styles.container}>
      <Pressable
        onPress={() => onTabChange('voice')}
        style={[styles.tab, activeTab === 'voice' && styles.tabActive]}
      >
        <Text style={[styles.tabText, activeTab === 'voice' && styles.tabTextActive]}>
          🎙️ Trợ lý giọng nói
        </Text>
      </Pressable>

      <Pressable
        onPress={() => onTabChange('devices')}
        style={[styles.tab, activeTab === 'devices' && styles.tabActive]}
      >
        <Text style={[styles.tabText, activeTab === 'devices' && styles.tabTextActive]}>
          💡 Thiết bị & MQTT
        </Text>
        {onDeviceCount > 0 && (
          <View style={styles.badge}>
            <Text style={styles.badgeText}>{onDeviceCount}</Text>
          </View>
        )}
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    backgroundColor: colors.surfaceAlt,
    marginHorizontal: spacing.lg,
    marginVertical: spacing.sm,
    padding: 3,
    borderRadius: radius.pill,
  },
  tab: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: spacing.sm,
    borderRadius: radius.pill,
    gap: spacing.xs,
  },
  tabActive: {
    backgroundColor: colors.surface,
    shadowColor: '#000',
    shadowOpacity: 0.2,
    shadowRadius: 4,
    elevation: 2,
  },
  tabText: {
    color: colors.textMuted,
    fontSize: 13,
    fontWeight: '600',
  },
  tabTextActive: {
    color: colors.text,
    fontWeight: '700',
  },
  badge: {
    backgroundColor: colors.ok,
    borderRadius: radius.pill,
    paddingHorizontal: 6,
    paddingVertical: 1,
  },
  badgeText: {
    color: '#0F1A15',
    fontSize: 10,
    fontWeight: '800',
  },
});
