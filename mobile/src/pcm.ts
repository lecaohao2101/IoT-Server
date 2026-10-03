/**
 * Microphone audio conditioning.
 *
 * `useAudioStream` hands us whatever the hardware will give -- often 44.1 or
 * 48 kHz, sometimes stereo. The server speaks exactly one dialect: 16 kHz mono
 * signed 16-bit little-endian PCM, headerless. Converting here rather than
 * declaring the native rate and letting the server resample keeps the wire
 * format identical to what the ESP32 firmware will send, so both clients
 * exercise the same server path.
 */

export const TARGET_SAMPLE_RATE = 16000;
export const TARGET_CHANNELS = 1;

/** Interpret a native buffer as PCM16 samples. */
export function asInt16(data: ArrayBuffer): Int16Array {
  // A partial frame would shift every following sample by one byte.
  const usable = data.byteLength - (data.byteLength % 2);
  return new Int16Array(data, 0, usable / 2);
}

/** Convert float32 samples (expo-audio's default encoding) to PCM16. */
export function floatToInt16(data: ArrayBuffer): Int16Array {
  const floats = new Float32Array(data, 0, Math.floor(data.byteLength / 4));
  const out = new Int16Array(floats.length);
  for (let i = 0; i < floats.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, floats[i]));
    out[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
  }
  return out;
}

/** Average interleaved channels down to one. */
export function toMono(samples: Int16Array, channels: number): Int16Array {
  if (channels <= 1) return samples;
  const frames = Math.floor(samples.length / channels);
  const out = new Int16Array(frames);
  for (let i = 0; i < frames; i += 1) {
    let sum = 0;
    for (let c = 0; c < channels; c += 1) sum += samples[i * channels + c];
    out[i] = (sum / channels) | 0;
  }
  return out;
}

/**
 * Linear-interpolation rate conversion.
 *
 * Good enough for speech heading into a recogniser, and cheap enough to run on
 * the JS thread for every 20 ms buffer. Matches the server's own resampler so
 * audio sounds the same whichever side converts it.
 */
export function resample(samples: Int16Array, srcRate: number, dstRate: number): Int16Array {
  if (srcRate === dstRate || samples.length < 2) return samples;

  const count = Math.max(1, Math.round((samples.length * dstRate) / srcRate));
  const out = new Int16Array(count);
  const step = count > 1 ? (samples.length - 1) / (count - 1) : 0;

  for (let i = 0; i < count; i += 1) {
    const pos = i * step;
    const left = Math.floor(pos);
    const right = Math.min(left + 1, samples.length - 1);
    const frac = pos - left;
    out[i] = (samples[left] + (samples[right] - samples[left]) * frac) | 0;
  }
  return out;
}

/** Full conditioning chain: native buffer in, server-ready bytes out. */
export function conditionBuffer(
  data: ArrayBuffer,
  sourceRate: number,
  channels: number,
  encoding: 'int16' | 'float32'
): ArrayBuffer {
  const raw = encoding === 'float32' ? floatToInt16(data) : asInt16(data);
  const mono = toMono(raw, channels);
  const resampled = resample(mono, sourceRate, TARGET_SAMPLE_RATE);
  // Copy: the native buffer may be reused for the next callback, and a view
  // onto it would be rewritten underneath the WebSocket.
  return resampled.slice().buffer;
}

/**
 * Wrap raw PCM16 in a RIFF/WAVE container.
 *
 * Needed when the server replies with `pcm16` rather than `mp3` -- the mock
 * synthesiser, and Google TTS when LINEAR16 is requested, both send headerless
 * frames that no media player will touch. MP3 replies arrive as complete files
 * and skip this entirely.
 */
export function pcmToWav(pcm: Uint8Array, sampleRate: number, channels = 1): Uint8Array {
  const bytesPerSample = 2;
  const blockAlign = channels * bytesPerSample;
  const byteRate = sampleRate * blockAlign;
  const out = new Uint8Array(44 + pcm.byteLength);
  const view = new DataView(out.buffer);

  const ascii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  };

  ascii(0, 'RIFF');
  view.setUint32(4, 36 + pcm.byteLength, true);
  ascii(8, 'WAVE');
  ascii(12, 'fmt ');
  view.setUint32(16, 16, true); // PCM header size
  view.setUint16(20, 1, true); // format: uncompressed PCM
  view.setUint16(22, channels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bytesPerSample * 8, true);
  ascii(36, 'data');
  view.setUint32(40, pcm.byteLength, true);
  out.set(pcm, 44);
  return out;
}

/** Join the PCM frames of one reply into a single buffer. */
export function concatBytes(chunks: Uint8Array[]): Uint8Array {
  const total = chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return out;
}

/** Peak level 0..1, for the talk button's level meter. */
export function peakLevel(data: ArrayBuffer, encoding: 'int16' | 'float32'): number {
  const samples = encoding === 'float32' ? floatToInt16(data) : asInt16(data);
  let peak = 0;
  const stride = Math.max(1, Math.floor(samples.length / 256)); // sample it, don't scan it
  for (let i = 0; i < samples.length; i += stride) {
    const magnitude = Math.abs(samples[i]);
    if (magnitude > peak) peak = magnitude;
  }
  return Math.min(1, peak / 32768);
}
