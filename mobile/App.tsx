/**
 * Voice client for the smart apartment server.
 *
 * Scope is deliberately the voice path from the architecture diagram: hold to
 * talk, hear the reply, and see exactly which commands the server accepted or
 * refused. The dashboard lives in the REST API and is not built here.
 */

import { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';

import { DEFAULT_SETTINGS, loadSettings, saveSettings, ROOMS, type Settings } from './src/settings';
import { DeviceControlView } from './src/ui/DeviceControlView';
import { NavTabs, type ActiveTab } from './src/ui/NavTabs';
import { SettingsModal } from './src/ui/SettingsModal';
import { StatusBar as ConnectionBar } from './src/ui/StatusBar';
import { TalkButton } from './src/ui/TalkButton';
import { Transcript } from './src/ui/Transcript';
import { colors, radius, spacing } from './src/ui/theme';
import { useDeviceManager } from './src/useDeviceManager';
import { useVoiceSession } from './src/useVoiceSession';

export default function App() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<ActiveTab>('voice');

  useEffect(() => {
    loadSettings().then(setSettings);
  }, []);

  const session = useVoiceSession(settings);
  const deviceManager = useDeviceManager(settings);

  if (!settings) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  const roomName = ROOMS.find((room) => room.id === settings.room)?.name ?? settings.room;

  const persist = (next: Settings) => {
    setSettings(next);
    void saveSettings(next);
    setSettingsOpen(false);
  };

  return (
    <SafeAreaProvider>
      <SafeAreaView style={styles.screen}>
        <StatusBar style="light" />

        <ConnectionBar
          state={session.state}
          room={roomName}
          serverUrl={settings.serverUrl}
          onOpenSettings={() => setSettingsOpen(true)}
          onClear={session.clear}
        />

        {/* Tab chuyển đổi giữa Giọng nói và Giám sát/Điều khiển MQTT */}
        <NavTabs
          activeTab={activeTab}
          onTabChange={setActiveTab}
          onDeviceCount={deviceManager.counts.onCount}
        />

        {activeTab === 'voice' ? (
          <>
            {session.error && (
              <Pressable onPress={session.dismissError} style={styles.error}>
                <Text style={styles.errorText}>{session.error}</Text>
                <Text style={styles.errorHint}>Chạm để ẩn</Text>
              </Pressable>
            )}

            {session.state === 'offline' && !session.error && (
              <Pressable onPress={session.connect} style={styles.retry}>
                <Text style={styles.retryText}>Chạm để kết nối lại</Text>
              </Pressable>
            )}

            <View style={styles.body}>
              <Transcript messages={session.messages} partial={session.partial} />
            </View>

            <TalkButton
              recording={session.recording}
              speaking={session.speaking}
              enabled={session.state === 'ready'}
              level={session.level}
              onPressIn={() => void session.startTalking()}
              onPressOut={() => void session.stopTalking()}
            />
          </>
        ) : (
          <DeviceControlView
            devices={deviceManager.devices}
            counts={deviceManager.counts}
            mqttStatus={deviceManager.mqttStatus}
            lastMessage={deviceManager.lastActionMessage}
            refreshing={deviceManager.refreshing}
            onRefresh={deviceManager.refresh}
            onToggleDevice={deviceManager.toggleDevice}
            onSetCapability={deviceManager.setCapability}
            onTurnOffAllLights={deviceManager.turnOffAllLights}
            onDismissMessage={deviceManager.dismissMessage}
            onOpenSettings={() => setSettingsOpen(true)}
          />
        )}

        <SettingsModal
          visible={settingsOpen}
          settings={settings ?? DEFAULT_SETTINGS}
          onClose={() => setSettingsOpen(false)}
          onSave={persist}
        />
      </SafeAreaView>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  loading: {
    flex: 1,
    backgroundColor: colors.background,
    alignItems: 'center',
    justifyContent: 'center',
  },
  body: { flex: 1 },
  error: {
    margin: spacing.lg,
    padding: spacing.md,
    borderRadius: radius.sm,
    backgroundColor: '#2A1A1A',
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.danger,
  },
  errorText: { color: colors.danger, fontSize: 13, lineHeight: 19 },
  errorHint: { color: colors.textMuted, fontSize: 11, marginTop: spacing.xs },
  retry: {
    marginHorizontal: spacing.lg,
    marginTop: spacing.lg,
    paddingVertical: spacing.md,
    borderRadius: radius.sm,
    alignItems: 'center',
    backgroundColor: colors.surfaceAlt,
  },
  retryText: { color: colors.textMuted, fontSize: 13, fontWeight: '600' },
});
