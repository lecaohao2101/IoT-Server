/**
 * Device monitoring and direct MQTT control screen.
 *
 * Provides real-time visibility into which devices are ON or OFF, and allows
 * toggling devices directly via MQTT topics with instantaneous feedback.
 */

import { useMemo, useState } from 'react';
import {
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  View,
} from 'react-native';

import { getDeviceIcon, getDeviceStatusLabel, isDeviceOn, type DeviceItem } from '../devices';
import type { MqttConnectionStatus } from '../mqtt';
import { colors, radius, spacing } from './theme';

type Props = {
  devices: DeviceItem[];
  counts: { total: number; onCount: number; offCount: number };
  mqttStatus: MqttConnectionStatus;
  lastMessage: string | null;
  refreshing: boolean;
  onRefresh: () => void;
  onToggleDevice: (device: DeviceItem) => void;
  onSetCapability: (device: DeviceItem, capability: string, value: any) => void;
  onTurnOffAllLights: () => void;
  onDismissMessage: () => void;
  onOpenSettings: () => void;
};

type FilterType = 'all' | 'on' | 'off' | 'living_room' | 'kitchen' | 'bedroom' | 'bathroom' | 'balcony';

export function DeviceControlView({
  devices,
  counts,
  mqttStatus,
  lastMessage,
  refreshing,
  onRefresh,
  onToggleDevice,
  onSetCapability,
  onTurnOffAllLights,
  onDismissMessage,
  onOpenSettings,
}: Props) {
  const [filter, setFilter] = useState<FilterType>('all');

  const filteredDevices = useMemo(() => {
    return devices.filter((d) => {
      const isOn = isDeviceOn(d);
      if (filter === 'on') return isOn;
      if (filter === 'off') return !isOn;
      if (filter === 'all') return true;
      return d.room === filter;
    });
  }, [devices, filter]);

  const mqttBadge = useMemo(() => {
    switch (mqttStatus) {
      case 'connected':
        return { label: 'MQTT: Trực tuyến', color: colors.ok };
      case 'connecting':
        return { label: 'MQTT: Đang nối…', color: colors.warn };
      case 'error':
        return { label: 'MQTT: Lỗi kết nối', color: colors.danger };
      default:
        return { label: 'MQTT: Ngắt kết nối', color: colors.textMuted };
    }
  }, [mqttStatus]);

  const onLightsCount = useMemo(() => {
    return devices.filter((d) => d.type === 'light' && isDeviceOn(d)).length;
  }, [devices]);

  return (
    <View style={styles.container}>
      {/* Thông báo thao tác lệnh MQTT */}
      {lastMessage && (
        <Pressable onPress={onDismissMessage} style={styles.toast}>
          <Text style={styles.toastText} numberOfLines={1}>
            {lastMessage}
          </Text>
          <Text style={styles.toastClose}>✕</Text>
        </Pressable>
      )}

      <ScrollView
        contentContainerStyle={styles.scrollContent}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />}
      >
        {/* Card Thống kê Tổng quan */}
        <View style={styles.summaryCard}>
          <View style={styles.summaryHeader}>
            <Text style={styles.summaryTitle}>Giám sát căn hộ</Text>
            <Pressable onPress={onOpenSettings} style={styles.mqttBadge}>
              <View style={[styles.statusDot, { backgroundColor: mqttBadge.color }]} />
              <Text style={[styles.mqttBadgeText, { color: mqttBadge.color }]}>{mqttBadge.label}</Text>
            </Pressable>
          </View>

          <View style={styles.statsRow}>
            {/* Box Đang Bật */}
            <View style={[styles.statBox, styles.statBoxOn]}>
              <Text style={styles.statNumberOn}>{counts.onCount}</Text>
              <Text style={styles.statLabelOn}>ĐANG BẬT</Text>
            </View>

            {/* Box Đang Tắt */}
            <View style={[styles.statBox, styles.statBoxOff]}>
              <Text style={styles.statNumberOff}>{counts.offCount}</Text>
              <Text style={styles.statLabelOff}>ĐANG TẮT</Text>
            </View>

            {/* Box Tổng thiết bị */}
            <View style={[styles.statBox, styles.statBoxTotal]}>
              <Text style={styles.statNumberTotal}>{counts.total}</Text>
              <Text style={styles.statLabelTotal}>TỔNG CỘNG</Text>
            </View>
          </View>

          {/* Nút hành động nhanh tắt tất cả đèn */}
          {onLightsCount > 0 && (
            <Pressable onPress={onTurnOffAllLights} style={styles.quickActionButton}>
              <Text style={styles.quickActionText}>💡 Tắt nhanh {onLightsCount} đèn đang bật</Text>
            </Pressable>
          )}
        </View>

        {/* Bộ lọc thiết bị */}
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterScroll}>
          <FilterChip label={`Tất cả (${counts.total})`} active={filter === 'all'} onPress={() => setFilter('all')} />
          <FilterChip
            label={`Đang bật (${counts.onCount})`}
            active={filter === 'on'}
            onPress={() => setFilter('on')}
            highlight
          />
          <FilterChip label={`Đang tắt (${counts.offCount})`} active={filter === 'off'} onPress={() => setFilter('off')} />
          <FilterChip label="Phòng khách" active={filter === 'living_room'} onPress={() => setFilter('living_room')} />
          <FilterChip label="Phòng bếp" active={filter === 'kitchen'} onPress={() => setFilter('kitchen')} />
          <FilterChip label="Phòng ngủ" active={filter === 'bedroom'} onPress={() => setFilter('bedroom')} />
          <FilterChip label="Phòng tắm" active={filter === 'bathroom'} onPress={() => setFilter('bathroom')} />
          <FilterChip label="Ban công" active={filter === 'balcony'} onPress={() => setFilter('balcony')} />
        </ScrollView>

        {/* Danh sách các thiết bị */}
        <View style={styles.deviceList}>
          {filteredDevices.length === 0 ? (
            <View style={styles.emptyContainer}>
              <Text style={styles.emptyText}>Không tìm thấy thiết bị nào phù hợp.</Text>
            </View>
          ) : (
            filteredDevices.map((device) => {
              const isOn = isDeviceOn(device);
              const statusLabel = getDeviceStatusLabel(device);

              return (
                <View key={device.id} style={[styles.deviceCard, isOn && styles.deviceCardActive]}>
                  {/* Hàng trên: Icon, Tên, Badge, và Switch MQTT */}
                  <View style={styles.deviceMainRow}>
                    <View style={[styles.iconContainer, isOn && styles.iconContainerActive]}>
                      <Text style={styles.deviceIconText}>{getDeviceIcon(device.type)}</Text>
                    </View>

                    <View style={styles.deviceDetails}>
                      <Text style={styles.deviceName}>{device.name}</Text>
                      <Text style={styles.deviceRoom}>
                        {device.roomName} {device.state.online === false ? '· Offline' : ''}
                      </Text>
                    </View>

                    <View style={styles.deviceRightCol}>
                      <View style={[styles.statusBadge, isOn ? styles.statusBadgeOn : styles.statusBadgeOff]}>
                        <Text style={[styles.statusBadgeText, isOn ? styles.statusBadgeTextOn : styles.statusBadgeTextOff]}>
                          {statusLabel}
                        </Text>
                      </View>

                      <Switch
                        value={isOn}
                        onValueChange={() => onToggleDevice(device)}
                        trackColor={{ false: colors.surfaceAlt, true: colors.ok }}
                        thumbColor={isOn ? '#FFFFFF' : '#8A94A6'}
                      />
                    </View>
                  </View>

                  {/* Hàng dưới: Các điều khiển phụ khi thiết bị đang BẬT */}
                  {isOn && (
                    <View style={styles.deviceSubControls}>
                      {/* Chỉnh nhanh độ sáng đèn */}
                      {device.type === 'light' && (
                        <View style={styles.subRow}>
                          <Text style={styles.subLabel}>Độ sáng:</Text>
                          <View style={styles.presetButtons}>
                            {[20, 50, 80, 100].map((level) => {
                              const active = device.state.brightness === level;
                              return (
                                <Pressable
                                  key={level}
                                  onPress={() => onSetCapability(device, 'brightness', level)}
                                  style={[styles.presetBtn, active && styles.presetBtnActive]}
                                >
                                  <Text style={[styles.presetBtnText, active && styles.presetBtnTextActive]}>
                                    {level}%
                                  </Text>
                                </Pressable>
                              );
                            })}
                          </View>
                        </View>
                      )}

                      {/* Chỉnh điều hòa: Nhiệt độ và Cánh gió Servo */}
                      {device.type === 'air_conditioner' && (
                        <View style={styles.acControlBlock}>
                          <View style={styles.acTempRow}>
                            <Text style={styles.subLabel}>Nhiệt độ:</Text>
                            <View style={styles.tempStepper}>
                              <Pressable
                                style={styles.stepBtn}
                                onPress={() => {
                                  const currentTemp = Number(device.state.temperature) || 26;
                                  if (currentTemp > 18) {
                                    onSetCapability(device, 'temperature', currentTemp - 1);
                                  }
                                }}
                              >
                                <Text style={styles.stepBtnText}>−</Text>
                              </Pressable>
                              <Text style={styles.tempValue}>{device.state.temperature ?? 26}°C</Text>
                              <Pressable
                                style={styles.stepBtn}
                                onPress={() => {
                                  const currentTemp = Number(device.state.temperature) || 26;
                                  if (currentTemp < 30) {
                                    onSetCapability(device, 'temperature', currentTemp + 1);
                                  }
                                }}
                              >
                                <Text style={styles.stepBtnText}>+</Text>
                              </Pressable>
                            </View>
                          </View>

                          <View style={styles.acVaneRow}>
                            <Text style={styles.subLabel}>Cánh gió Servo:</Text>
                            <View style={styles.presetButtons}>
                              <Pressable
                                onPress={() => onSetCapability(device, 'vane_angle', 0)}
                                style={[styles.presetBtn, device.state.vane_angle === 0 && styles.presetBtnActive]}
                              >
                                <Text
                                  style={[
                                    styles.presetBtnText,
                                    device.state.vane_angle === 0 && styles.presetBtnTextActive,
                                  ]}
                                >
                                  Đóng (0°)
                                </Text>
                              </Pressable>
                              <Pressable
                                onPress={() => onSetCapability(device, 'vane_angle', 30)}
                                style={[styles.presetBtn, device.state.vane_angle === 30 && styles.presetBtnActive]}
                              >
                                <Text
                                  style={[
                                    styles.presetBtnText,
                                    device.state.vane_angle === 30 && styles.presetBtnTextActive,
                                  ]}
                                >
                                  Mở (30°)
                                </Text>
                              </Pressable>
                            </View>
                          </View>
                        </View>
                      )}
                    </View>
                  )}
                </View>
              );
            })
          )}
        </View>
      </ScrollView>
    </View>
  );
}

