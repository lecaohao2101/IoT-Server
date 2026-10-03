# Smart Apartment — Voice client

Ứng dụng React Native (Expo) đóng vai **Mobile app** trong sơ đồ hệ thống: giữ nút
để nói, âm thanh đi thẳng qua WebSocket tới server, nghe câu trả lời phát ra loa, và
nhìn thấy đúng những lệnh mà server đã chấp nhận hay từ chối.

```
   ┌──────────────────────────────┐
   │  Căn hộ thông minh      ●    │   ● = trạng thái kết nối
   ├──────────────────────────────┤
   │        bạn: bật đèn phòng    │
   │             khách lên 70%    │
   │                              │
   │ Đã chỉnh Đèn phòng khách:    │
   │ brightness 70.               │
   │ ✓ living_room_light.power→on │
   │ ✓ ...brightness → 70         │
   │ 573 ms · giọng nói sau 568ms │
   ├──────────────────────────────┤
   │      (  GIỮ ĐỂ NÓI  )        │
   └──────────────────────────────┘
```

---

## Chạy thử

```bash
cd mobile
npm install
npm start          # hiện mã QR
```

Cài **Expo Go** trên điện thoại, quét mã QR. Điện thoại và máy chạy server phải
**cùng một mạng Wi-Fi**.

Lần đầu mở app, bấm **Cài đặt** và điền địa chỉ LAN của server, ví dụ
`http://192.168.1.12:8000`. Tìm địa chỉ đó bằng `ipconfig` (Windows) hoặc
`ifconfig` (macOS/Linux) — **không dùng `localhost`**, vì với điện thoại thì
`localhost` là chính nó.

Server phải lắng nghe trên mọi interface chứ không chỉ loopback:

```bash
cd ../server
.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Nếu server bật `API_KEY`, điền cùng giá trị đó vào ô **Token**.

---

## Cách hoạt động

### Thu âm
`expo-audio` cung cấp `useAudioStream` — luồng PCM thời gian thực từ micro, không
phải "ghi ra file rồi gửi". Mỗi buffer được đưa qua [`src/pcm.ts`](src/pcm.ts) để
chuẩn hoá về **16 kHz, mono, PCM16 little-endian** rồi gửi thẳng qua WebSocket.

Việc chuyển đổi làm ở client chứ không khai tần số gốc rồi để server resample, vì
như vậy app gửi lên **đúng khuôn dạng mà firmware ESP32 sẽ gửi** — hai client cùng
đi một đường dẫn trên server, lỗi của bên này lộ ra ở bên kia.

### Phát câu trả lời
Server tổng hợp giọng nói theo **từng mệnh đề** và gửi ngay khi xong, nên người
dùng nghe thấy câu trả lời trước khi mô hình viết xong câu. App xử lý hai dạng:

| `tts.start` báo | Ý nghĩa | App làm gì |
|---|---|---|
| `mp3` | mỗi khung nhị phân là một file MP3 hoàn chỉnh | phát ngay từng khung, nối tiếp nhau |
| `pcm16` | các khung là mảnh PCM thô (~100 ms) | gom lại tới `tts.end`, thêm header WAV rồi mới phát |

Phân biệt này là bắt buộc: một mảnh PCM 100 ms không phải file phát được, ghi
thẳng ra `.wav` sẽ cho ra file hỏng. [`src/speechQueue.ts`](src/speechQueue.ts)
đảm bảo các mệnh đề phát **đúng thứ tự và không chồng tiếng**.

### Cắt lời (barge-in)
Nhấn nút khi trợ lý đang nói sẽ dừng phát ngay tại máy **và** gửi `cancel` lên
server để nó bỏ dở lượt đang chạy — không phải chờ TTS xả hết.

---

## Cấu trúc

```
App.tsx                  màn hình chính
src/
  protocol.ts            hợp đồng WebSocket, soi gương từ server/app/api/ws_protocol.py
  pcm.ts                 chuẩn hoá audio: mono, resample, header WAV
  settings.ts            địa chỉ server / token / phòng, lưu trên máy
  speechQueue.ts         hàng đợi phát câu trả lời, tuần tự
  useVoiceSession.ts     WebSocket + micro + trạng thái hội thoại
  ui/                    StatusBar, Transcript, TalkButton, SettingsModal
scripts/
  verify.ts              test logic thuần (PCM, URL, giao thức)
  e2e.ts                 test hợp đồng app ↔ server bằng kết nối thật
```

---

## Kiểm thử

```bash
npm run typecheck   # app + scripts
npm run verify      # 22 test logic thuần, không cần server
npm run bundle      # bundle Android, bắt lỗi import mà tsc không thấy
```

Kiểm thử hợp đồng với server đang chạy:

```bash
npm run e2e -- ws://127.0.0.1:8000
```

Script này kết nối thật và kiểm đúng những giả định mà app dựa vào: thứ tự khung,
codec khai báo trong `tts.start`, mỗi khung MP3 là file hoàn chỉnh, và
`assistant.final` có kèm command plan. Nếu server đổi khuôn dạng, script gãy trước
khi người dùng cầm điện thoại phát hiện ra.

Đã chạy thực tế với cả hai cấu hình server:

```
TTS=google  ->  1 khung MP3, 23 040 byte
TTS=mock    ->  27 khung PCM, ghép lại thành WAV 2,67s
```

---

## Giới hạn hiện tại

**STT chưa dùng được.** Service account còn thiếu quyền `speech.recognizers.recognize`,
nên nói vào micro sẽ nhận lỗi 403 hiện trên banner đỏ. Cách cấp quyền nằm trong
[server/README.md](../server/README.md) §8.

Trong lúc chờ, chạy server với `STT_PROVIDER=mock` để kiểm tra toàn bộ phần còn lại
— thu âm, truyền, suy luận, kiểm duyệt lệnh, phát giọng nói. Bộ nhận dạng mock trả
về câu kịch bản sẵn thay vì nội dung bạn nói, nhưng mọi chặng khác đều là thật.

**Phạm vi là voice.** Dashboard và điều khiển thủ công nằm ở REST API của server và
chưa được dựng trong app này.
