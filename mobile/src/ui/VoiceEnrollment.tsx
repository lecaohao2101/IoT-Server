/**
 * Màn hình đăng ký giọng nói — thứ đầu tiên người dùng mới nhìn thấy.
 *
 * Căn hộ chỉ nghe lời người đã đăng ký giọng, nên không có đường vòng: app bị
 * chặn ở đây cho tới khi hồ sơ giọng của máy này đọc đủ số câu server yêu cầu.
 * Đường thoát duy nhất là nút Cài đặt — vì khi server sai địa chỉ thì đăng ký
 * không thể thành công, và khoá chết người dùng trong màn hình này mới là lỗi.
 *
 * Nội dung các câu mẫu do server cấp, không viết cứng trong app: chúng mang tên
 * trợ lý, và đổi tên trợ lý thì câu phải đọc cũng đổi theo.
 */

import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import {
  enrollSpeaker,
  fetchEnrollmentPlan,
  type EnrollmentPlan,
  type SpeakerRole,
} from '../api/speakers';
import type { Settings } from '../settings';
import { colors, radius, spacing } from './theme';

type Props = {
  settings: Settings;
  capturingClip: boolean;
  level: number;
  startCaptureClip: () => Promise<void>;
  stopCaptureClip: () => Promise<Uint8Array | null>;
  cancelCaptureClip: () => void;
  onDone: (speakerId: string, speakerName: string) => void;
  onOpenSettings: () => void;
};

const ROLE_LABELS: Record<SpeakerRole, string> = {
  owner: '👑 Chủ nhà',
  member: '👤 Thành viên',
  guest: '👥 Khách',
};

/** 16 kHz, PCM16 mono: 2 byte cho mỗi mẫu. */
function bytesToMs(byteLength: number, sampleRate: number): number {
  return (byteLength / 2 / sampleRate) * 1000;
}

