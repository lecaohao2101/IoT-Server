/**
 * Kiểm thử hợp đồng giữa app và server bằng một kết nối WebSocket thật.
 *
 * Dùng đúng những giả định mà `useVoiceSession` dựa vào, và chỉ những giả định
 * đó. Nếu server đổi khuôn dạng khung hay cách đóng gói audio, script này gãy
 * trước khi người dùng cầm điện thoại phát hiện ra.
 *
 *   node --experimental-strip-types scripts/e2e.ts ws://127.0.0.1:8011
 */

import assert from 'node:assert/strict';

import { concatBytes, pcmToWav } from '../src/pcm.ts';

const url = process.argv[2] ?? 'ws://127.0.0.1:8011';
/** Codec app yêu cầu. Server có thể trả về pcm16 nếu provider không mã hoá được. */
const wanted = (process.argv[3] ?? 'mp3') as 'mp3' | 'pcm16';
const TIMEOUT_MS = 30000;

type Frame = Record<string, any>;

/** MP3 bắt đầu bằng thẻ ID3 hoặc frame sync 11 bit. */
function looksLikeMp3(bytes: Uint8Array): boolean {
  if (bytes.length < 4) return false;
  const isId3 = bytes[0] === 0x49 && bytes[1] === 0x44 && bytes[2] === 0x33;
  const isSync = bytes[0] === 0xff && (bytes[1] & 0xe0) === 0xe0;
  return isId3 || isSync;
}

async function main(): Promise<number> {
  const frames: Frame[] = [];
  const audio: Uint8Array[] = [];
  const ttsStart: { frame: Frame | null } = { frame: null };

  const socket = new WebSocket(`${url}/ws/voice`);
  socket.binaryType = 'arraybuffer';

  const done = new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('hết thời gian chờ')), TIMEOUT_MS);

    socket.onopen = () => {
      socket.send(
        JSON.stringify({
          type: 'hello',
          room: 'living_room',
          session_id: 'e2e-mobile',
          sample_rate: 16000,
          channels: 1,
          codec: 'pcm16',
          reply_encoding: wanted,
        })
      );
    };

    socket.onmessage = (event) => {
      if (typeof event.data === 'string') {
        const frame = JSON.parse(event.data) as Frame;
        frames.push(frame);
        if (frame.type === 'session.ready') {
          socket.send(JSON.stringify({ type: 'text', text: 'bật đèn phòng khách lên 70 phần trăm' }));
        }
        if (frame.type === 'tts.start') ttsStart.frame = frame;
        if (frame.type === 'assistant.final') {
          clearTimeout(timer);
          socket.close();
          resolve();
        }
      } else {
        audio.push(new Uint8Array(event.data as ArrayBuffer));
      }
    };

    socket.onerror = () => {
      clearTimeout(timer);
      reject(new Error(`không kết nối được tới ${url}`));
    };
  });

  await done;

  const kinds = frames.map((f) => f.type);
  console.log(`  khung nhận được: ${kinds.join(' -> ')}`);
  console.log(`  khung audio: ${audio.length}, tổng ${audio.reduce((n, a) => n + a.length, 0)} byte`);

  let failed = 0;
  const check = (name: string, run: () => void) => {
    try {
      run();
      console.log(`  [OK]  ${name}`);
    } catch (error) {
      failed += 1;
      console.log(`  [LỖI] ${name}\n        ${(error as Error).message.split('\n')[0]}`);
    }
  };

  check('server chào bằng session.ready', () => {
    assert.ok(kinds.includes('session.ready'));
  });

  check('có assistant.delta trước khi có assistant.final', () => {
    assert.ok(kinds.indexOf('assistant.delta') < kinds.indexOf('assistant.final'));
    assert.ok(kinds.indexOf('assistant.delta') >= 0);
  });

  check('tts.start khai báo codec để app biết cách xử lý', () => {
    assert.ok(ttsStart.frame, 'không nhận được tts.start');
    assert.ok(
      ['mp3', 'pcm16', 'wav'].includes(ttsStart.frame!.encoding),
      `codec lạ: ${ttsStart.frame!.encoding}`
    );
    assert.ok(typeof ttsStart.frame!.sample_rate === 'number' && ttsStart.frame!.sample_rate > 0);
  });

  check('tts.end đóng luồng audio', () => {
    assert.ok(kinds.includes('tts.end'));
    assert.ok(kinds.indexOf('tts.start') < kinds.indexOf('tts.end'));
  });

  if (ttsStart.frame?.encoding === 'mp3') {
    check('MP3: mỗi khung nhị phân là một file hoàn chỉnh, phát được ngay', () => {
      assert.ok(audio.length > 0, 'không có audio nào');
      audio.forEach((chunk, index) => {
        assert.ok(
          looksLikeMp3(chunk),
          `khung ${index} không bắt đầu như MP3: ${Array.from(chunk.slice(0, 4))}`
        );
      });
    });
  } else {
    check('PCM16: khung là mảnh thô, app phải ghép lại rồi mới phát', () => {
      assert.ok(audio.length > 0, 'không có audio nào');
      assert.ok(
        !looksLikeMp3(audio[0]),
        'khung PCM không được trông giống MP3, nếu không app sẽ xử lý nhầm nhánh'
      );
      const joined = concatBytes(audio);
      assert.equal(joined.byteLength % 2, 0, 'PCM16 phải chẵn byte');

      const wav = pcmToWav(joined, ttsStart.frame!.sample_rate);
      assert.equal(String.fromCharCode(...wav.slice(0, 4)), 'RIFF');
      const seconds = joined.byteLength / 2 / ttsStart.frame!.sample_rate;
      assert.ok(seconds > 0.2, `audio quá ngắn: ${seconds.toFixed(2)}s`);
      console.log(`        ghép ${audio.length} khung -> WAV ${seconds.toFixed(2)}s`);
    });
  }

  check('assistant.final kèm command plan đã thực hiện', () => {
    const final = frames.find((f) => f.type === 'assistant.final')!;
    const accepted = final.plan?.accepted ?? [];
    assert.ok(accepted.length > 0, 'plan rỗng');
    assert.ok(
      accepted.some((c: Frame) => c.device_id === 'living_room_light'),
      JSON.stringify(accepted)
    );
  });

  check('assistant.final báo độ trễ để app hiển thị', () => {
    const final = frames.find((f) => f.type === 'assistant.final')!;
    assert.ok(typeof final.latency_ms?.total === 'number');
  });

  console.log(failed === 0 ? '\nHợp đồng app <-> server khớp.' : `\n${failed} kiểm tra thất bại.`);
  return failed === 0 ? 0 : 1;
}

main().then(
  (code) => process.exit(code),
  (error) => {
    console.error(`  [LỖI] ${(error as Error).message}`);
    process.exit(1);
  }
);
