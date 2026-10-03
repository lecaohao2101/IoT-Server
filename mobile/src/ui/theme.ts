/** One place for colour and spacing, so screens stay consistent. */

export const colors = {
  background: '#0E1116',
  surface: '#171B22',
  surfaceAlt: '#1F242D',
  border: '#2A313C',
  text: '#ECEFF4',
  textMuted: '#96A0B0',
  accent: '#F5A623',
  accentSoft: '#2A2016',
  user: '#2563EB',
  ok: '#34D399',
  warn: '#FBBF24',
  danger: '#F87171',
} as const;

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
} as const;

export const radius = {
  sm: 8,
  md: 14,
  lg: 22,
  pill: 999,
} as const;
