/**
 * Checks for the parts of the app that are pure logic.
 *
 * Audio conditioning and URL building are where a silent mistake costs the most:
 * a wrong sample rate produces a chipmunk recording that the recogniser rejects
 * with no useful error, and a malformed URL just fails to connect. Both are
 * testable without a device, so they are tested.
 *
 *   npm run verify
 */

import assert from 'node:assert/strict';

import {
  asInt16,
  concatBytes,
  conditionBuffer,
  pcmToWav,
  floatToInt16,
  peakLevel,
  resample,
  toMono,
  TARGET_SAMPLE_RATE,
} from '../src/pcm.ts';
import { describeCommand, parseServerFrame } from '../src/protocol.ts';
import {
  DEFAULT_SETTINGS,
  DEPLOYED_SERVER_URL,
  authHeaders,
  httpUrl,
  voiceSocketUrl,
} from '../src/settings.ts';

let passed = 0;
let failed = 0;

function test(name: string, run: () => void): void {
  try {
    run();
    passed += 1;
    console.log(`  [OK]  ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`  [LỖI] ${name}`);
    console.log(`        ${(error as Error).message.split('\n')[0]}`);
  }
}

function pcm16(values: number[]): ArrayBuffer {
  return Int16Array.from(values).buffer;
}

// ------------------------------------------------------------------- PCM

test('asInt16 bỏ qua byte lẻ thay vì lệch toàn bộ mẫu', () => {
  const bytes = new Uint8Array([0x01, 0x02, 0x03]); // 1.5 mẫu
  assert.equal(asInt16(bytes.buffer).length, 1);
});

test('floatToInt16 ánh xạ đúng biên -1..1', () => {
  const floats = Float32Array.from([0, 1, -1, 0.5]);
  const out = floatToInt16(floats.buffer);
  assert.equal(out[0], 0);
  assert.equal(out[1], 32767);
  assert.equal(out[2], -32768);
  assert.ok(Math.abs(out[3] - 16383) <= 1);
});

test('floatToInt16 kẹp giá trị vượt biên, không cho tràn số', () => {
  const out = floatToInt16(Float32Array.from([3, -3]).buffer);
  assert.equal(out[0], 32767);
  assert.equal(out[1], -32768);
});

test('toMono lấy trung bình hai kênh', () => {
  const stereo = Int16Array.from([1000, 2000, 0, 0]);
  const mono = toMono(stereo, 2);
  assert.deepEqual(Array.from(mono), [1500, 0]);
});

test('toMono trả nguyên vẹn khi đã là mono', () => {
  const mono = Int16Array.from([1, 2, 3]);
  assert.equal(toMono(mono, 1), mono);
});

test('resample 48k -> 16k còn một phần ba số mẫu', () => {
  const input = new Int16Array(4800);
  const out = resample(input, 48000, 16000);
  assert.ok(Math.abs(out.length - 1600) <= 2, `nhận ${out.length}`);
});

test('resample giữ nguyên khi cùng tần số', () => {
  const input = Int16Array.from([1, 2, 3]);
  assert.equal(resample(input, 16000, 16000), input);
});

test('resample nội suy tuyến tính, không nhảy bậc', () => {
  // 3 mẫu -> 5 mẫu: các điểm giữa phải nằm giữa hai mẫu gốc
  const out = resample(Int16Array.from([0, 100, 200]), 3, 5);
  assert.equal(out.length, 5);
  assert.equal(out[0], 0);
  assert.equal(out[4], 200);
  for (let i = 1; i < out.length; i += 1) assert.ok(out[i] >= out[i - 1]);
});

test('conditionBuffer: 48 kHz stereo -> 16 kHz mono PCM16', () => {
  const frames = 480; // 10 ms ở 48 kHz
  const stereo = new Int16Array(frames * 2).fill(1234);
  const out = conditionBuffer(stereo.buffer, 48000, 2, 'int16');
  const samples = new Int16Array(out);
  assert.ok(Math.abs(samples.length - 160) <= 2, `nhận ${samples.length} mẫu`);
  assert.ok(samples.every((s) => Math.abs(s - 1234) <= 1));
});

test('conditionBuffer sao chép dữ liệu, không giữ view vào buffer native', () => {
  const source = Int16Array.from([100, 200, 300, 400]);
  const out = conditionBuffer(source.buffer, TARGET_SAMPLE_RATE, 1, 'int16');
  source.fill(0); // buffer native bị ghi đè cho callback kế tiếp
  assert.deepEqual(Array.from(new Int16Array(out)), [100, 200, 300, 400]);
});

test('peakLevel phân biệt im lặng với tiếng nói', () => {
  assert.equal(peakLevel(pcm16(new Array(320).fill(0)), 'int16'), 0);
  assert.ok(peakLevel(pcm16(new Array(320).fill(30000)), 'int16') > 0.9);
});

test('pcmToWav dựng header RIFF đúng chuẩn', () => {
  const pcm = new Uint8Array(Int16Array.from([1, -1, 2, -2]).buffer);
  const wav = pcmToWav(pcm, 16000);
  const text = (start: number, length: number) =>
    String.fromCharCode(...wav.slice(start, start + length));
  const view = new DataView(wav.buffer, wav.byteOffset, wav.byteLength);

  assert.equal(text(0, 4), 'RIFF');
  assert.equal(text(8, 4), 'WAVE');
  assert.equal(text(12, 4), 'fmt ');
  assert.equal(text(36, 4), 'data');
  assert.equal(wav.byteLength, 44 + pcm.byteLength);
  assert.equal(view.getUint32(4, true), 36 + pcm.byteLength);
  assert.equal(view.getUint16(20, true), 1, 'định dạng PCM không nén');
  assert.equal(view.getUint16(22, true), 1, 'mono');
  assert.equal(view.getUint32(24, true), 16000, 'tần số lấy mẫu');
  assert.equal(view.getUint32(28, true), 32000, 'byte rate = 16000 * 2');
  assert.equal(view.getUint16(32, true), 2, 'block align');
  assert.equal(view.getUint16(34, true), 16, 'độ sâu bit');
  assert.equal(view.getUint32(40, true), pcm.byteLength);
  assert.deepEqual(Array.from(wav.slice(44)), Array.from(pcm));
});

test('concatBytes nối đúng thứ tự các khung PCM rời', () => {
  const joined = concatBytes([
    Uint8Array.from([1, 2]),
    Uint8Array.from([]),
    Uint8Array.from([3, 4, 5]),
  ]);
  assert.deepEqual(Array.from(joined), [1, 2, 3, 4, 5]);
});

test('concatBytes xử lý được danh sách rỗng', () => {
  assert.equal(concatBytes([]).byteLength, 0);
});

// -------------------------------------------------------------- giao thức

test('parseServerFrame đọc được khung hợp lệ', () => {
  const frame = parseServerFrame('{"type":"stt.final","ts":"x","text":"bật đèn","confidence":0.9}');
  assert.equal(frame?.type, 'stt.final');
});

test('parseServerFrame trả null thay vì ném lỗi khi JSON hỏng', () => {
  assert.equal(parseServerFrame('{không phải json'), null);
  assert.equal(parseServerFrame('"chuỗi trần"'), null);
  assert.equal(parseServerFrame('{"thiếu":"type"}'), null);
});

test('describeCommand đọc boolean bằng tiếng Việt, không in true/false', () => {
  const text = describeCommand({ device_id: 'front_door_lock', capability: 'locked', value: false });
  assert.ok(text.includes('tắt'), text);
  assert.ok(!text.includes('false'), text);
});

// ----------------------------------------------------------------- cấu hình

test('voiceSocketUrl đổi http thành ws và gắn token', () => {
  const url = voiceSocketUrl({ ...DEFAULT_SETTINGS, serverUrl: 'http://10.0.0.5:8000', token: 'abc' });
  assert.ok(url.startsWith('ws://10.0.0.5:8000/ws/voice?'), url);
  assert.ok(url.includes('token=abc'), url);
  assert.ok(url.includes('room=living_room'), url);
});

test('voiceSocketUrl đổi https thành wss', () => {
  const url = voiceSocketUrl({ ...DEFAULT_SETTINGS, serverUrl: 'https://home.example.com' });
  assert.ok(url.startsWith('wss://home.example.com/ws/voice'), url);
});

test('voiceSocketUrl thêm scheme khi người dùng chỉ gõ host', () => {
  const url = voiceSocketUrl({ ...DEFAULT_SETTINGS, serverUrl: '192.168.1.12:8000', token: '' });
  assert.ok(url.startsWith('ws://192.168.1.12:8000/ws/voice'), url);
  assert.ok(!url.includes('token='), url);
});

test('voiceSocketUrl cắt dấu / thừa, không tạo //ws/voice', () => {
  const url = voiceSocketUrl({ ...DEFAULT_SETTINGS, serverUrl: 'http://host:8000///', token: '' });
  assert.ok(url.startsWith('ws://host:8000/ws/voice'), url);
});

test('httpUrl và authHeaders dùng chung cách chuẩn hoá', () => {
  assert.equal(
    httpUrl({ ...DEFAULT_SETTINGS, serverUrl: 'host:8000/' }, '/api/v1/devices'),
    'http://host:8000/api/v1/devices'
  );
  assert.deepEqual(authHeaders({ ...DEFAULT_SETTINGS, token: '  k  ' }), {
    Authorization: 'Bearer k',
  });
  assert.deepEqual(authHeaders({ ...DEFAULT_SETTINGS, token: '   ' }), {});
});

test('mặc định trỏ thẳng vào server đã deploy, quét QR là chạy', () => {
  assert.equal(DEFAULT_SETTINGS.serverUrl, DEPLOYED_SERVER_URL);
  assert.ok(DEPLOYED_SERVER_URL.startsWith('https://'), DEPLOYED_SERVER_URL);
  assert.ok(voiceSocketUrl(DEFAULT_SETTINGS).startsWith('wss://'));
});

test('token KHÔNG được nhúng sẵn trong app', () => {
  // Một bí mật dùng chung nằm trong APK thì ai giải nén cũng lấy được.
  assert.equal(DEFAULT_SETTINGS.token, '');
});

// ------------------------------------------------------------------- Devices & MQTT
import { DEFAULT_DEVICES, isDeviceOn, getDeviceStatusLabel, type DeviceItem } from '../src/devices.ts';
import { DEFAULT_MQTT_SETTINGS } from '../src/settings.ts';

test('DEFAULT_DEVICES chứa đúng 10 thiết bị phần cứng thực tế của căn hộ', () => {
  assert.equal(DEFAULT_DEVICES.length, 10);
});

test('isDeviceOn nhận diện đúng trạng thái BẬT và TẮT của đèn', () => {
  const lightOn: DeviceItem = {
    id: 'living_room_light',
    name: 'Đèn phòng khách',
    type: 'light',
    room: 'living_room',
    roomName: 'Phòng khách',
    capabilities: ['power'],
    state: { power: 'on' },
  };
  const lightOff: DeviceItem = { ...lightOn, state: { power: 'off' } };
  assert.equal(isDeviceOn(lightOn), true);
  assert.equal(isDeviceOn(lightOff), false);
  assert.equal(getDeviceStatusLabel(lightOn), 'ĐANG BẬT');
  assert.equal(getDeviceStatusLabel(lightOff), 'ĐANG TẮT');
});

test('isDeviceOn nhận diện đúng trạng thái rèm và khóa cửa', () => {
  const lockLocked: DeviceItem = {
    id: 'front_door_lock',
    name: 'Khóa cửa',
    type: 'lock',
    room: 'living_room',
    roomName: 'Phòng khách',
    capabilities: ['locked'],
    state: { locked: true },
  };
  const lockUnlocked: DeviceItem = { ...lockLocked, state: { locked: false } };
  assert.equal(isDeviceOn(lockLocked), false); // locked = off/đã khóa
  assert.equal(isDeviceOn(lockUnlocked), true); // unlocked = on/đã mở
  assert.equal(getDeviceStatusLabel(lockLocked), 'Đã khóa');
  assert.equal(getDeviceStatusLabel(lockUnlocked), 'Đã mở khóa');
});

test('DEFAULT_MQTT_SETTINGS trỏ đúng broker HiveMQ Cloud TLS 8884', () => {
  assert.equal(DEFAULT_MQTT_SETTINGS.enabled, true);
  assert.equal(DEFAULT_MQTT_SETTINGS.port, 8884);
  assert.equal(DEFAULT_MQTT_SETTINGS.ssl, true);
  assert.equal(DEFAULT_MQTT_SETTINGS.baseTopic, 'home');
});

console.log(`\n${passed} đạt, ${failed} lỗi`);
process.exit(failed === 0 ? 0 : 1);
