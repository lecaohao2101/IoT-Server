/**
 * Hook managing real-time smart apartment device states and MQTT interactions.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  DEFAULT_DEVICES,
  isDeviceOn,
  type DeviceItem,
  type DeviceState,
} from './devices';
import { mqttManager, type MqttConnectionStatus } from './mqtt';
import { authHeaders, httpUrl, type Settings } from './settings';

export function useDeviceManager(settings: Settings | null) {
  const [devices, setDevices] = useState<DeviceItem[]>(DEFAULT_DEVICES);
  const [mqttStatus, setMqttStatus] = useState<MqttConnectionStatus>(mqttManager.getStatus());
  const [lastActionMessage, setLastActionMessage] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const settingsRef = useRef<Settings | null>(settings);
  settingsRef.current = settings;

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
      setDevices((prev) =>
        prev.map((dev) => ({
          ...dev,
          state: { ...dev.state, online: isOnline },
        }))
      );
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
    setDevices((prev) =>
      prev.map((d) => (d.id === device.id ? { ...d, state: { ...d.state, ...nextValues } } : d))
    );

    // 1. Direct MQTT publish
    const mqttSent = mqttManager.publishCommand(device.room, device.id, nextValues);

    const actionText = `${nextValues.power === 'on' || nextValues.state === 'open' || nextValues.locked === false ? 'Bật' : 'Tắt'} ${device.name}`;
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
  }, []);

  const setCapability = useCallback((device: DeviceItem, capability: string, value: any) => {
    const nextValues = { [capability]: value };

    // Optimistic update
    setDevices((prev) =>
      prev.map((d) => (d.id === device.id ? { ...d, state: { ...d.state, ...nextValues } } : d))
    );

    // Send direct MQTT command
    mqttManager.publishCommand(device.room, device.id, nextValues);
    setLastActionMessage(`[MQTT] Đã cập nhật ${device.name}: ${capability} = ${value}`);
  }, []);

  const turnOffAllLights = useCallback(() => {
    const onLights = devices.filter((d) => d.type === 'light' && isDeviceOn(d));
    if (onLights.length === 0) return;

    for (const light of onLights) {
      setDevices((prev) =>
        prev.map((d) => (d.id === light.id ? { ...d, state: { ...d.state, power: 'off' } } : d))
      );
      mqttManager.publishCommand(light.room, light.id, { power: 'off' });
    }
    setLastActionMessage(`[MQTT] Đã tắt ${onLights.length} đèn.`);
  }, [devices]);

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