function FilterChip({
  label,
  active,
  onPress,
  highlight,
}: {
  label: string;
  active: boolean;
  onPress: () => void;
  highlight?: boolean;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={[
        styles.chip,
        active && (highlight ? styles.chipActiveHighlight : styles.chipActive),
      ]}
    >
      <Text style={[styles.chipText, active && styles.chipTextActive]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  scrollContent: { paddingBottom: spacing.xxl + 20 },
  toast: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#1C2E24',
    borderColor: colors.ok,
    borderWidth: 1,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    marginHorizontal: spacing.md,
    marginTop: spacing.sm,
    borderRadius: radius.sm,
  },
  toastText: { color: colors.ok, fontSize: 13, flex: 1 },
  toastClose: { color: colors.textMuted, fontSize: 14, marginLeft: spacing.sm },

  // Summary Card
  summaryCard: {
    backgroundColor: colors.surface,
    margin: spacing.md,
    padding: spacing.md,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
  },
  summaryHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing.md,
  },
  summaryTitle: { color: colors.text, fontSize: 16, fontWeight: '700' },
  mqttBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.surfaceAlt,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderRadius: radius.pill,
  },
  statusDot: { width: 8, height: 8, borderRadius: radius.pill, marginRight: 6 },
  mqttBadgeText: { fontSize: 11, fontWeight: '600' },

  statsRow: { flexDirection: 'row', gap: spacing.sm },
  statBox: {
    flex: 1,
    paddingVertical: spacing.md,
    borderRadius: radius.sm,
    alignItems: 'center',
    justifyContent: 'center',
  },
  statBoxOn: { backgroundColor: 'rgba(52, 211, 153, 0.12)', borderColor: colors.ok, borderWidth: 1 },
  statBoxOff: { backgroundColor: colors.surfaceAlt },
  statBoxTotal: { backgroundColor: colors.surfaceAlt },
  statNumberOn: { color: colors.ok, fontSize: 24, fontWeight: '800' },
  statLabelOn: { color: colors.ok, fontSize: 10, fontWeight: '700', marginTop: 2 },
  statNumberOff: { color: colors.textMuted, fontSize: 24, fontWeight: '800' },
  statLabelOff: { color: colors.textMuted, fontSize: 10, fontWeight: '700', marginTop: 2 },
  statNumberTotal: { color: colors.text, fontSize: 24, fontWeight: '800' },
  statLabelTotal: { color: colors.textMuted, fontSize: 10, fontWeight: '700', marginTop: 2 },

  quickActionButton: {
    marginTop: spacing.md,
    backgroundColor: 'rgba(245, 166, 35, 0.15)',
    borderWidth: 1,
    borderColor: colors.accent,
    borderRadius: radius.sm,
    paddingVertical: spacing.sm,
    alignItems: 'center',
  },
  quickActionText: { color: colors.accent, fontSize: 13, fontWeight: '700' },

  // Filters
  filterScroll: { paddingHorizontal: spacing.md, gap: spacing.xs, marginBottom: spacing.sm },
  chip: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs + 2,
    borderRadius: radius.pill,
    backgroundColor: colors.surface,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
  },
  chipActive: { backgroundColor: colors.accentSoft, borderColor: colors.accent },
  chipActiveHighlight: { backgroundColor: 'rgba(52, 211, 153, 0.2)', borderColor: colors.ok },
  chipText: { color: colors.textMuted, fontSize: 12 },
  chipTextActive: { color: colors.text, fontWeight: '700' },

  // Device List
  deviceList: { paddingHorizontal: spacing.md, gap: spacing.sm },
  emptyContainer: { padding: spacing.xl, alignItems: 'center' },
  emptyText: { color: colors.textMuted, fontSize: 14 },

  deviceCard: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    padding: spacing.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
  },
  deviceCardActive: { borderColor: 'rgba(52, 211, 153, 0.4)', backgroundColor: '#182124' },
  deviceMainRow: { flexDirection: 'row', alignItems: 'center' },
  iconContainer: {
    width: 44,
    height: 44,
    borderRadius: radius.md,
    backgroundColor: colors.surfaceAlt,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: spacing.md,
  },
  iconContainerActive: { backgroundColor: 'rgba(52, 211, 153, 0.18)' },
  deviceIconText: { fontSize: 22 },
  deviceDetails: { flex: 1 },
  deviceName: { color: colors.text, fontSize: 15, fontWeight: '600' },
  deviceRoom: { color: colors.textMuted, fontSize: 12, marginTop: 2 },
  deviceRightCol: { alignItems: 'flex-end', gap: spacing.xs },
  statusBadge: { paddingHorizontal: spacing.sm, paddingVertical: 2, borderRadius: radius.pill },
  statusBadgeOn: { backgroundColor: 'rgba(52, 211, 153, 0.2)' },
  statusBadgeOff: { backgroundColor: colors.surfaceAlt },
  statusBadgeText: { fontSize: 10, fontWeight: '700' },
  statusBadgeTextOn: { color: colors.ok },
  statusBadgeTextOff: { color: colors.textMuted },

  // Sub Controls
  deviceSubControls: {
    marginTop: spacing.md,
    paddingTop: spacing.sm,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
  },
  subRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  subLabel: { color: colors.textMuted, fontSize: 12 },
  presetButtons: { flexDirection: 'row', gap: spacing.xs },
  presetBtn: {
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    backgroundColor: colors.surfaceAlt,
    borderRadius: radius.sm,
  },
  presetBtnActive: { backgroundColor: colors.accentSoft, borderColor: colors.accent, borderWidth: 1 },
  presetBtnText: { color: colors.textMuted, fontSize: 11 },
  presetBtnTextActive: { color: colors.accent, fontWeight: '700' },

  acControlBlock: { gap: spacing.sm },
  acTempRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  tempStepper: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  stepBtn: {
    width: 30,
    height: 30,
    borderRadius: radius.sm,
    backgroundColor: colors.surfaceAlt,
    alignItems: 'center',
    justifyContent: 'center',
  },
  stepBtnText: { color: colors.text, fontSize: 18, fontWeight: '700' },
  tempValue: { color: colors.text, fontSize: 15, fontWeight: '700', minWidth: 42, textAlign: 'center' },
  acVaneRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
});
