/**
 * Device models and default catalogue matching server/config/home.yaml.
 *
 * Provides a self-contained device inventory so the mobile app can monitor
 * and directly control devices over MQTT even when the backend REST server
 * is offline.
 */

export type DeviceType =
  | 'light'
  | 'air_conditioner'
  | 'curtain'
  | 'tv'
  | 'water_heater'
  | 'outlet'
  | 'lock'
  | 'sensor';

export type DeviceState = {
  power?: 'on' | 'off';
  brightness?: number;
  temperature?: number;
  vane_angle?: number;
  state?: 'open' | 'closed' | 'stop';
  position?: number;
  volume?: number;
  locked?: boolean;
  online?: boolean;
  [key: string]: any;
};

export type DeviceItem = {
  id: string;
  name: string;
  type: DeviceType;
  room: string;
  roomName: string;
  capabilities: string[];
  state: DeviceState;
};

export const DEFAULT_DEVICES: DeviceItem[] = [
  // --- Phòng khách (living_room) ---
  {
    id: 'living_room_light',
    name: 'Đèn phòng khách',
    type: 'light',
    room: 'living_room',
    roomName: 'Phòng khách',
    capabilities: ['power', 'brightness', 'color_temp'],
    state: { power: 'off', brightness: 80, online: true },
  },
  {
    id: 'living_room_sofa_light',
    name: 'Đèn sofa phòng khách',
    type: 'light',
    room: 'living_room',
    roomName: 'Phòng khách',
    capabilities: ['power', 'brightness'],
    state: { power: 'off', brightness: 80, online: true },
  },
  {
    id: 'living_room_ac',
    name: 'Điều hòa phòng khách',
    type: 'air_conditioner',
    room: 'living_room',
    roomName: 'Phòng khách',
    capabilities: ['power', 'temperature', 'mode', 'fan_speed', 'vane_angle'],
    state: { power: 'off', temperature: 26, vane_angle: 0, online: true },
  },
  {
    id: 'living_room_curtain',
    name: 'Rèm phòng khách',
    type: 'curtain',
    room: 'living_room',
    roomName: 'Phòng khách',
    capabilities: ['state', 'position'],
    state: { state: 'closed', position: 0, online: true },
  },
  {
    id: 'living_room_tv',
    name: 'Tivi phòng khách',
    type: 'tv',
    room: 'living_room',
    roomName: 'Phòng khách',
    capabilities: ['power', 'volume'],
    state: { power: 'off', volume: 25, online: true },
  },
  {
    id: 'front_door_lock',
    name: 'Khóa cửa chính',
    type: 'lock',
    room: 'living_room',
    roomName: 'Phòng khách',
    capabilities: ['locked'],
    state: { locked: true, online: true },
  },

  // --- Phòng bếp (kitchen) ---
  {
    id: 'kitchen_light',
    name: 'Đèn bếp',
    type: 'light',
    room: 'kitchen',
    roomName: 'Phòng bếp',
    capabilities: ['power', 'brightness'],
    state: { power: 'off', brightness: 90, online: true },
  },
  {
    id: 'kitchen_outlet',
    name: 'Ổ cắm bếp',
    type: 'outlet',
    room: 'kitchen',
    roomName: 'Phòng bếp',
    capabilities: ['power'],
    state: { power: 'off', online: true },
  },

  // --- Phòng ngủ (bedroom) ---
  {
    id: 'bedroom_light',
    name: 'Đèn phòng ngủ',
    type: 'light',
    room: 'bedroom',
    roomName: 'Phòng ngủ',
    capabilities: ['power', 'brightness'],
    state: { power: 'off', brightness: 60, online: true },
  },
  {
    id: 'bedroom_side_light',
    name: 'Đèn đầu giường',
    type: 'light',
    room: 'bedroom',
    roomName: 'Phòng ngủ',
    capabilities: ['power', 'brightness'],
    state: { power: 'off', brightness: 50, online: true },
  },
  {
    id: 'study_light',
    name: 'Đèn bàn học',
    type: 'light',
    room: 'bedroom',
    roomName: 'Phòng ngủ',
    capabilities: ['power', 'brightness'],
    state: { power: 'off', brightness: 80, online: true },
  },
  {
    id: 'bedroom_ac',
    name: 'Điều hòa phòng ngủ',
    type: 'air_conditioner',
    room: 'bedroom',
    roomName: 'Phòng ngủ',
    capabilities: ['power', 'temperature', 'mode', 'fan_speed', 'vane_angle'],
    state: { power: 'off', temperature: 26, vane_angle: 0, online: true },
  },
  {
    id: 'bedroom_curtain',
    name: 'Rèm phòng ngủ',
    type: 'curtain',
    room: 'bedroom',
    roomName: 'Phòng ngủ',
    capabilities: ['state', 'position'],
    state: { state: 'closed', position: 0, online: true },
  },

  // --- Phòng tắm (bathroom) ---
  {
    id: 'bathroom_light',
    name: 'Đèn phòng tắm',
    type: 'light',
    room: 'bathroom',
    roomName: 'Phòng tắm',
    capabilities: ['power', 'brightness'],
    state: { power: 'off', brightness: 100, online: true },
  },
  {
    id: 'bathroom_water_heater',
    name: 'Bình nóng lạnh',
    type: 'water_heater',
    room: 'bathroom',
    roomName: 'Phòng tắm',
    capabilities: ['power', 'temperature'],
    state: { power: 'off', temperature: 45, online: true },
  },

  // --- Ban công (balcony) ---
  {
    id: 'balcony_light',
    name: 'Đèn ban công',
    type: 'light',
    room: 'balcony',
    roomName: 'Ban công',
    capabilities: ['power', 'brightness'],
    state: { power: 'off', brightness: 80, online: true },
  },
];

/** Kiểm tra thiết bị đang BẬT hay TẮT */
export function isDeviceOn(device: DeviceItem): boolean {
  const { state, type } = device;
  if (type === 'lock') {
    // Với khóa cửa: khóa = false nghĩa là cửa đang mở
    return state.locked === false;
  }
  if (type === 'curtain') {
    return state.state === 'open' || (typeof state.position === 'number' && state.position > 0);
  }
  return state.power === 'on' || state.power === (true as any);
}

/** Biểu tượng trực quan cho từng loại thiết bị */
export function getDeviceIcon(type: DeviceType): string {
  switch (type) {
    case 'light':
      return '💡';
    case 'air_conditioner':
      return '❄️';
    case 'curtain':
      return '🪟';
    case 'tv':
      return '📺';
    case 'water_heater':
      return '🚿';
    case 'outlet':
      return '🔌';
    case 'lock':
      return '🔒';
    case 'sensor':
      return '📊';
    default:
      return '⚡';
  }
}

/** Nhãn hiển thị trạng thái bằng tiếng Việt */
export function getDeviceStatusLabel(device: DeviceItem): string {
  const on = isDeviceOn(device);
  if (device.type === 'lock') {
    return on ? 'Đã mở khóa' : 'Đã khóa';
  }
  if (device.type === 'curtain') {
    return on ? 'Đang mở' : 'Đã đóng';
  }
  return on ? 'ĐANG BẬT' : 'ĐANG TẮT';
}
