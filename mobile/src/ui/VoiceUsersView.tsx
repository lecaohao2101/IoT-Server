import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import {
  deleteSpeakerProfile,
  enrollSpeaker,
  fetchSpeakers,
  updateSpeakerSettings,
  verifySpeakerAudio,
  type SpeakerRole,
  type SpeakerSummary,
  type VerifySpeakerResponse,
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
  onOpenSettings: () => void;
};

export function VoiceUsersView({
  settings,
  capturingClip,
  level,
  startCaptureClip,
  stopCaptureClip,
  cancelCaptureClip,
  onOpenSettings,
}: Props) {
  const [speakers, setSpeakers] = useState<SpeakerSummary[]>([]);
  const [enabled, setEnabled] = useState(true);
  const [threshold, setThreshold] = useState(0.75);
  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Enroll modal state
  const [enrollModalVisible, setEnrollModalVisible] = useState(false);
  const [newName, setNewName] = useState('');
  const [newRole, setNewRole] = useState<SpeakerRole>('member');
  const [enrollSaving, setEnrollSaving] = useState(false);

  // Test modal state
  const [testModalVisible, setTestModalVisible] = useState(false);
  const [testTesting, setTestTesting] = useState(false);
  const [testResult, setTestResult] = useState<VerifySpeakerResponse | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchSpeakers(settings);
      setSpeakers(res.speakers);
      setEnabled(res.enabled);
      setThreshold(res.threshold);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [settings]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const handleToggleEnabled = async () => {
    const next = !enabled;
    setEnabled(next);
    try {
      await updateSpeakerSettings(settings, next);
      setFeedback(next ? 'Đã bật xác thực giọng nói' : 'Đã tắt xác thực giọng nói (cho phép tất cả)');
      setTimeout(() => setFeedback(null), 3000);
    } catch (err) {
      setEnabled(!next);
      setError('Không thể cập nhật cấu hình xác thực');
    }
  };

  const handleDelete = (s: SpeakerSummary) => {
    Alert.alert(
      'Xoá hồ sơ giọng nói',
      `Bạn có chắc chắn muốn xoá giọng nói của "${s.name}"? Người này sẽ không còn quyền điều khiển căn hộ.`,
      [
        { text: 'Huỷ', style: 'cancel' },
        {
          text: 'Xoá',
          style: 'destructive',
          onPress: async () => {
            try {
              await deleteSpeakerProfile(settings, s.id);
              setFeedback(`Đã xoá hồ sơ của "${s.name}"`);
              void loadData();
              setTimeout(() => setFeedback(null), 3000);
            } catch (err) {
              setError('Không thể xoá hồ sơ giọng nói');
            }
          },
        },
      ]
    );
  };

  const handleFinishEnrollRecord = async () => {
    const pcm = await stopCaptureClip();
    if (!pcm || pcm.byteLength < 16000 * 2 * 0.8) {
      setError('Mẫu giọng quá ngắn (cần nói rõ ràng từ 1 đến 3 giây). Vui lòng thử lại.');
      return;
    }
    const name = newName.trim();
    if (!name) {
      setError('Vui lòng nhập tên người dùng');
      return;
    }
    const speakerId = `user_${Date.now()}`;
    setEnrollSaving(true);
    setError(null);
    try {
      await enrollSpeaker(settings, speakerId, name, newRole, pcm);
      setEnrollModalVisible(false);
      setNewName('');
      setFeedback(`Đã đăng ký thành công giọng nói của "${name}"!`);
      void loadData();
      setTimeout(() => setFeedback(null), 3500);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setEnrollSaving(false);
    }
  };

  const handleFinishTestRecord = async () => {
    const pcm = await stopCaptureClip();
    if (!pcm || pcm.byteLength < 16000 * 2 * 0.5) {
      setError('Mẫu giọng kiểm tra quá ngắn. Vui lòng thử lại.');
      return;
    }
    setTestTesting(true);
    setError(null);
    try {
      const res = await verifySpeakerAudio(settings, pcm);
      setTestResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setTestTesting(false);
    }
  };

  const roleLabel = (r: SpeakerRole) => {
    if (r === 'owner') return '👑 Chủ nhà';
    if (r === 'member') return '👤 Thành viên';
    return '👥 Khách';
  };

  return (
    <View style={styles.container}>
      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        {/* Banner thông báo */}
        {feedback && (
          <View style={styles.feedbackBanner}>
            <Text style={styles.feedbackText}>✓ {feedback}</Text>
          </View>
        )}
        {error && (
          <Pressable onPress={() => setError(null)} style={styles.errorBanner}>
            <Text style={styles.errorText}>⚠ {error}</Text>
          </Pressable>
        )}

        {/* Thẻ cài đặt bảo mật sinh trắc học */}
        <View style={styles.card}>
          <View style={styles.cardHeader}>
            <View style={{ flex: 1 }}>
              <Text style={styles.cardTitle}>Bảo mật sinh trắc học giọng nói</Text>
              <Text style={styles.cardSubtitle}>
                Chỉ những người được thêm vào danh sách dưới đây mới có quyền ra lệnh cho thiết bị căn hộ.
              </Text>
            </View>
            <Pressable
              onPress={handleToggleEnabled}
              style={[styles.toggleBtn, enabled ? styles.toggleOn : styles.toggleOff]}
            >
              <Text style={[styles.toggleText, enabled ? styles.toggleTextOn : styles.toggleTextOff]}>
                {enabled ? 'BẬT' : 'TẮT'}
              </Text>
            </Pressable>
          </View>

          <View style={styles.securityMeta}>
            <View style={styles.badge}>
              <Text style={styles.badgeLabel}>Ngưỡng nhận diện: {Math.round(threshold * 100)}%</Text>
            </View>
            <View style={styles.badge}>
              <Text style={styles.badgeLabel}>Phân tích: MFCC + Cao độ Pitch</Text>
            </View>
            <View style={styles.badge}>
              <Text style={styles.badgeLabel}>Chặn người lạ: BẬT</Text>
            </View>
          </View>
        </View>

        {/* Danh sách người dùng được cấp quyền */}
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>
            Người dùng được cấp quyền ({speakers.length})
          </Text>
          <Pressable onPress={() => void loadData()} style={styles.refreshBtn}>
            <Text style={styles.refreshText}>Làm mới</Text>
          </Pressable>
        </View>

        {loading ? (
          <View style={styles.emptyState}>
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.emptyText}>Đang tải danh sách người dùng…</Text>
          </View>
        ) : speakers.length === 0 ? (
          <View style={styles.emptyState}>
            <Text style={styles.emptyIcon}>👥</Text>
            <Text style={styles.emptyTitle}>Chưa có người dùng nào</Text>
            <Text style={styles.emptyText}>
              Khi bật nhận diện giọng nói mà danh sách rỗng, hệ thống sẽ tạm chấp nhận mọi giọng nói hoặc từ chối người lạ tuỳ cấu hình. Hãy thêm giọng nói của bạn ngay!
            </Text>
          </View>
        ) : (
          speakers.map((s) => (
            <View key={s.id} style={styles.speakerItem}>
              <View style={styles.avatar}>
                <Text style={styles.avatarText}>
                  {s.name.slice(0, 1).toUpperCase()}
                </Text>
              </View>
              <View style={styles.speakerInfo}>
                <View style={styles.nameRow}>
                  <Text style={styles.speakerName}>{s.name}</Text>
                  <View style={styles.roleTag}>
                    <Text style={styles.roleTagText}>{roleLabel(s.role)}</Text>
                  </View>
                </View>
                <Text style={styles.speakerSub}>
                  {s.samples_count} mẫu giọng · Đăng ký lúc: {new Date(s.created_at).toLocaleDateString('vi-VN')}
                </Text>
              </View>
              <Pressable
                onPress={() => handleDelete(s)}
                hitSlop={8}
                style={styles.deleteBtn}
              >
                <Text style={styles.deleteText}>🗑️</Text>
              </Pressable>
            </View>
          ))
        )}

        {/* Nút hành động */}
        <View style={styles.actionRow}>
          <Pressable
            onPress={() => {
              setNewName('');
              setNewRole('member');
              setError(null);
              setEnrollModalVisible(true);
            }}
            style={[styles.btn, styles.primaryBtn]}
          >
            <Text style={styles.primaryBtnText}>+ Thêm người dùng (Ghi âm mẫu)</Text>
          </Pressable>

          <Pressable
            onPress={() => {
              setTestResult(null);
              setError(null);
              setTestModalVisible(true);
            }}
            style={[styles.btn, styles.secondaryBtn]}
          >
            <Text style={styles.secondaryBtnText}>🔍 Thử nhận diện giọng nói</Text>
          </Pressable>
        </View>
      </ScrollView>

      {/* Modal đăng ký người dùng mới */}
      <Modal
        visible={enrollModalVisible}
        transparent
        animationType="slide"
        onRequestClose={() => {
          if (!capturingClip) setEnrollModalVisible(false);
        }}
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>Thêm người dùng giọng nói</Text>
            <Text style={styles.modalSubtitle}>
              Ghi âm giọng nói thực tế của người dùng (khoảng 2-3 giây) để hệ thống trích xuất vân giọng đặc trưng.
            </Text>

            <View style={styles.modalField}>
              <Text style={styles.modalLabel}>Tên người dùng (Người A, B, C...)</Text>
              <TextInput
                style={styles.modalInput}
                placeholder="Ví dụ: Anh Nam, Chị Mai, Bố..."
                placeholderTextColor={colors.textMuted}
                value={newName}
                onChangeText={setNewName}
                autoCorrect={false}
              />
            </View>

            <View style={styles.modalField}>
              <Text style={styles.modalLabel}>Vai trò</Text>
              <View style={styles.rolePickerRow}>
                {(['owner', 'member', 'guest'] as SpeakerRole[]).map((r) => {
                  const active = newRole === r;
                  return (
                    <Pressable
                      key={r}
                      onPress={() => setNewRole(r)}
                      style={[styles.roleChip, active && styles.roleChipActive]}
                    >
                      <Text style={[styles.roleChipText, active && styles.roleChipTextActive]}>
                        {roleLabel(r)}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
            </View>

            <View style={styles.recordSection}>
              <Text style={styles.recordHint}>
                {capturingClip
                  ? 'Đang lắng nghe... Hãy nói rõ ràng câu khẩu lệnh bất kỳ'
                  : 'Giữ nút bên dưới và nói một câu ngắn (khoảng 2-3 giây)'}
              </Text>

              {/* Đồng hồ âm lượng */}
              {capturingClip && (
                <View style={styles.meterContainer}>
                  <View style={[styles.meterFill, { width: `${Math.min(100, Math.round(level * 100))}%` }]} />
                </View>
              )}

              <Pressable
                onPressIn={() => void startCaptureClip()}
                onPressOut={() => void handleFinishEnrollRecord()}
                disabled={enrollSaving || !newName.trim()}
                style={[
                  styles.recordHoldBtn,
                  capturingClip && styles.recordHoldBtnActive,
                  (!newName.trim() || enrollSaving) && styles.recordHoldBtnDisabled,
                ]}
              >
                {enrollSaving ? (
                  <ActivityIndicator color="#17130B" />
                ) : (
                  <Text style={styles.recordHoldBtnText}>
                    {capturingClip ? '🔴 Đang thu âm... Thả tay để lưu' : '🎙️ Giữ để thu mẫu giọng'}
                  </Text>
                )}
              </Pressable>
            </View>

            <View style={styles.modalActions}>
              <Pressable
                onPress={() => {
                  cancelCaptureClip();
                  setEnrollModalVisible(false);
                }}
                disabled={enrollSaving}
                style={[styles.btn, styles.cancelBtn]}
              >
                <Text style={styles.cancelBtnText}>Đóng</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>

      {/* Modal thử nghiệm nhận diện giọng nói */}
      <Modal
        visible={testModalVisible}
        transparent
        animationType="slide"
        onRequestClose={() => {
          if (!capturingClip) setTestModalVisible(false);
        }}
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>Kiểm tra nhận diện giọng nói</Text>
            <Text style={styles.modalSubtitle}>
              Giữ nút bên dưới và nói một câu ngắn. Hệ thống sẽ đo độ tương đồng Cosine để xem bạn có được nhận diện hay bị từ chối.
            </Text>

            <View style={styles.recordSection}>
              {capturingClip && (
                <View style={styles.meterContainer}>
                  <View style={[styles.meterFill, { width: `${Math.min(100, Math.round(level * 100))}%` }]} />
                </View>
              )}

              <Pressable
                onPressIn={() => void startCaptureClip()}
                onPressOut={() => void handleFinishTestRecord()}
                disabled={testTesting}
                style={[styles.recordHoldBtn, capturingClip && styles.recordHoldBtnActive]}
              >
                {testTesting ? (
                  <ActivityIndicator color="#17130B" />
                ) : (
                  <Text style={styles.recordHoldBtnText}>
                    {capturingClip ? '🔴 Đang nghe... Thả tay để kiểm tra' : '🎙️ Giữ để nói thử'}
                  </Text>
                )}
              </Pressable>
            </View>

            {testResult && (
              <View
                style={[
                  styles.resultCard,
                  testResult.verified ? styles.resultCardOk : styles.resultCardFail,
                ]}
              >
                <Text style={styles.resultTitle}>
                  {testResult.verified ? '✅ ĐƯỢC CHẤP THUẬN' : '❌ TỪ CHỐI (NGƯỜI LẠ)'}
                </Text>
                <Text style={styles.resultDetails}>
                  {testResult.verified
                    ? `Người dùng: ${testResult.speaker_name} (${roleLabel((testResult.role as SpeakerRole) || 'member')})\nĐộ tương đồng: ${Math.round(testResult.score * 100)}% (Vượt ngưỡng ${Math.round(testResult.threshold * 100)}%)`
                    : `Lý do: ${testResult.reason}\nĐộ tương đồng cao nhất: ${Math.round(testResult.score * 100)}% (Dưới ngưỡng ${Math.round(testResult.threshold * 100)}%)`}
                </Text>
              </View>
            )}

            <View style={styles.modalActions}>
              <Pressable
                onPress={() => {
                  cancelCaptureClip();
                  setTestModalVisible(false);
                }}
                disabled={testTesting}
                style={[styles.btn, styles.cancelBtn]}
              >
                <Text style={styles.cancelBtnText}>Đóng</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  scroll: { padding: spacing.lg, gap: spacing.lg, paddingBottom: 60 },
  feedbackBanner: {
    backgroundColor: '#173627',
    borderWidth: 1,
    borderColor: colors.ok,
    borderRadius: radius.sm,
    padding: spacing.md,
  },
  feedbackText: { color: colors.ok, fontSize: 13, fontWeight: '600' },
  errorBanner: {
    backgroundColor: '#351616',
    borderWidth: 1,
    borderColor: colors.danger,
    borderRadius: radius.sm,
    padding: spacing.md,
  },
  errorText: { color: colors.danger, fontSize: 13 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    padding: spacing.lg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    gap: spacing.md,
  },
  cardHeader: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  cardTitle: { color: colors.text, fontSize: 16, fontWeight: '700' },
  cardSubtitle: { color: colors.textMuted, fontSize: 13, marginTop: spacing.xs, lineHeight: 18 },
  toggleBtn: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    borderRadius: radius.pill,
    minWidth: 64,
    alignItems: 'center',
  },
  toggleOn: { backgroundColor: colors.ok },
  toggleOff: { backgroundColor: colors.surfaceAlt, borderWidth: 1, borderColor: colors.border },
  toggleText: { fontWeight: '700', fontSize: 13 },
  toggleTextOn: { color: '#0F1A15' },
  toggleTextOff: { color: colors.textMuted },
  securityMeta: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginTop: spacing.xs },
  badge: {
    backgroundColor: colors.surfaceAlt,
    paddingHorizontal: spacing.sm + 2,
    paddingVertical: spacing.xs,
    borderRadius: radius.xs,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
  },
  badgeLabel: { color: colors.textMuted, fontSize: 11, fontWeight: '500' },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: spacing.xs,
  },
  sectionTitle: { color: colors.text, fontSize: 15, fontWeight: '700' },
  refreshBtn: { padding: spacing.xs },
  refreshText: { color: colors.accent, fontSize: 12, fontWeight: '600' },
  emptyState: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    padding: spacing.xl,
    alignItems: 'center',
    gap: spacing.sm,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
  },
  emptyIcon: { fontSize: 36 },
  emptyTitle: { color: colors.text, fontSize: 15, fontWeight: '600' },
  emptyText: { color: colors.textMuted, fontSize: 12, textAlign: 'center', lineHeight: 18 },
  speakerItem: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.surface,
    padding: spacing.md,
    borderRadius: radius.sm,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    gap: spacing.md,
  },
  avatar: {
    width: 42,
    height: 42,
    borderRadius: radius.pill,
    backgroundColor: colors.accentSoft,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.accent,
  },
  avatarText: { color: colors.accent, fontSize: 16, fontWeight: '700' },
  speakerInfo: { flex: 1, gap: 2 },
  nameRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  speakerName: { color: colors.text, fontSize: 15, fontWeight: '600' },
  roleTag: {
    backgroundColor: colors.surfaceAlt,
    paddingHorizontal: spacing.xs + 2,
    paddingVertical: 1,
    borderRadius: radius.xs,
  },
  roleTagText: { color: colors.textMuted, fontSize: 11 },
  speakerSub: { color: colors.textMuted, fontSize: 12 },
  deleteBtn: { padding: spacing.sm },
  deleteText: { fontSize: 16 },
  actionRow: { gap: spacing.sm, marginTop: spacing.xs },
  btn: {
    paddingVertical: spacing.md,
    borderRadius: radius.sm,
    alignItems: 'center',
    justifyContent: 'center',
  },
  primaryBtn: { backgroundColor: colors.accent },
  primaryBtnText: { color: '#17130B', fontWeight: '700', fontSize: 14 },
  secondaryBtn: { backgroundColor: colors.surfaceAlt, borderWidth: 1, borderColor: colors.border },
  secondaryBtnText: { color: colors.text, fontWeight: '600', fontSize: 14 },
  modalBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.7)',
    justifyContent: 'flex-end',
  },
  modalCard: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: radius.lg,
    borderTopRightRadius: radius.lg,
    padding: spacing.xl,
    gap: spacing.lg,
    maxHeight: '90%',
  },
  modalTitle: { color: colors.text, fontSize: 18, fontWeight: '700' },
  modalSubtitle: { color: colors.textMuted, fontSize: 13, lineHeight: 18 },
  modalField: { gap: spacing.xs },
  modalLabel: { color: colors.text, fontSize: 13, fontWeight: '600' },
  modalInput: {
    backgroundColor: colors.surfaceAlt,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm + 2,
    color: colors.text,
    fontSize: 14,
  },
  rolePickerRow: { flexDirection: 'row', gap: spacing.sm },
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
  recordSection: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: radius.md,
    padding: spacing.lg,
    alignItems: 'center',
    gap: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
  },
  recordHint: { color: colors.textMuted, fontSize: 12, textAlign: 'center', lineHeight: 17 },
  meterContainer: {
    width: '100%',
    height: 8,
    backgroundColor: colors.border,
    borderRadius: radius.pill,
    overflow: 'hidden',
  },
  meterFill: { height: '100%', backgroundColor: colors.accent },
  recordHoldBtn: {
    backgroundColor: colors.accent,
    width: '100%',
    paddingVertical: spacing.lg,
    borderRadius: radius.md,
    alignItems: 'center',
    justifyContent: 'center',
  },
  recordHoldBtnActive: { backgroundColor: colors.warn },
  recordHoldBtnDisabled: { opacity: 0.5 },
  recordHoldBtnText: { color: '#17130B', fontWeight: '700', fontSize: 15 },
  modalActions: { marginTop: spacing.xs },
  cancelBtn: { backgroundColor: colors.surfaceAlt },
  cancelBtnText: { color: colors.textMuted, fontWeight: '600', fontSize: 14 },
  resultCard: {
    borderRadius: radius.sm,
    padding: spacing.md,
    borderWidth: 1,
    gap: spacing.xs,
  },
  resultCardOk: { backgroundColor: '#132B1D', borderColor: colors.ok },
  resultCardFail: { backgroundColor: '#301616', borderColor: colors.danger },
  resultTitle: { fontWeight: '700', fontSize: 14, color: colors.text },
  resultDetails: { color: colors.textMuted, fontSize: 12, lineHeight: 18 },
});
