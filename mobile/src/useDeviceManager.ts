import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  DEFAULT_DEVICES,
  isDeviceOn,
  type DeviceItem,
  type DeviceState,
} from './devices';
import { mqttManager, type MqttConnectionStatus } from './mqtt';
import { authHeaders, httpUrl, type Settings } from './settings';

const DEVICE_HISTORY_KEY = 'smart_apartment_device_history_v1';

export function useDeviceManager(settings: Settings | null) {
  const [devices, setDevices] = useState<DeviceItem[]>(DEFAULT_DEVICES);
  const [mqttStatus, setMqttStatus] = useState<MqttConnectionStatus>(mqttManager.getStatus());
  const [lastActionMessage, setLastActionMessage] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const settingsRef = useRef<Settings | null>(settings);
  settingsRef.current = settings;

  // Hàng đợi lưu các lệnh được bấm trong lúc ESP32 đang reboot/offline
  const pendingCommandsRef = useRef<Record<string, { room: string; values: Record<string, any> }>>({});

  // Lưu lịch sử trạng thái thiết bị vào AsyncStorage
  const saveHistory = useCallback((updated: DeviceItem[]) => {
    const map: Record<string, DeviceState> = {};
    for (const d of updated) {
      map[d.id] = d.state;
    }
    AsyncStorage.setItem(DEVICE_HISTORY_KEY, JSON.stringify(map)).catch(() => {});
  }, []);

  // 1. Tải trạng thái/lịch sử đã lưu trên App khi khởi động
  useEffect(() => {
    AsyncStorage.getItem(DEVICE_HISTORY_KEY)
      .then((raw) => {
        if (!raw) return;
        try {
          const saved: Record<string, DeviceState> = JSON.parse(raw);
          if (saved && typeof saved === 'object') {
            setDevices((prev) =>
              prev.map((dev) => (saved[dev.id] ? { ...dev, state: { ...dev.state, ...saved[dev.id] } } : dev))
            );
          }
        } catch {}
      })
      .catch(() => {});
  }, []);

  // ------------------------------------------------------------- MQTT Sync
  useEffect(() => {
    if (!settings?.mqtt) return;

    mqttManager.connect(settings.mqtt);

    const unsubscribeStatus = mqttManager.onStatusChange((status) => {
      setMqttStatus(status);
    });

    const unsubscribeMessage = mqttManager.onMessage((topic, payload) => {
      handleIncomingMqtt(topic, payload);
    });

    return () => {
      unsubscribeStatus();
      unsubscribeMessage();
    };
  }, [settings?.mqtt]);

  const handleIncomingMqtt = useCallback((topic: string, rawPayload: string) => {
    const parts = topic.split('/');
    // Example topic: home/living_room/living_room_light/state
    // or: home/living_room/living_room_light/availability
    // or: home/living_room/living_room_light/state/brightness
    // Board-level availability: home/esp32s3/availability or home/esp32/availability
    if (parts.length === 3 && parts[2] === 'availability') {
      const isOnline = rawPayload.toLowerCase() === 'online' || rawPayload === 'true' || rawPayload === '1';
      console.log(`[MQTT] Board availability (${parts[1]}): ${isOnline ? 'online' : 'offline'}`);
      setDevices((prev) => {
        const next = prev.map((dev) => ({
          ...dev,
          state: { ...dev.state, online: isOnline },
        }));

        // ĐỒNG BỘ THEO APP: Khi ESP32 vừa online, khôi phục toàn bộ trạng thái đang mở trên App xuống mạch!
        if (isOnline) {
          console.log('[MQTT-SYNC] ESP32 vừa online! Bắt đầu đồng bộ trạng thái từ App xuống phần cứng...');
          // 1. Gửi lại các lệnh đang xếp hàng (nếu người dùng bấm lúc đang reboot)
          if (Object.keys(pendingCommandsRef.current).length > 0) {
            for (const [id, cmd] of Object.entries(pendingCommandsRef.current)) {
              mqttManager.publishCommand(cmd.room, id, cmd.values);
            }
            pendingCommandsRef.current = {};
          }

          // 2. Khôi phục tất cả thiết bị đang BẬT trên App xuống ESP32
          for (const dev of next) {
            if (isDeviceOn(dev)) {
              console.log(`[MQTT-SYNC] Khôi phục thiết bị ${dev.name} (${dev.id}) sang BẬT trên ESP32`);
              mqttManager.publishCommand(dev.room, dev.id, dev.state);
            }
          }
        }

        saveHistory(next);
        return next;
      });
      return;
    }

    if (parts.length < 4) return;

    const base = parts[0];
    const room = parts[1];
    const deviceId = parts[2];
    const action = parts[3];
    const subCap = parts[4]; // optional: /state/<capability>

    // Check device-level availability topic: home/<room>/<device>/availability
    if (action === 'availability') {
      const isOnline = rawPayload.toLowerCase() === 'online' || rawPayload === 'true' || rawPayload === '1';
      setDevices((prev) =>
        prev.map((dev) => (dev.id === deviceId ? { ...dev, state: { ...dev.state, online: isOnline } } : dev))
      );

      if (isOnline && pendingCommandsRef.current[deviceId]) {
        const cmd = pendingCommandsRef.current[deviceId];
        delete pendingCommandsRef.current[deviceId];
        console.log(`[MQTT] Thiết bị ${deviceId} vừa online! Tự động gửi lại lệnh...`);
        mqttManager.publishCommand(cmd.room, deviceId, cmd.values);
      }
      return;
    }

    if (action !== 'state') return;

    let parsedState: Record<string, any> = {};

    if (subCap) {
      // Sub-capability topic: home/<room>/<device>/state/<capability>
      let val: any = rawPayload;
      if (rawPayload === 'true' || rawPayload === 'on') val = 'on';
      else if (rawPayload === 'false' || rawPayload === 'off') val = 'off';
      else if (!isNaN(Number(rawPayload)) && rawPayload.trim() !== '') val = Number(rawPayload);
      parsedState[subCap] = val;
    } else {
      // JSON or raw state topic: home/<room>/<device>/state
      try {
        const json = JSON.parse(rawPayload);
        if (typeof json === 'object' && json !== null) {
          const unwrapped = json.state || json.reported || json.attributes || json;
          parsedState = { ...unwrapped };
        } else {
          parsedState = { power: json === 'on' || json === true ? 'on' : 'off' };
        }
      } catch {
        const norm = rawPayload.trim().toLowerCase();
        if (norm === 'on' || norm === 'true') parsedState = { power: 'on' };
        else if (norm === 'off' || norm === 'false') parsedState = { power: 'off' };
      }
    }

    if (Object.keys(parsedState).length === 0) return;

    setDevices((prev) =>
      prev.map((dev) => {
        if (dev.id !== deviceId) return dev;
        return {
          ...dev,
          state: {
            ...dev.state,
            ...parsedState,
          },
        };
      })
    );
  }, []);

  // ------------------------------------------------------------- REST Sync (Fallback/Initial)
  const fetchFromApi = useCallback(async () => {
    const current = settingsRef.current;
    if (!current?.serverUrl) return;

    try {
      const res = await fetch(httpUrl(current, '/api/v1/devices'), {
        headers: authHeaders(current),
      });
      if (!res.ok) return;
      const data = await res.json();
      if (!Array.isArray(data?.devices)) return;

      const apiDevices: Array<{ id: string; name?: string; state?: DeviceState; online?: boolean }> =
        data.devices;

      setDevices((prev) =>
        prev.map((dev) => {
          const match = apiDevices.find((d) => d.id === dev.id);
          if (!match) return dev;
          return {
            ...dev,
            name: match.name || dev.name,
            state: {
              ...dev.state,
              ...(match.state || {}),
              online: match.online ?? dev.state.online ?? true,
            },
          };
        })
      );
    } catch {
      // If server is unreachable, in-memory catalogue + MQTT retained state takes over
    }
  }, []);

  useEffect(() => {
    void fetchFromApi();
  }, [fetchFromApi]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    await fetchFromApi();
    if (settingsRef.current?.mqtt) {
      mqttManager.connect(settingsRef.current.mqtt);
    }
    setRefreshing(false);
  }, [fetchFromApi]);

  // ------------------------------------------------------------- Direct Device Controls
  const toggleDevice = useCallback((device: DeviceItem) => {
    const currentlyOn = isDeviceOn(device);
    let nextValues: Record<string, any> = {};

    if (device.type === 'lock') {
      const nextLocked = currentlyOn; // if on (unlocked), lock it
      nextValues = { locked: nextLocked };
    } else if (device.type === 'curtain') {
      nextValues = { state: currentlyOn ? 'closed' : 'open' };
    } else {
      nextValues = { power: currentlyOn ? 'off' : 'on' };
    }

    // Optimistic update
    setDevices((prev) => {
      const next = prev.map((d) => (d.id === device.id ? { ...d, state: { ...d.state, ...nextValues } } : d));
      saveHistory(next);
      return next;
    });

    const actionText = `${nextValues.power === 'on' || nextValues.state === 'open' || nextValues.locked === false ? 'Bật' : 'Tắt'} ${device.name}`;
    const isOnline = device.state?.online !== false;

    if (!isOnline) {
      // Thiết bị hoặc board đang offline (ví dụ đang flash/reboot lại .ino)
      pendingCommandsRef.current[device.id] = { room: device.room, values: nextValues };
      setLastActionMessage(`[ESP32 đang khởi động] Đã nhận lệnh: ${actionText}. Sẽ tự động áp dụng khi thiết bị online!`);
      return;
    }

    delete pendingCommandsRef.current[device.id];

    // 1. Direct MQTT publish
    const mqttSent = mqttManager.publishCommand(device.room, device.id, nextValues);

    if (mqttSent) {
      setLastActionMessage(`[MQTT] Đã gửi lệnh: ${actionText}`);
    } else {
      setLastActionMessage(`[MQTT chưa sẵn sàng] Gửi lệnh dự phòng: ${actionText}`);
      // Fallback via server REST if MQTT not connected
      const current = settingsRef.current;
      if (current?.serverUrl) {
        const [cap, val] = Object.entries(nextValues)[0] || ['power', 'on'];
        fetch(httpUrl(current, `/api/v1/devices/${device.id}/command`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders(current) },
          body: JSON.stringify({ capability: cap, value: val }),
        }).catch(() => {});
      }
    }
  }, [saveHistory]);

  const setCapability = useCallback((device: DeviceItem, capability: string, value: any) => {
    const nextValues = { [capability]: value };

    // Optimistic update
    setDevices((prev) => {
      const next = prev.map((d) => (d.id === device.id ? { ...d, state: { ...d.state, ...nextValues } } : d));
      saveHistory(next);
      return next;
    });

    const isOnline = device.state?.online !== false;
    if (!isOnline) {
      pendingCommandsRef.current[device.id] = { room: device.room, values: nextValues };
      setLastActionMessage(`[ESP32 đang khởi động] Đã xếp hàng: ${device.name} (${capability} = ${value})`);
      return;
    }

    delete pendingCommandsRef.current[device.id];

    // Send direct MQTT command
    mqttManager.publishCommand(device.room, device.id, nextValues);
    setLastActionMessage(`[MQTT] Đã cập nhật ${device.name}: ${capability} = ${value}`);
  }, [saveHistory]);

  const turnOffAllLights = useCallback(() => {
    const onLights = devices.filter((d) => d.type === 'light' && isDeviceOn(d));
    if (onLights.length === 0) return;

    for (const light of onLights) {
      setDevices((prev) => {
        const next: DeviceItem[] = prev.map((d) => (d.id === light.id ? { ...d, state: { ...d.state, power: 'off' as const } } : d));
        saveHistory(next);
        return next;
      });
      mqttManager.publishCommand(light.room, light.id, { power: 'off' });
    }
    setLastActionMessage(`[MQTT] Đã tắt ${onLights.length} đèn.`);
  }, [devices, saveHistory]);

  // ------------------------------------------------------------- Counts & Stats
  const counts = useMemo(() => {
    let onCount = 0;
    let offCount = 0;
    for (const d of devices) {
      if (isDeviceOn(d)) onCount += 1;
      else offCount += 1;
    }
    return {
      total: devices.length,
      onCount,
      offCount,
    };
  }, [devices]);

  return {
    devices,
    counts,
    mqttStatus,
    lastActionMessage,
    refreshing,
    refresh,
    toggleDevice,
    setCapability,
    turnOffAllLights,
    dismissMessage: () => setLastActionMessage(null),
  };
}
