import { useEffect, useRef } from 'react';
import { FlatList, StyleSheet, Text, View } from 'react-native';

import { describeCommand } from '../protocol';
import type { Message } from '../useVoiceSession';
import { colors, radius, spacing } from './theme';

type Props = {
  messages: Message[];
  partial: string;
};

export function Transcript({ messages, partial }: Props) {
  const listRef = useRef<FlatList<Message>>(null);

  useEffect(() => {
    if (messages.length) {
      // A voice UI should always show the newest turn without a swipe.
      requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: true }));
    }
  }, [messages.length, partial]);

  if (!messages.length && !partial) {
    return (
      <View style={styles.empty}>
        <Text style={styles.emptyTitle}>Giữ nút bên dưới và nói</Text>
        <Text style={styles.emptyHint}>
          Thử: “bật đèn phòng khách lên 70 phần trăm”, “mở rèm phòng ngủ”,
          “nhiệt độ ban công bao nhiêu”
        </Text>
      </View>
    );
  }

  return (
    <FlatList
      ref={listRef}
      data={messages}
      keyExtractor={(item) => item.id}
      contentContainerStyle={styles.list}
      renderItem={({ item }) => <Bubble message={item} />}
      ListFooterComponent={
        partial ? (
          <View style={[styles.bubble, styles.user, styles.partial]}>
            <Text style={styles.partialText}>{partial}…</Text>
          </View>
        ) : null
      }
    />
  );
}

function Bubble({ message }: { message: Message }) {
  const isUser = message.role === 'user';
  const commands = message.commands ?? [];
  const rejections = message.rejections ?? [];

  return (
    <View style={[styles.bubble, isUser ? styles.user : styles.assistant]}>
      <Text style={styles.text}>{message.text}</Text>

      {commands.length > 0 && (
        <View style={styles.meta}>
          {commands.map((command, index) => (
            <Text key={`${command.device_id}-${index}`} style={styles.command}>
              ✓ {describeCommand(command)}
            </Text>
          ))}
        </View>
      )}

      {rejections.length > 0 && (
        <View style={styles.meta}>
          {rejections.map((rejection, index) => (
            <Text key={`${rejection.device_id}-${index}`} style={styles.rejected}>
              ✕ {rejection.message}
            </Text>
          ))}
        </View>
      )}

      {message.latency?.total != null && (
        <Text style={styles.latency}>
          {Math.round(message.latency.total)} ms
          {message.latency.first_audio != null
            ? ` · giọng nói sau ${Math.round(message.latency.first_audio)} ms`
            : ''}
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  list: { padding: spacing.lg, gap: spacing.md },
  empty: { flex: 1, justifyContent: 'center', paddingHorizontal: spacing.xl, gap: spacing.md },
  emptyTitle: { color: colors.text, fontSize: 18, fontWeight: '600', textAlign: 'center' },
  emptyHint: { color: colors.textMuted, fontSize: 14, lineHeight: 21, textAlign: 'center' },

  bubble: {
    maxWidth: '88%',
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
  },
  user: { alignSelf: 'flex-end', backgroundColor: colors.user },
  assistant: {
    alignSelf: 'flex-start',
    backgroundColor: colors.surface,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
  },
  partial: { opacity: 0.55, marginTop: spacing.md },
  partialText: { color: colors.text, fontSize: 15, fontStyle: 'italic' },
  text: { color: colors.text, fontSize: 15, lineHeight: 22 },

  meta: { marginTop: spacing.sm, gap: 2 },
  command: { color: colors.ok, fontSize: 12, fontFamily: 'monospace' },
  rejected: { color: colors.danger, fontSize: 12 },
  latency: { color: colors.textMuted, fontSize: 11, marginTop: spacing.sm },
});
