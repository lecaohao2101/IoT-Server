import { useEffect, useRef } from 'react';
import { Animated, Pressable, StyleSheet, Text, View } from 'react-native';

import { colors, radius, spacing } from './theme';

type Props = {
  recording: boolean;
  speaking: boolean;
  enabled: boolean;
  /** Microphone peak, 0..1 -- drives the ring so the user can see it listening. */
  level: number;
  onPressIn: () => void;
  onPressOut: () => void;
};

export function TalkButton({ recording, speaking, enabled, level, onPressIn, onPressOut }: Props) {
  const ring = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    // Follow the level quickly on the way up and ease down, so the ring reads
    // as speech rather than flickering with every buffer.
    Animated.timing(ring, {
      toValue: recording ? Math.min(1, level * 1.6) : 0,
      duration: 90,
      useNativeDriver: true,
    }).start();
  }, [level, recording, ring]);

  const scale = ring.interpolate({ inputRange: [0, 1], outputRange: [1, 1.45] });
  const opacity = ring.interpolate({ inputRange: [0, 1], outputRange: [0.15, 0.45] });

  const caption = !enabled
    ? 'Chưa kết nối server'
    : recording
      ? 'Đang nghe… thả ra để gửi'
      : speaking
        ? 'Đang trả lời — nhấn để cắt ngang'
        : 'Giữ để nói';

  return (
    <View style={styles.wrap}>
      <Text style={styles.caption}>{caption}</Text>
      <View style={styles.buttonArea}>
        <Animated.View
          pointerEvents="none"
          style={[styles.ring, { transform: [{ scale }], opacity }]}
        />
        <Pressable
          onPressIn={enabled ? onPressIn : undefined}
          onPressOut={enabled ? onPressOut : undefined}
          disabled={!enabled}
          style={({ pressed }) => [
            styles.button,
            recording && styles.buttonRecording,
            !enabled && styles.buttonDisabled,
            pressed && styles.buttonPressed,
          ]}
        >
          <Text style={styles.icon}>{recording ? '■' : '🎤'}</Text>
        </Pressable>
      </View>
    </View>
  );
}

const SIZE = 96;

const styles = StyleSheet.create({
  wrap: {
    alignItems: 'center',
    paddingBottom: spacing.xl,
    paddingTop: spacing.md,
    gap: spacing.md,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
    backgroundColor: colors.surface,
  },
  caption: { color: colors.textMuted, fontSize: 13 },
  buttonArea: { width: SIZE * 1.6, height: SIZE, alignItems: 'center', justifyContent: 'center' },
  ring: {
    position: 'absolute',
    width: SIZE,
    height: SIZE,
    borderRadius: radius.pill,
    backgroundColor: colors.accent,
  },
  button: {
    width: SIZE,
    height: SIZE,
    borderRadius: radius.pill,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.accentSoft,
    borderWidth: 2,
    borderColor: colors.accent,
  },
  buttonRecording: { backgroundColor: colors.accent, borderColor: colors.accent },
  buttonPressed: { opacity: 0.85 },
  buttonDisabled: { opacity: 0.4, borderColor: colors.border },
  icon: { fontSize: 30 },
});
