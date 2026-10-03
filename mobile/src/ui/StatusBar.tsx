import { Pressable, StyleSheet, Text, View } from 'react-native';

import { colors, radius, spacing } from './theme';
import type { ConnectionState } from '../useVoiceSession';

const LABELS: Record<ConnectionState, { text: string; colour: string }> = {
  offline: { text: 'Mất kết nối', colour: colors.textMuted },
  connecting: { text: 'Đang kết nối…', colour: colors.warn },
  ready: { text: 'Đã kết nối', colour: colors.ok },
  error: { text: 'Lỗi kết nối', colour: colors.danger },
};

type Props = {
  state: ConnectionState;
  room: string;
  serverUrl: string;
  onOpenSettings: () => void;
  onClear: () => void;
};

export function StatusBar({ state, room, serverUrl, onOpenSettings, onClear }: Props) {
  const label = LABELS[state];
  return (
    <View style={styles.row}>
      <View style={styles.left}>
        <View style={[styles.dot, { backgroundColor: label.colour }]} />
        <View>
          <Text style={styles.title}>Căn hộ thông minh</Text>
          <Text style={styles.subtitle} numberOfLines={1}>
            {label.text} · {room} · {serverUrl.replace(/^https?:\/\//, '')}
          </Text>
        </View>
      </View>

      <View style={styles.actions}>
        <Pressable onPress={onClear} hitSlop={8} style={styles.action}>
          <Text style={styles.actionText}>Xoá</Text>
        </Pressable>
        <Pressable onPress={onOpenSettings} hitSlop={8} style={styles.action}>
          <Text style={styles.actionText}>Cài đặt</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
    backgroundColor: colors.surface,
  },
  left: { flexDirection: 'row', alignItems: 'center', gap: spacing.md, flex: 1 },
  dot: { width: 10, height: 10, borderRadius: radius.pill },
  title: { color: colors.text, fontSize: 16, fontWeight: '600' },
  subtitle: { color: colors.textMuted, fontSize: 12, marginTop: 2 },
  actions: { flexDirection: 'row', gap: spacing.sm },
  action: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs + 2,
    borderRadius: radius.sm,
    backgroundColor: colors.surfaceAlt,
  },
  actionText: { color: colors.textMuted, fontSize: 12, fontWeight: '600' },
});
