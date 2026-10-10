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

            <View style={styles.divider} />

            <View style={styles.mqttSectionHeader}>
              <Text style={styles.headingMqtt}>Cấu hình MQTT Broker (Điều khiển trực tiếp)</Text>
              <Pressable
                onPress={() =>
                  setDraft({
                    ...draft,
                    mqtt: {
                      enabled: true,
                      host: 'navyqueen-54cb285b.a01.euc1.aws.hivemq.cloud',
                      port: 8884,
                      path: '/mqtt',
                      ssl: true,
                      user: 'esp32_device',
                      pass: 'esp32_device',
                      baseTopic: 'home',
                    },
                  })
                }
                style={styles.resetMqttBtn}
              >
                <Text style={styles.resetMqttText}>Mặc định HiveMQ</Text>
              </Pressable>
            </View>
            <Text style={styles.hint}>
              Dùng để đồng bộ trạng thái Bật/Tắt và gửi lệnh điều khiển trực tiếp tới thiết bị ESP32 không qua LLM.
            </Text>

            <Field
              label="Host Broker (WebSocket)"
              hint="Ví dụ: navyqueen-54cb285b.a01.euc1.aws.hivemq.cloud hoặc 192.168.1.12"
              value={draft.mqtt?.host ?? ''}
              onChangeText={(host) => setDraft({ ...draft, mqtt: { ...draft.mqtt, host } })}
              placeholder="navyqueen-54cb285b.a01.euc1.aws.hivemq.cloud"
              autoCapitalize="none"
            />

            <View style={styles.inlineRow}>
              <View style={{ flex: 1 }}>
                <Field
                  label="Cổng WebSocket"
                  hint="8884 (TLS) hoặc 9001 (WS)"
                  value={String(draft.mqtt?.port ?? 8884)}
                  onChangeText={(port) =>
                    setDraft({ ...draft, mqtt: { ...draft.mqtt, port: parseInt(port, 10) || 8884 } })
                  }
                  placeholder="8884"
                  keyboardType="number-pad"
                />
              </View>

              <View style={{ flex: 1 }}>
                <Field
                  label="Đường dẫn Path"
                  hint="Mặc định: /mqtt"
                  value={draft.mqtt?.path ?? '/mqtt'}
                  onChangeText={(path) => setDraft({ ...draft, mqtt: { ...draft.mqtt, path } })}
                  placeholder="/mqtt"
                  autoCapitalize="none"
                />
              </View>
            </View>

            <View style={styles.inlineRow}>
              <View style={{ flex: 1 }}>
                <Field
                  label="Tài khoản MQTT"
                  value={draft.mqtt?.user ?? ''}
                  onChangeText={(user) => setDraft({ ...draft, mqtt: { ...draft.mqtt, user } })}
                  placeholder="esp32_device"
                  autoCapitalize="none"
                />
              </View>

              <View style={{ flex: 1 }}>
                <Field
                  label="Mật khẩu MQTT"
                  value={draft.mqtt?.pass ?? ''}
                  onChangeText={(pass) => setDraft({ ...draft, mqtt: { ...draft.mqtt, pass } })}
                  placeholder="••••••••"
                  autoCapitalize="none"
                  secureTextEntry
                />
              </View>
            </View>
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
  divider: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: colors.border,
    marginVertical: spacing.sm,
  },
  mqttSectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: spacing.xs,
  },
  headingMqtt: {
    color: colors.text,
    fontSize: 15,
    fontWeight: '700',
    flex: 1,
  },
  resetMqttBtn: {
    backgroundColor: colors.surfaceAlt,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderRadius: radius.sm,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
  },
  resetMqttText: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: '600',
  },
  inlineRow: {
    flexDirection: 'row',
    gap: spacing.md,
  },
});
