import { useEffect, useState } from 'react';
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { ROOMS, type Settings } from '../settings';
import { colors, radius, spacing } from './theme';

type Props = {
  visible: boolean;
  settings: Settings;
  onClose: () => void;
  onSave: (settings: Settings) => void;
};

export function SettingsModal({ visible, settings, onClose, onSave }: Props) {
  const [draft, setDraft] = useState(settings);

  // Reopening must show what is actually in use, not a stale edit.
  useEffect(() => {
    if (visible) setDraft(settings);
  }, [visible, settings]);

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        style={styles.backdrop}
      >
        <View style={styles.sheet}>
          <Text style={styles.heading}>Cài đặt kết nối</Text>

          <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
            <Field
              label="Địa chỉ server"
              hint="Địa chỉ LAN của máy chạy server, ví dụ http://192.168.1.12:8000"
              value={draft.serverUrl}
              onChangeText={(serverUrl) => setDraft({ ...draft, serverUrl })}
              placeholder="http://192.168.1.12:8000"
              autoCapitalize="none"
              keyboardType="url"
            />

            <Field
              label="Token"
              hint="Để trống nếu server chạy không bật API_KEY"
              value={draft.token}
              onChangeText={(token) => setDraft({ ...draft, token })}
              placeholder="(không bắt buộc)"
              autoCapitalize="none"
              secureTextEntry
            />

            <Text style={styles.label}>Phòng hiện tại</Text>
            <Text style={styles.hint}>
              Dùng khi câu nói không nêu rõ phòng, ví dụ “bật đèn”
            </Text>
            <View style={styles.rooms}>
              {ROOMS.map((room) => {
                const active = draft.room === room.id;
                return (
                  <Pressable
                    key={room.id}
                    onPress={() => setDraft({ ...draft, room: room.id })}
                    style={[styles.chip, active && styles.chipActive]}
                  >
                    <Text style={[styles.chipText, active && styles.chipTextActive]}>
                      {room.name}
                    </Text>
                  </Pressable>
                );
              })}
            </View>

            <Field
              label="Mã phiên"
              hint="Giữ nguyên để trợ lý nhớ ngữ cảnh giữa các lần mở app"
              value={draft.sessionId}
              onChangeText={(sessionId) => setDraft({ ...draft, sessionId })}
              autoCapitalize="none"
            />
          </ScrollView>

          <View style={styles.footer}>
            <Pressable onPress={onClose} style={[styles.button, styles.secondary]}>
              <Text style={styles.secondaryText}>Huỷ</Text>
            </Pressable>
            <Pressable
              onPress={() => onSave({ ...draft, serverUrl: draft.serverUrl.trim() })}
              style={[styles.button, styles.primary]}
            >
              <Text style={styles.primaryText}>Lưu và kết nối lại</Text>
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

type FieldProps = React.ComponentProps<typeof TextInput> & { label: string; hint?: string };

function Field({ label, hint, ...input }: FieldProps) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      {hint ? <Text style={styles.hint}>{hint}</Text> : null}
      <TextInput
        {...input}
        style={styles.input}
        placeholderTextColor={colors.textMuted}
        autoCorrect={false}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.6)' },
  sheet: {
    backgroundColor: colors.background,
    borderTopLeftRadius: radius.lg,
    borderTopRightRadius: radius.lg,
    paddingTop: spacing.xl,
    maxHeight: '88%',
  },
  heading: {
    color: colors.text,
    fontSize: 18,
    fontWeight: '700',
    paddingHorizontal: spacing.xl,
    marginBottom: spacing.lg,
  },
  body: { paddingHorizontal: spacing.xl, paddingBottom: spacing.xl, gap: spacing.lg },
  field: { gap: spacing.xs },
  label: { color: colors.text, fontSize: 14, fontWeight: '600' },
  hint: { color: colors.textMuted, fontSize: 12, lineHeight: 17 },
  input: {
    marginTop: spacing.xs,
    backgroundColor: colors.surface,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    color: colors.text,
    fontSize: 15,
  },
  rooms: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginTop: spacing.xs },
  chip: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    borderRadius: radius.pill,
    backgroundColor: colors.surface,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
  },
  chipActive: { backgroundColor: colors.accentSoft, borderColor: colors.accent },
  chipText: { color: colors.textMuted, fontSize: 13 },
  chipTextActive: { color: colors.accent, fontWeight: '600' },
  footer: {
    flexDirection: 'row',
    gap: spacing.md,
    padding: spacing.xl,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
  },
  button: {
    flex: 1,
    paddingVertical: spacing.md + 2,
    borderRadius: radius.sm,
    alignItems: 'center',
  },
  primary: { backgroundColor: colors.accent },
  primaryText: { color: '#17130B', fontWeight: '700', fontSize: 15 },
  secondary: { backgroundColor: colors.surfaceAlt },
  secondaryText: { color: colors.textMuted, fontWeight: '600', fontSize: 15 },
});
