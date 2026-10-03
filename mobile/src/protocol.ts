/**
 * The WebSocket contract, mirrored from the server's `app/api/ws_protocol.py`.
 *
 * Text frames are JSON control messages; binary frames are audio and nothing
 * else. Keep this file in step with the server: it is the only place the app
 * knows what the wire looks like.
 */

export type ReplyEncoding = 'pcm16' | 'mp3' | 'wav' | 'none';

// ----------------------------------------------------------------- outbound

export type ClientFrame =
  | {
      type: 'hello';
      room?: string | null;
      session_id?: string;
      sample_rate: number;
      channels: number;
      codec: 'pcm16';
      reply_encoding: ReplyEncoding;
    }
  | { type: 'audio.start' }
  | { type: 'audio.end' }
  | { type: 'text'; text: string }
  | { type: 'cancel' }
  | { type: 'ping' };

// ------------------------------------------------------------------ inbound

export type PlanCommand = {
  device_id: string;
  capability: string;
  value: unknown;
  notes?: string[];
};

export type PlanRejection = {
  device_id: string;
  capability: string;
  code: string;
  message: string;
};

export type CommandPlan = {
  plan_id: string;
  accepted: PlanCommand[];
  pending_confirmation: PlanCommand[];
  rejected: PlanRejection[];
  notes: string[];
};

export type ServerFrame =
  | { type: 'session.ready'; ts: string; session_id: string; room: string | null; sample_rate: number }
  | { type: 'stt.partial'; ts: string; text: string }
  | { type: 'stt.final'; ts: string; text: string; confidence: number }
  | { type: 'assistant.delta'; ts: string; text: string }
  | {
      type: 'assistant.final';
      ts: string;
      text: string;
      plan: CommandPlan | null;
      needs_clarification: boolean;
      latency_ms: Record<string, number>;
    }
  | { type: 'tts.start'; ts: string; encoding: string; sample_rate: number }
  | { type: 'tts.end'; ts: string }
  | { type: 'state.changed'; ts: string; device_id: string; state: Record<string, unknown>; online: boolean }
  | { type: 'notice'; ts: string; code: string; message: string }
  | { type: 'error'; ts: string; code: string; message: string }
  | { type: 'cancelled'; ts: string }
  | { type: 'pong'; ts: string };

export function parseServerFrame(raw: string): ServerFrame | null {
  try {
    const parsed = JSON.parse(raw);
    return typeof parsed?.type === 'string' ? (parsed as ServerFrame) : null;
  } catch {
    return null;
  }
}

/** Render a plan line the way a person would read it, not the way JSON prints. */
export function describeCommand(command: PlanCommand): string {
  const value =
    typeof command.value === 'boolean' ? (command.value ? 'bật' : 'tắt') : String(command.value);
  return `${command.device_id}.${command.capability} → ${value}`;
}