export function VoiceEnrollment({
  settings,
  capturingClip,
  level,
  startCaptureClip,
  stopCaptureClip,
  cancelCaptureClip,
  onDone,
  onOpenSettings,
}: Props) {
  const [plan, setPlan] = useState<EnrollmentPlan | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [name, setName] = useState(settings.speakerName);
  const [role, setRole] = useState<SpeakerRole>('owner');
  const [started, setStarted] = useState(false);
  const [step, setStep] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [speakerId, setSpeakerId] = useState(settings.speakerId);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const fetched = await fetchEnrollmentPlan(settings, settings.speakerId || undefined);
      setPlan(fetched);
      // Lần đăng ký trước bị gián đoạn thì đọc tiếp câu còn dở, không bắt đọc lại từ đầu.
      setStep(Math.min(fetched.completed_samples, fetched.phrases.length - 1));
      if (fetched.completed_samples > 0) setStarted(true);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  }, [settings]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleRelease = async () => {
    if (!plan) return;
    const pcm = await stopCaptureClip();
    const minBytes = (plan.min_sample_ms / 1000) * plan.sample_rate * 2;
    if (!pcm || pcm.byteLength < minBytes) {
      const got = pcm ? Math.round(bytesToMs(pcm.byteLength, plan.sample_rate)) : 0;
      setError(
        `Đoạn thu quá ngắn (${got} ms). Hãy giữ nút và đọc trọn câu, khoảng 2–3 giây.`
      );
      return;
    }

    const id = speakerId || `user_${Date.now()}`;
    setSaving(true);
    setError(null);
    try {
      const summary = await enrollSpeaker(settings, id, name.trim(), role, pcm, step);
      setSpeakerId(id);
      if (summary.complete || summary.samples_count >= plan.required_samples) {
        onDone(id, name.trim());
        return;
      }
      setStep(Math.min(summary.samples_count, plan.phrases.length - 1));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  // ------------------------------------------------------------- màn hình lỗi
  if (loadError) {
    return (
      <View style={styles.centered}>
        <Text style={styles.emoji}>📡</Text>
        <Text style={styles.title}>Chưa kết nối được server</Text>
        <Text style={styles.body}>{loadError}</Text>
        <Text style={styles.hint}>
          Không đăng ký được giọng nói khi chưa gọi tới server. Kiểm tra lại địa chỉ server và
          token trong phần Cài đặt.
        </Text>
        <Pressable onPress={() => void load()} style={[styles.btn, styles.primaryBtn]}>
          <Text style={styles.primaryBtnText}>Thử lại</Text>
        </Pressable>
        <Pressable onPress={onOpenSettings} style={[styles.btn, styles.ghostBtn]}>
          <Text style={styles.ghostBtnText}>Mở cài đặt</Text>
        </Pressable>
      </View>
    );
  }

  if (!plan) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.body}>Đang tải hướng dẫn đăng ký…</Text>
      </View>
    );
  }

  // --------------------------------------------------------- bước nhập tên
  if (!started) {
    return (
      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        <Text style={styles.emoji}>🎙️</Text>
        <Text style={styles.title}>Đăng ký giọng nói của bạn</Text>
        <Text style={styles.body}>
          Căn hộ chỉ nhận lệnh từ giọng nói đã đăng ký. Bạn sẽ đọc {plan.required_samples} câu
          ngắn để hệ thống ghi nhớ đặc trưng giọng của mình.
        </Text>

        <View style={styles.infoCard}>
          <Text style={styles.infoTitle}>Trợ lý của bạn tên là “{plan.assistant_name}”</Text>
          <Text style={styles.infoBody}>
            {plan.wake_word_enabled
              ? `Mỗi khi muốn ra lệnh, hãy gọi “${plan.assistant_name}” trước. Các câu dưới đây đều bắt đầu bằng tên đó, đọc xong là bạn quen luôn.`
              : 'Các câu dưới đây giúp hệ thống nghe đủ dải giọng của bạn.'}
          </Text>
        </View>

        <View style={styles.field}>
          <Text style={styles.label}>Bạn tên là gì?</Text>
          <TextInput
            style={styles.input}
            placeholder="Ví dụ: Anh Nam, Chị Mai…"
            placeholderTextColor={colors.textMuted}
            value={name}
            onChangeText={setName}
            autoCorrect={false}
          />
        </View>

        <View style={styles.field}>
          <Text style={styles.label}>Vai trò trong nhà</Text>
          <View style={styles.roleRow}>
            {(Object.keys(ROLE_LABELS) as SpeakerRole[]).map((item) => {
              const active = role === item;
              return (
                <Pressable
                  key={item}
                  onPress={() => setRole(item)}
                  style={[styles.roleChip, active && styles.roleChipActive]}
                >
                  <Text style={[styles.roleChipText, active && styles.roleChipTextActive]}>
                    {ROLE_LABELS[item]}
                  </Text>
                </Pressable>
              );
            })}
          </View>
        </View>

        <Pressable
          onPress={() => setStarted(true)}
          disabled={!name.trim()}
          style={[styles.btn, styles.primaryBtn, !name.trim() && styles.btnDisabled]}
        >
          <Text style={styles.primaryBtnText}>Bắt đầu đọc câu mẫu</Text>
        </Pressable>
        <Pressable onPress={onOpenSettings} style={[styles.btn, styles.ghostBtn]}>
          <Text style={styles.ghostBtnText}>Cài đặt server</Text>
        </Pressable>
      </ScrollView>
    );
  }

  // ------------------------------------------------------- bước đọc câu mẫu
  const phrase = plan.phrases[Math.min(step, plan.phrases.length - 1)];
  const done = Math.min(step, plan.required_samples);

  return (
    <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
      <Text style={styles.stepCount}>
        Câu {done + 1} / {plan.required_samples}
      </Text>
      <View style={styles.dots}>
        {Array.from({ length: plan.required_samples }).map((_, index) => (
          <View key={index} style={[styles.dot, index <= done && styles.dotActive]} />
        ))}
      </View>

      <Text style={styles.title}>Đọc to câu sau</Text>
      <View style={styles.phraseCard}>
        <Text style={styles.phraseText}>“{phrase.text}”</Text>
      </View>
      <Text style={styles.hint}>
        Giữ nút bên dưới, đọc hết câu với giọng bình thường rồi thả tay. Nói trong phòng yên
        tĩnh sẽ cho mẫu giọng chính xác hơn.
      </Text>

      {error && (
        <Pressable onPress={() => setError(null)} style={styles.errorBanner}>
          <Text style={styles.errorText}>⚠ {error}</Text>
        </Pressable>
      )}

      {capturingClip && (
        <View style={styles.meter}>
          <View style={[styles.meterFill, { width: `${Math.min(100, Math.round(level * 100))}%` }]} />
        </View>
      )}

      <Pressable
        onPressIn={() => void startCaptureClip()}
        onPressOut={() => void handleRelease()}
        disabled={saving}
        style={[
          styles.recordBtn,
          capturingClip && styles.recordBtnActive,
          saving && styles.btnDisabled,
        ]}
      >
        {saving ? (
          <ActivityIndicator color="#17130B" />
        ) : (
          <Text style={styles.recordBtnText}>
            {capturingClip ? '🔴 Đang thu… thả tay để lưu' : '🎙️ Giữ để đọc câu này'}
          </Text>
        )}
      </Pressable>

      <Pressable
        onPress={() => {
          cancelCaptureClip();
          setStarted(false);
        }}
        disabled={saving}
        style={[styles.btn, styles.ghostBtn]}
      >
        <Text style={styles.ghostBtnText}>Quay lại</Text>
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scroll: {
    padding: spacing.xl,
    gap: spacing.lg,
    backgroundColor: colors.background,
    flexGrow: 1,
    justifyContent: 'center',
  },
  centered: {
    flex: 1,
    padding: spacing.xl,
    gap: spacing.md,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.background,
  },
  emoji: { fontSize: 44, textAlign: 'center' },
  title: { color: colors.text, fontSize: 21, fontWeight: '700', textAlign: 'center' },
  body: { color: colors.textMuted, fontSize: 14, lineHeight: 21, textAlign: 'center' },
  hint: { color: colors.textMuted, fontSize: 12, lineHeight: 18, textAlign: 'center' },
  infoCard: {
    backgroundColor: colors.accentSoft,
    borderWidth: 1,
    borderColor: colors.accent,
    borderRadius: radius.md,
    padding: spacing.lg,
    gap: spacing.xs,
  },
  infoTitle: { color: colors.accent, fontSize: 15, fontWeight: '700' },
  infoBody: { color: colors.text, fontSize: 13, lineHeight: 19 },
  field: { gap: spacing.xs },
  label: { color: colors.text, fontSize: 13, fontWeight: '600' },
  input: {
    backgroundColor: colors.surfaceAlt,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm + 2,
    color: colors.text,
    fontSize: 15,
  },
  roleRow: { flexDirection: 'row', gap: spacing.sm },
  roleChip: {
    flex: 1,
    paddingVertical: spacing.sm,
    alignItems: 'center',
    backgroundColor: colors.surfaceAlt,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  roleChipActive: { backgroundColor: colors.accentSoft, borderColor: colors.accent },
  roleChipText: { color: colors.textMuted, fontSize: 12 },
  roleChipTextActive: { color: colors.accent, fontWeight: '700' },
  stepCount: { color: colors.textMuted, fontSize: 13, textAlign: 'center' },
  dots: { flexDirection: 'row', gap: spacing.sm, justifyContent: 'center' },
  dot: {
    width: 28,
    height: 4,
    borderRadius: radius.pill,
    backgroundColor: colors.border,
  },
  dotActive: { backgroundColor: colors.accent },
  phraseCard: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.xl,
  },
  phraseText: {
    color: colors.text,
    fontSize: 19,
    lineHeight: 29,
    textAlign: 'center',
    fontWeight: '600',
  },
  errorBanner: {
    backgroundColor: '#351616',
    borderWidth: 1,
    borderColor: colors.danger,
    borderRadius: radius.sm,
    padding: spacing.md,
  },
  errorText: { color: colors.danger, fontSize: 13, lineHeight: 19 },
  meter: {
    height: 8,
    backgroundColor: colors.border,
    borderRadius: radius.pill,
    overflow: 'hidden',
  },
  meterFill: { height: '100%', backgroundColor: colors.accent },
  recordBtn: {
    backgroundColor: colors.accent,
    paddingVertical: spacing.lg,
    borderRadius: radius.md,
    alignItems: 'center',
    justifyContent: 'center',
  },
  recordBtnActive: { backgroundColor: colors.warn },
  recordBtnText: { color: '#17130B', fontWeight: '700', fontSize: 15 },
  btn: {
    paddingVertical: spacing.md,
    borderRadius: radius.sm,
    alignItems: 'center',
    justifyContent: 'center',
  },
  btnDisabled: { opacity: 0.5 },
  primaryBtn: { backgroundColor: colors.accent },
  primaryBtnText: { color: '#17130B', fontWeight: '700', fontSize: 15 },
  ghostBtn: { backgroundColor: colors.surfaceAlt, borderWidth: 1, borderColor: colors.border },
  ghostBtnText: { color: colors.textMuted, fontWeight: '600', fontSize: 14 },
});
