/**
 * One live conversation with the apartment.
 *
 * Owns the WebSocket, the microphone stream and the playback queue, and exposes
 * them as plain React state. The ordering that matters:
 *
 *   press  -> stop any playback (barge-in), open mic, stream PCM frames
 *   release-> send `audio.end`; the server endpoints the utterance
 *   reply  -> `assistant.delta` text arrives first, audio follows clause by clause
 *
 * The socket is kept open between turns: reconnecting per utterance would add a
 * handshake to every sentence, and the server keeps conversation context keyed
 * by `session_id` anyway.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  requestRecordingPermissionsAsync,
  setAudioModeAsync,
  useAudioStream,
  type AudioStreamBuffer,
} from 'expo-audio';

import {
  concatBytes,
  conditionBuffer,
  pcmToWav,
  peakLevel,
  TARGET_SAMPLE_RATE,
} from './pcm';
import {
  describeCommand,
  parseServerFrame,
  type ClientFrame,
  type PlanCommand,
  type PlanRejection,
} from './protocol';
import { SpeechQueue } from './speechQueue';
import { voiceSocketUrl, type Settings } from './settings';

const MIC_ENCODING = 'int16' as const;
const PING_INTERVAL_MS = 20000;
// Mã đóng WebSocket server dùng khi từ chối xác thực (policy violation).
const WS_POLICY_VIOLATION = 1008;
const RECONNECT_DELAY_MS = 2000;
const RECONNECT_MAX_DELAY_MS = 30000;

export type ConnectionState = 'offline' | 'connecting' | 'ready' | 'error';

export type Message = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  streaming?: boolean;
  commands?: PlanCommand[];
  rejections?: PlanRejection[];
  latency?: Record<string, number>;
};

export type VoiceSession = ReturnType<typeof useVoiceSession>;

let messageCounter = 0;
const nextId = () => `m${(messageCounter += 1)}`;

export function useVoiceSession(settings: Settings | null) {
  const [state, setState] = useState<ConnectionState>('offline');
  const [messages, setMessages] = useState<Message[]>([]);
  const [partial, setPartial] = useState('');
  const [recording, setRecording] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const wantConnectedRef = useRef(false);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const pingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const recordingRef = useRef(false);
  const replyExtRef = useRef('mp3');
  // The server may answer in MP3 (one complete file per clause) or in raw
  // PCM16 (fixed-size frames that mean nothing until a header is added and
  // the whole reply is joined). The two need different handling.
  const replyIsPcmRef = useRef(false);
  const replyRateRef = useRef(TARGET_SAMPLE_RATE);
  const pcmChunksRef = useRef<Uint8Array[]>([]);
  const settingsRef = useRef<Settings | null>(settings);
  settingsRef.current = settings;

  const speech = useMemo(() => new SpeechQueue(setSpeaking), []);

  // ------------------------------------------------------------- microphone
  // A ref keeps the native callback identity stable: the options object below
  // is created once, so the stream is never torn down and rebuilt mid-sentence.
  const onBufferRef = useRef<(buffer: AudioStreamBuffer) => void>(() => {});

  const streamOptions = useMemo(
    () => ({
      sampleRate: TARGET_SAMPLE_RATE,
      channels: 1,
      encoding: MIC_ENCODING,
      onBuffer: (buffer: AudioStreamBuffer) => onBufferRef.current(buffer),
    }),
    []
  );
  const { stream } = useAudioStream(streamOptions);

  onBufferRef.current = (buffer: AudioStreamBuffer) => {
    if (!recordingRef.current) return;
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;

    setLevel(peakLevel(buffer.data, MIC_ENCODING));
    try {
      socket.send(
        conditionBuffer(buffer.data, buffer.sampleRate, buffer.channels, MIC_ENCODING)
      );
    } catch (err) {
      console.warn('mic: send failed', err);
    }
  };

  // ----------------------------------------------------------------- socket
  const send = useCallback((frame: ClientFrame) => {
    const socket = socketRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(frame));
    }
  }, []);

  const appendAssistantDelta = useCallback((text: string) => {
    setMessages((previous) => {
      const last = previous[previous.length - 1];
      if (last?.role === 'assistant' && last.streaming) {
        return [...previous.slice(0, -1), { ...last, text: last.text + text }];
      }
      return [...previous, { id: nextId(), role: 'assistant', text, streaming: true }];
    });
  }, []);

  const handleFrame = useCallback(
    (raw: string) => {
      const frame = parseServerFrame(raw);
      if (!frame) return;

      switch (frame.type) {
        case 'session.ready':
          setState('ready');
          setError(null);
          attemptRef.current = 0; // kết nối tốt: bắt đầu lại backoff từ đầu
          break;

        case 'stt.partial':
          setPartial(frame.text);
          break;

        case 'stt.final':
          setPartial('');
          setMessages((previous) => [
            ...previous,
            { id: nextId(), role: 'user', text: frame.text },
          ]);
          break;

        case 'assistant.delta':
          appendAssistantDelta(frame.text);
          break;

        case 'tts.start':
          // The server names the codec it is about to send; trust it over a guess.
          replyIsPcmRef.current = frame.encoding === 'pcm16';
          replyExtRef.current = frame.encoding === 'mp3' ? 'mp3' : 'wav';
          replyRateRef.current = frame.sample_rate || TARGET_SAMPLE_RATE;
          pcmChunksRef.current = [];
          speech.reset();
          break;

        case 'tts.end':
          if (replyIsPcmRef.current && pcmChunksRef.current.length) {
            // Raw frames are only playable once joined and given a header.
            const joined = concatBytes(pcmChunksRef.current);
            pcmChunksRef.current = [];
            speech.enqueue(pcmToWav(joined, replyRateRef.current), 'wav');
          }
          break;

        case 'assistant.final':
          setMessages((previous) => {
            const last = previous[previous.length - 1];
            const finished: Message = {
              id: last?.streaming ? last.id : nextId(),
              role: 'assistant',
              text: frame.text,
              streaming: false,
              commands: frame.plan?.accepted ?? [],
              rejections: frame.plan?.rejected ?? [],
              latency: frame.latency_ms,
            };
            return last?.role === 'assistant' && last.streaming
              ? [...previous.slice(0, -1), finished]
              : [...previous, finished];
          });
          break;

        case 'notice':
          setError(`${frame.code}: ${frame.message}`);
          break;

        case 'error':
          setError(frame.message);
          break;

        default:
          break;
      }
    },
    [appendAssistantDelta, speech]
  );

  const disconnect = useCallback(() => {
    wantConnectedRef.current = false;
    attemptRef.current = 0;
    if (reconnectRef.current) clearTimeout(reconnectRef.current);
    if (pingRef.current) clearInterval(pingRef.current);
    reconnectRef.current = null;
    pingRef.current = null;
    socketRef.current?.close();
    socketRef.current = null;
    setState('offline');
  }, []);

  const connect = useCallback(() => {
    const current = settingsRef.current;
    if (!current) return;
    if (socketRef.current && socketRef.current.readyState <= WebSocket.OPEN) return;

    wantConnectedRef.current = true;
    setState('connecting');
    setError(null);

    let socket: WebSocket;
    try {
      socket = new WebSocket(voiceSocketUrl(current));
    } catch (err) {
      setState('error');
      setError(`Không mở được kết nối: ${String(err)}`);
      return;
    }
    socket.binaryType = 'arraybuffer';
    socketRef.current = socket;

    socket.onopen = () => {
      if (!isCurrent()) {
        socket.close();
        return;
      }
      send({
        type: 'hello',
        room: current.room || null,
        session_id: current.sessionId,
        sample_rate: TARGET_SAMPLE_RATE,
        channels: 1,
        codec: 'pcm16',
        reply_encoding: 'mp3',
      });
      pingRef.current = setInterval(() => send({ type: 'ping' }), PING_INTERVAL_MS);
    };

    // Every handler checks it is still the current socket. A socket closing
    // late would otherwise clear the reference to its replacement, clear the
    // new ping timer, and report offline while the new connection is alive.
    const isCurrent = () => socketRef.current === socket;

    socket.onmessage = (event) => {
      if (!isCurrent()) return;
      if (typeof event.data === 'string') {
        handleFrame(event.data);
      } else if (event.data instanceof ArrayBuffer) {
        const bytes = new Uint8Array(event.data);
        if (replyIsPcmRef.current) {
          // Buffer until tts.end: a 100 ms slice of PCM is not a playable file.
          pcmChunksRef.current.push(bytes);
        } else {
          speech.enqueue(bytes, replyExtRef.current);
        }
      }
    };

    socket.onerror = () => {
      if (!isCurrent()) return;
      setState('error');
      // Nguyên nhân thật nằm ở mã đóng, onclose luôn chạy ngay sau onerror và
      // sẽ ghi đè thông báo này bằng câu chính xác hơn.
      setError('Không kết nối được tới server.');
    };

    socket.onclose = (event: WebSocketCloseEvent) => {
      if (!isCurrent()) return;
      if (pingRef.current) clearInterval(pingRef.current);
      pingRef.current = null;
      socketRef.current = null;
      recordingRef.current = false;
      setRecording(false);
      setState((previous) => (previous === 'error' ? 'error' : 'offline'));

      // Server từ chối ở bước bắt tay thì đóng với 1008. Thử lại không bao giờ
      // sửa được một khoá sai -- nó chỉ đập vào tường mỗi 30 giây và làm ngập
      // log của server. Dừng lại và nói đúng chỗ cần sửa.
      if (event?.code === WS_POLICY_VIOLATION) {
        wantConnectedRef.current = false;
        attemptRef.current = 0;
        setState('error');
        setError(
          current.token.trim()
            ? 'Server từ chối khoá API. Kiểm tra lại token trong Cài đặt.'
            : 'Server yêu cầu khoá API. Hãy nhập token trong Cài đặt.'
        );
        return;
      }

      if (wantConnectedRef.current) {
        // Back off: an unreachable server should not be retried every 2.5 s
        // for as long as the app is open -- that is a flat battery by lunch.
        const wait = Math.min(
          RECONNECT_MAX_DELAY_MS,
          RECONNECT_DELAY_MS * 2 ** attemptRef.current
        );
        attemptRef.current += 1;
        reconnectRef.current = setTimeout(() => connect(), wait);
      }
    };
  }, [handleFrame, send, speech]);

  // ------------------------------------------------------------ push to talk
  const startTalking = useCallback(async () => {
    if (recordingRef.current) return;
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setError('Chưa kết nối tới server.');
      return;
    }

    const permission = await requestRecordingPermissionsAsync();
    if (!permission.granted) {
      setError('Ứng dụng chưa được cấp quyền dùng micro.');
      return;
    }

    // Cutting in on the assistant is a feature, not an accident: stop locally
    // and tell the server so it abandons the rest of the turn.
    speech.stop();
    pcmChunksRef.current = [];
    send({ type: 'cancel' });
    speech.reset();

    try {
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
      await stream.start();
    } catch (err) {
      setError(`Không mở được micro: ${String(err)}`);
      return;
    }

    setError(null);
    setPartial('');
    recordingRef.current = true;
    setRecording(true);
    send({ type: 'audio.start' });
  }, [send, speech, stream]);

  const stopTalking = useCallback(async () => {
    if (!recordingRef.current) return;
    recordingRef.current = false;
    setRecording(false);
    setLevel(0);

    try {
      stream.stop();
    } catch (err) {
      console.warn('mic: stop failed', err);
    }
    send({ type: 'audio.end' });
    // Hand the output route back to the speaker before the reply arrives.
    try {
      await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true });
    } catch {
      // Not fatal: playback still works, possibly through the earpiece.
    }
  }, [send, stream]);

  const sendText = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      speech.stop();
      speech.reset();
      setMessages((previous) => [
        ...previous,
        { id: nextId(), role: 'user', text: trimmed },
      ]);
      send({ type: 'text', text: trimmed });
    },
    [send, speech]
  );

  const clear = useCallback(() => {
    speech.stop();
    speech.reset();
    setMessages([]);
    setPartial('');
    setError(null);
  }, [speech]);

  // Connect once settings are loaded, and reconnect when they change.
  useEffect(() => {
    if (!settings) return;
    disconnect();
    const timer = setTimeout(connect, 50);
    return () => {
      clearTimeout(timer);
      disconnect();
    };
    // Reconnect only on the fields that define the endpoint.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings?.serverUrl, settings?.token, settings?.room, settings?.sessionId]);

  useEffect(() => () => speech.stop(), [speech]);

  return {
    state,
    messages,
    partial,
    recording,
    speaking,
    level,
    error,
    connect,
    disconnect,
    startTalking,
    stopTalking,
    sendText,
    clear,
    dismissError: () => setError(null),
    describeCommand,
  };
}
