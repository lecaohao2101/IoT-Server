/**
 * Connection settings, persisted on the device.
 *
 * The server URL is per-installation: a phone on the same Wi-Fi reaches the dev
 * machine by LAN address, which nobody can guess at build time. Being a native
 * app, plain `http://` is fine here -- the secure-origin rule that would block a
 * browser's microphone does not apply.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

const STORAGE_KEY = 'smart-apartment.settings.v1';

export type Settings = {
  /** Base HTTP URL of the server, e.g. http://192.168.1.12:8000 */
  serverUrl: string;
  /** API key or device token. Empty when the server runs without auth. */
  token: string;
  /** Which room the phone is "in"; disambiguates "bật đèn" with no room named. */
  room: string;
  /** Stable id so the conversation survives app restarts. */
  sessionId: string;
};

export const DEFAULT_SETTINGS: Settings = {
  serverUrl: 'http://192.168.1.10:8000',
  token: '',
  room: 'living_room',
  sessionId: `mobile-${Math.random().toString(36).slice(2, 10)}`,
};

export const ROOMS = [
  { id: 'living_room', name: 'Phòng khách' },
  { id: 'kitchen', name: 'Phòng bếp' },
  { id: 'bedroom', name: 'Phòng ngủ' },
  { id: 'bathroom', name: 'Phòng tắm' },
  { id: 'balcony', name: 'Ban công' },
];

export async function loadSettings(): Promise<Settings> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    // Merge over the defaults so a settings file written by an older build,
    // missing a field added since, does not produce `undefined` in a URL.
    return { ...DEFAULT_SETTINGS, ...(JSON.parse(raw) as Partial<Settings>) };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export async function saveSettings(settings: Settings): Promise<void> {
  try {
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // A failed write costs the user a re-entry, not a crash.
  }
}

function normaliseBase(serverUrl: string): string {
  const trimmed = serverUrl.trim().replace(/\/+$/, '');
  if (!trimmed) return '';
  return /^https?:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`;
}

/** `http://host:8000` -> `ws://host:8000/ws/voice?token=...` */
export function voiceSocketUrl(settings: Settings): string {
  const base = normaliseBase(settings.serverUrl).replace(/^http/i, 'ws');
  const query = new URLSearchParams();
  if (settings.token.trim()) query.set('token', settings.token.trim());
  if (settings.room.trim()) query.set('room', settings.room.trim());
  const suffix = query.toString();
  return `${base}/ws/voice${suffix ? `?${suffix}` : ''}`;
}

export function httpUrl(settings: Settings, path: string): string {
  return `${normaliseBase(settings.serverUrl)}${path}`;
}

export function authHeaders(settings: Settings): Record<string, string> {
  const token = settings.token.trim();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
