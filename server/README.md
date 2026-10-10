# Smart Apartment Server

Backend điều khiển căn hộ thông minh bằng giọng nói: nhận luồng âm thanh từ ESP32
và mobile app qua WebSocket, nhận dạng tiếng Việt, suy luận bằng LLM, kiểm duyệt
lệnh bằng bộ luật tĩnh, rồi đẩy xuống thiết bị qua MQTT — và trả lời bằng giọng nói
ngay trong lúc còn đang suy luận.

```
   ESP32 / Mobile app
        │  WebSocket (PCM 16 kHz)          ┌──────────────────────────────┐
        └─────────────────────────────────►│  FastAPI (async)             │
                                           │                              │
   Mobile app ── HTTP /api/v1 ────────────►│  ┌────────────────────────┐  │
                                           │  │ STT  ─► LLM ─► TTS     │  │
   Dashboard ── WS /ws/events ◄────────────│  │        │               │  │
                                           │  │        ▼               │  │
                                           │  │  Command Plan          │  │
                                           │  │  (rule validator)      │  │
                                           │  └────────┬───────────────┘  │
                                           │           │                  │
                                           │  Redis ◄──┴── Device State   │
                                           └───────────┬──────────────────┘
                                                       │ MQTT (QoS 1)
                                              Thiết bị trong căn hộ
```

---

## 1. Bắt đầu trong 2 phút

Server chạy được **hoàn toàn offline**: không cần Redis, không cần MQTT broker,
không cần API key. Các provider mock sẽ thay thế STT/LLM/TTS và một transport
loopback sẽ đóng vai thiết bị.

```bash
cd server
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt   # Linux/macOS: .venv/bin/python

cp .env.example .env        # mặc định đã là chế độ offline
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
```

Mở <http://localhost:8000/docs> để xem toàn bộ REST API.

Thử một lượt hội thoại đầy đủ (giả lập ESP32):

```bash
.venv/Scripts/python.exe tools/simulate_client.py --say "bật đèn phòng khách lên 70 phần trăm"
```

```
  phiên: sess_ab12cd34ef56
  bạn: bật đèn phòng khách lên 70 phần trăm
  trợ lý: Đã chỉnh Đèn phòng khách: brightness 70.
    -> living_room_light.power = on
    -> living_room_light.brightness = 70
    latency: {'llm_first_token': 3, 'first_audio': 29, 'total': 36}
  đã lưu âm thanh trả lời: reply.wav (2.7s)
```

Chạy toàn bộ stack thật (Mosquitto + Redis + server + căn hộ giả lập):

```bash
docker compose --profile sim up --build
```

---

## 2. Kiến trúc

Bốn phân lớp, ánh xạ trực tiếp sang sơ đồ hệ thống.

### 2.1 Thiết bị biên
Không nằm trong repo này, nhưng hợp đồng giao tiếp thì có: xem
[§4 Giao thức WebSocket](#4-giao-thức-websocket) và [§6 Hợp đồng MQTT](#6-hợp-đồng-mqtt).

### 2.2 Giao thức mạng
| Luồng | Giao thức | Lý do |
|---|---|---|
| Âm thanh hai chiều | WebSocket | Kết nối duy trì liên tục, khung nhị phân, độ trễ thấp |
| Dashboard, điều khiển thủ công | HTTP/REST | Tác vụ rời rạc, dễ cache, dễ debug |
| Sự kiện trạng thái | WebSocket `/ws/events` | Đẩy một chiều tới nhiều dashboard |
| Lệnh xuống thiết bị | MQTT QoS 1 | Nhẹ, pub/sub, có retain và last-will cho thiết bị chập chờn |

### 2.3 Máy chủ lõi
FastAPI async. Toàn bộ phụ thuộc được dựng một lần trong
[`app/container.py`](app/container.py) — không có singleton toàn cục ngoài `Settings`,
nên thay thế bất kỳ thành phần nào trong test chỉ tốn ba dòng.

Trạng thái nằm ở Redis (`DeviceStateManager`, `ConversationManager`), không nằm trong
tiến trình, để nhiều worker nhìn thấy cùng một căn hộ.

### 2.4 Đường ống AI
```
audio ─► STT (streaming) ─► LLM (JSON có schema) ─┬─► trường "speech" ─► TTS ─► audio
                                                  └─► trường "commands" ─► Validator ─► MQTT
```

Điểm quan trọng về độ trễ: trường `speech` được **bóc ra khỏi JSON ngay khi model
còn đang sinh**, nhờ `StreamingStringField`, rồi cắt thành từng mệnh đề và tổng hợp
song song. Người dùng nghe câu trả lời sớm hơn khoảng một giây so với việc chờ
dấu `}` cuối cùng.

---

## 3. Lớp an toàn (Command Plan)

Đây là phần quan trọng nhất của hệ thống, và là phần được test nhiều nhất.

> Prompt có thể bị dẫn dụ. Luật chạy **sau** model thì không.

Mọi lệnh — dù phát sinh từ giọng nói, từ nút bấm trong app, hay từ một scene — đều
đi qua cùng một `CommandValidator`. Bộ validator là hàm thuần: cùng input, cùng
catalogue, cùng policy, cùng đồng hồ thì luôn cho cùng kết quả.

Thứ tự kiểm tra cho mỗi lệnh (gặp lỗi đầu tiên là dừng):

1. Thiết bị có tồn tại không (có một lần thử sửa khi model trả về tên hiển thị)
2. Thuộc tính có tồn tại và có ghi được không
3. Giá trị có thuộc miền của thuộc tính không → **có thể bị kẹp (clamp)**
4. Giá trị có vượt giới hạn hẹp hơn trong policy không → **có thể bị kẹp**
5. Có luật `forbid` nào chặn không
6. Thiết bị có online không (nếu policy yêu cầu)
7. Hạn mức lệnh cho thiết bị đó còn không
8. Thao tác nhạy cảm → **không gửi**, mà treo lại chờ người dùng xác nhận

Kết quả là một `CommandPlan` gồm `accepted` / `rejected` / `pending_confirmation` /
`notes`. **Không có lệnh nào bị bỏ qua trong im lặng** — lý do từ chối và các giá trị
bị kẹp đều được nói lại cho người dùng.

Cấu hình trong [`config/safety.yaml`](config/safety.yaml):

```yaml
limits:
  - match: { device_type: air_conditioner, capability: temperature }
    minimum: 18                      # máy cho tới 16, policy không cho
    maximum: 30

  - match: { room: bedroom, capability: brightness }
    maximum: 40
    quiet_hours_only: true           # chỉ áp dụng 22:00–06:00

forbid:
  - match: { device_type: lock, capability: locked }
    value: false
    quiet_hours_only: true
    message: "Vì lý do an toàn, mình không mở khóa cửa bằng giọng nói vào ban đêm."
```

Luồng xác nhận cho thao tác nhạy cảm:

```
người dùng: "mở khóa cửa"
   → plan.pending_confirmation = [front_door_lock.locked = false]
   → trợ lý: "Bạn chắc chắn muốn mở khóa cửa chính chứ?"
người dùng: "vâng"
   → lệnh được gửi; LLM KHÔNG được gọi lại (tránh bị đổi ý giữa chừng)
```

---

## 4. Giao thức WebSocket

`ws://host:8000/ws/voice?token=<credential>&device_id=<id>&room=<room_id>`

Khung **text** là JSON điều khiển. Khung **nhị phân** chỉ chứa âm thanh:
PCM 16-bit little-endian, mono, không header. Client thu ở tần số khác thì khai báo
trong `hello`, server sẽ tự resample.

### Client → Server

| `type` | Trường | Ý nghĩa |
|---|---|---|
| `hello` | `room`, `sample_rate`, `channels`, `reply_encoding`, `session_id` | Khai báo phiên. `reply_encoding`: `pcm16` \| `mp3` \| `wav` \| `none` |
| *(binary)* | — | Một khung PCM microphone |
| `audio.start` | — | Bắt đầu câu nói mới (tuỳ chọn) |
| `audio.end` | — | Kết thúc câu nói |
| `text` | `text` | Gửi thẳng văn bản, bỏ qua nhận dạng |
| `cancel` | — | Ngắt lượt đang chạy |
| `ping` | — | Giữ kết nối |

### Server → Client

| `type` | Trường |
|---|---|
| `session.ready` | `session_id`, `room`, `sample_rate`, `silence_timeout_ms`, `max_utterance_s` |
| `stt.partial` / `stt.final` | `text`, `confidence` |
| `assistant.delta` | `text` — từng đoạn câu trả lời |
| `tts.start` / `tts.end` | `encoding`, `sample_rate` |
| *(binary)* | Khung âm thanh trả lời (~100 ms mỗi khung với PCM) |
| `assistant.final` | `text`, `plan`, `needs_clarification`, `latency_ms` |
| `state.changed` | `device_id`, `state`, `online` |
| `notice` / `error` | `code`, `message` |

### Vòng đời một câu nói

```
client ──► {"type":"hello","room":"living_room","sample_rate":16000}
server ──► {"type":"session.ready","session_id":"sess_..."}
client ──► <PCM> <PCM> <PCM> ...
client ──► {"type":"audio.end"}                     ← hoặc để server tự ngắt
server ──► {"type":"stt.partial","text":"bật đèn"}
server ──► {"type":"stt.final","text":"bật đèn phòng khách"}
server ──► {"type":"assistant.delta","text":"Đã bật"}
server ──► {"type":"tts.start","encoding":"pcm16","sample_rate":16000}
server ──► <PCM> <PCM> ...
server ──► {"type":"tts.end"}
server ──► {"type":"assistant.final","text":"...","plan":{...}}
```

Câu nói kết thúc khi xảy ra **một trong ba**: client gửi `audio.end`, bộ nhận dạng
báo hết giọng nói, hoặc bộ phát hiện im lặng của server quyết định (ngưỡng năng
lượng tự thích nghi theo tiếng ồn phòng).

### Barge-in
Một khung âm thanh đến trong lúc trợ lý đang nói sẽ **hủy ngay lượt đang chạy**:
dừng TTS, dừng gửi audio, và mở câu nói mới. Không cần chờ TTS xả hết.

---

## 5. REST API

Tất cả dưới `/api/v1`. Xác thực: `Authorization: Bearer <API_KEY>` hoặc `X-API-Key`.

| Method | Path | Mô tả |
|---|---|---|
| `GET` | `/healthz` | Liveness, không chạm phụ thuộc nào. Không bao giờ cần xác thực |
| `GET` | `/readyz` | Readiness: 503 nếu hỏng. Ẩn danh chỉ thấy `status`/`version`/`build`; có credential mới thấy chi tiết hạ tầng |
| `GET` | `/api/v1/system/info` | Thông tin server, provider, policy. **Cần xác thực** |
| `GET` | `/api/v1/rooms` | Danh sách phòng |
| `GET` | `/api/v1/devices?room=` | Thiết bị kèm trạng thái và miền giá trị |
| `GET` | `/api/v1/devices/{id}` | Một thiết bị |
| `POST` | `/api/v1/devices/{id}/command` | Đặt một thuộc tính |
| `POST` | `/api/v1/commands` | Đặt nhiều thuộc tính trong một plan |
| `GET` | `/api/v1/scenes` | Danh sách ngữ cảnh |
| `POST` | `/api/v1/scenes/{id}/activate` | Kích hoạt ngữ cảnh |
| `POST` | `/api/v1/chat` | Một lượt hội thoại bằng văn bản |
| `GET` | `/api/v1/chat/{session_id}` | Lịch sử phiên |
| `DELETE` | `/api/v1/chat/{session_id}` | Xoá phiên |
| `POST` | `/api/v1/speak` | Tổng hợp giọng nói cho văn bản bất kỳ (WAV/MP3) |
| `GET` | `/api/v1/logs?after=` | Vòng đệm log gần nhất kèm nguyên vẹn các trường `extra`. **Cần xác thực** |
| `GET` | `/logs` | Trang xem log trực tiếp. Bản thân trang không mang dữ liệu, nó hỏi API key rồi tự poll |

### Xem log chi tiết

Console của Fly làm phẳng mỗi bản ghi thành một dòng, nên mất đúng phần cần nhìn:
các trường `extra` như `peak_level`, `audio_bytes`, `confidence`, `tts_audio_bytes`.
Mở `https://<host>/logs`, dán API key, trang sẽ hiện các trường đó cạnh thông điệp,
ẩn sẵn access log lặp và các đường thăm dò, và có nút lọc riêng cho luồng thoại.

Vòng đệm nằm trong bộ nhớ tiến trình (`LOG_TAIL_SIZE`, mặc định 500 bản ghi) — là
đuôi log trực tiếp, không phải nơi lưu trữ. Thứ cần sống qua lần khởi động lại vẫn
phải đi qua `fly logs` hoặc Grafana.

Ví dụ — giá trị bị kẹp chứ không bị từ chối:

```bash
curl -X POST localhost:8000/api/v1/devices/living_room_light/command \
     -H 'content-type: application/json' \
     -d '{"capability":"brightness","value":500}'
```
```json
{
  "plan": {
    "accepted": [{"device_id":"living_room_light","capability":"brightness",
                  "value":100,"notes":["Đèn phòng khách: brightness lowered from 500 to the maximum 100"]}],
    "rejected": []
  },
  "dispatched": 1
}
```

---

## 6. Hợp đồng MQTT

| Topic | Chiều | Payload |
|---|---|---|
| `home/<room>/<device>/set` | server → thiết bị | `{"id","plan_id","ts","device_id","set":{...},"delay_s"}` |
| `home/<room>/<device>/set/<cap>` | server → thiết bị | giá trị trần (cho firmware đơn giản) |
| `home/<room>/<device>/state` | thiết bị → server | `{"state":{...}}` hoặc `{...}` |
| `home/<room>/<device>/state/<cap>` | thiết bị → server | giá trị trần |
| `home/<room>/<device>/availability` | thiết bị → server | `online` / `offline`, **retained**, nên đặt làm LWT |
| `home/server/status` | server | `online` / `offline`, retained, LWT của server |

Nhiều thuộc tính của cùng một thiết bị luôn được gộp vào **một** message:
`{"set":{"power":"on","brightness":70}}` — đèn nhận một thay đổi nguyên tử, không
phải hai message đua nhau.

Chọn kiểu payload cho từng thiết bị trong `home.yaml`:

```yaml
mqtt:
  payload_style: value     # mặc định là "json"
```

---

## 7. Cấu hình

### `config/home.yaml` — mô tả căn hộ
Nguồn sự thật duy nhất. File này sinh ra: danh mục thiết bị trong prompt, bảng
topic MQTT, luật kiểm tra giá trị, dữ liệu dashboard. **Thêm thiết bị là sửa YAML,
không sửa code.**

```yaml
devices:
  - id: living_room_light
    name: "Đèn phòng khách"
    type: light
    room: living_room
    aliases: ["đèn phòng khách", "đèn khách"]
    capabilities:
      power:      { kind: enum,   values: ["on", "off"], default: "off" }
      brightness: { kind: number, minimum: 0, maximum: 100, step: 1, unit: "%" }
```

Kiểu thuộc tính: `enum`, `number`, `boolean`, `string`.
Cờ: `read_only` (cảm biến), `sensitive` (bắt buộc xác nhận).

Ngữ cảnh (`scenes`) là gói lệnh có tên — "chế độ ngủ", "ra ngoài" — và vẫn phải đi
qua validator như mọi lệnh khác.

### Biến môi trường
Xem [`.env.example`](.env.example) để biết đầy đủ. Những biến quan trọng nhất:

| Biến | Mặc định | Ghi chú |
|---|---|---|
| `APP_ENV` | `dev` | `prod` bắt buộc có `API_KEY` và `REDIS_URL`, cấm `DEBUG` |
| `API_KEY` | rỗng | Rỗng = tắt xác thực (chỉ cho dev) |
| `DEVICE_TOKENS` | `{}` | JSON `{"esp32_living":"token"}` — thu hồi từng thiết bị |
| `REDIS_URL` | rỗng | Rỗng = bộ nhớ tiến trình, chỉ hợp cho 1 worker |
| `MQTT_ENABLED` | `true` | `false` = transport loopback, lệnh tự phản hồi về state |
| `STT/TTS/LLM_PROVIDER` | `mock` | `google` để dùng Cloud STT / Cloud TTS / Gemini |
| `SILENCE_TIMEOUT_MS` | `900` | Ngưỡng im lặng để kết thúc câu nói |

`Settings` **từ chối khởi động** nếu cấu hình production không an toàn — thiếu
`API_KEY`, thiếu Redis, hoặc bật `DEBUG`. Hỏng sớm tốt hơn hỏng âm thầm.

---

## 8. Bật các provider Google

```bash
.venv/Scripts/python.exe -m pip install -r requirements-ai.txt
```

```dotenv
STT_PROVIDER=google
TTS_PROVIDER=google
LLM_PROVIDER=google

GOOGLE_PROJECT_ID=my-project
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/google.json
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-flash-latest
TTS_VOICE=vi-VN-Neural2-A
```

- **STT** — Cloud Speech-to-Text v2 streaming, một gRPC stream hai chiều cho mỗi
  câu nói, có bật `voice_activity_events` để kết thúc lượt sớm hơn.
- **TTS** — Cloud Text-to-Speech, tổng hợp **theo từng mệnh đề** chứ không chờ cả
  câu trả lời. LINEAR16 trả về có header RIFF, server bóc header trước khi gửi cho
  ESP32.
- **LLM** — Gemini qua `google-genai`, decode có ràng buộc JSON schema.

Kiểm tra credentials bằng một lệnh — công cụ gọi thật từng dịch vụ và dịch lỗi
của Google sang đúng việc cần làm:

```bash
.venv/Scripts/python.exe tools/check_providers.py --all
```

Hai điều dễ vấp:

* **Gemini API key không dùng được cho Cloud STT/TTS.** Đó là hai hệ xác thực khác
  nhau: Gemini nhận API key, còn Cloud Speech-to-Text và Text-to-Speech chỉ nhận
  OAuth2 / service account (`GOOGLE_APPLICATION_CREDENTIALS`).
* Nếu key báo `API_KEY_SERVICE_BLOCKED`, vào Console > Credentials > mở key >
  *API restrictions* và cho phép *Generative Language API*, hoặc tạo key mới tại
  <https://aistudio.google.com/apikey>.

Mỗi tầng chọn provider độc lập: có thể chạy STT Google + LLM mock khi đang debug.
Nếu provider khởi tạo lỗi, ngoài production sẽ tự rơi về mock và ghi log; trong
production thì **từ chối khởi động**. Luôn kiểm tra `GET /api/v1/system/info` →
trường `providers` để biết tầng nào đang chạy thật, thay vì tin vào `.env`.

> Mock không phải đồ trang trí: `MockLanguageModel` là bộ so khớp ý định chạy trên
> chính catalogue thật, nên toàn bộ đường ống (WS → STT → suy luận → validator →
> MQTT → TTS) demo và test được mà không cần một API key nào.

---

## 9. Vận hành

### Độ trễ
`assistant.final` kèm `latency_ms`: `llm_first_token`, `first_audio`, `total`.
Mỗi lượt cũng được ghi log kèm `plan` và `latency_ms` — đủ để dựng dashboard p95
mà không cần thêm instrumentation.

### Scale
Một tiến trình, một worker: phiên WebSocket và kết nối MQTT là trạng thái theo
tiến trình. Scale ngang bằng replica sau load balancer có sticky session, cùng
chia sẻ một Redis. `Settings` đã chặn cấu hình prod dùng bộ nhớ tiến trình.

### Khi hạ tầng hỏng
| Sự cố | Hành vi |
|---|---|
| MQTT mất kết nối | Supervisor tự kết nối lại (backoff + jitter). Lệnh trong lúc mất kết nối **bị từ chối, không xếp hàng** — và trợ lý nói rõ điều đó |
| Redis không truy cập được | Dev: rơi về bộ nhớ tiến trình. Prod: từ chối khởi động |
| LLM lỗi giữa chừng | Giữ phần `speech` đã sinh được; nếu chưa có gì thì nói câu xin lỗi. **Không có lệnh nào được gửi** |
| Model trả JSON hỏng | Vẫn nói được phần `speech` đã bóc; `commands` bị bỏ |
| Thiết bị gửi thuộc tính lạ | Bị loại khỏi state, không lọt vào ngữ cảnh của model |

---

## 10. Triển khai lên Fly.io

```bash
cd server
fly deploy --build-arg BUILD_SHA=$(git rev-parse --short HEAD)
```

`BUILD_SHA` để `GET /api/v1/system/info` nói được chính xác commit nào đang chạy —
không có nó thì không cách gì biết bản trên mạng có phải bản bạn vừa sửa hay không.

### Secrets — không bao giờ nằm trong `fly.toml`

```bash
fly secrets set API_KEY=$(openssl rand -hex 24)        # bắt buộc
fly secrets set GEMINI_API_KEY=...                     # nếu LLM_PROVIDER=google
fly secrets set GOOGLE_PROJECT_ID=...
fly secrets set GOOGLE_CREDENTIALS_JSON="$(cat credentials/service-account.json)"
fly secrets set REDIS_URL=redis://default:...@...      # nếu cần state bền
```

`GOOGLE_CREDENTIALS_JSON` tồn tại vì Fly chỉ cấp secret dưới dạng biến môi trường,
trong khi thư viện Google chỉ đọc credentials từ **đường dẫn file**. Server tự ghi
nội dung đó ra file tạm, quyền chỉ chủ sở hữu đọc được, trước khi dựng client.

### Server công khai bắt buộc có xác thực

Với `APP_ENV` khác `dev`, **không có `API_KEY` thì server từ chối khởi động**. Đây
là chủ ý: URL triển khai ai biết cũng gọi được, mà server này đóng/mở thiết bị
thật trong nhà. Muốn chạy demo mở thì phải nói rõ:

```bash
fly secrets set ALLOW_ANONYMOUS=true
```

Lúc đó mỗi lần khởi động vẫn ghi một dòng cảnh báo vào log.

### CORS

`CORS_ORIGINS="*"` khiến mọi trang web gọi được API này. Server không gửi
`Access-Control-Allow-Credentials` khi origin là `*` — vừa đúng chuẩn CORS, vừa
tránh việc một trang bất kỳ người dùng ghé thăm lại điều khiển được căn hộ qua
trình duyệt của họ. Khi đã có domain front-end thật, khai báo cụ thể:

```bash
fly secrets set CORS_ORIGINS=https://app.example.com
```

### Những giới hạn của cấu hình mặc định

| Mặc định trong `fly.toml` | Hệ quả |
|---|---|
| `REDIS_URL` trống | State và hội thoại nằm trong tiến trình, **mất sạch mỗi lần deploy**, và không chia sẻ được giữa nhiều máy |
| `MQTT_ENABLED=false` | Transport loopback: lệnh tự phản hồi, không có thiết bị thật nào nhận |
| `*_PROVIDER=mock` | Không gọi Google, không tốn tiền, nhưng cũng không nhận dạng được giọng nói thật |

Để nối MQTT thật, deploy broker rồi trỏ vào DNS nội bộ của Fly:

```bash
fly secrets set MQTT_ENABLED=true MQTT_HOST=smart-apartment-mosquitto.internal
```

Một worker cho mỗi máy: phiên WebSocket và kết nối MQTT là trạng thái theo tiến
trình. Muốn chạy nhiều máy thì **bắt buộc** có `REDIS_URL` chung, và load balancer
phải sticky theo phiên.

---

## 11. CI/CD

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) chạy mỗi lần push lên
`main` và mỗi pull request.

```
push/PR ──► server: ruff + pytest + kiểm tra .env.example
        ──► mobile: typecheck + verify + bundle Android
                        │
                 cả hai xanh
                        │
            push lên main ──► deploy Fly.io ──► kiểm tra bản vừa deploy
```

### Chuẩn bị một lần

```bash
fly tokens create deploy -x 8760h
```

Thêm vào GitHub → **Settings → Secrets and variables → Actions**:

| Secret | Bắt buộc | Dùng để |
|---|---|---|
| `FLY_API_TOKEN` | ✅ | deploy |

Bước kiểm tra sau deploy không cần secret nào: `/readyz` công bố mã build cho cả
caller ẩn danh. Một kiểm tra chỉ chạy khi có credential là một kiểm tra sẽ âm
thầm ngừng chạy.

### Những quyết định trong workflow

**Chỉ deploy khi `server/` thay đổi.** Một lần deploy thừa vẫn khởi động lại máy,
và với state nằm trong bộ nhớ thì toàn bộ trạng thái thiết bị cùng lịch sử hội
thoại bay sạch. Sửa app mobile không có lý do gì làm căn hộ quên mất đèn đang bật.
Cần deploy lại bằng tay thì chạy workflow thủ công với `force_deploy`.

**Kiểm tra sau khi deploy, không chỉ sau khi build.** Bước cuối gọi `/healthz` rồi
đối chiếu trường `build` trong `/api/v1/system/info` với commit vừa push. Deploy
"thành công" nhưng máy vẫn chạy bản cũ là chuyện có thật — và không có mã build
thì không ai phát hiện ra.

**Kiểm tra `.env.example`.** Đó là file người mới clone về sẽ copy nguyên xi.
Bước này từng bắt được một lỗi thật: `CORS_ORIGINS=*` làm server chết ngay lúc
khởi động, vì pydantic-settings `json.loads` trường kiểu list trước khi validator
kịp chạy.

**PR không bao giờ deploy.** Workflow chỉ deploy khi sự kiện là `push` lên `main`.

### Lần deploy đầu sẽ hỏng nếu chưa đặt secret

Sau bản vá chặn triển khai mở, server **từ chối khởi động** khi `APP_ENV` khác
`dev` mà không có `API_KEY`. Health check của Fly sẽ fail và deploy bị coi là
thất bại. Đặt secret trước:

```bash
fly secrets set API_KEY=$(openssl rand -hex 24)
```

---

## 12. Phát triển

```bash
make test       # hoặc: .venv/Scripts/python.exe -m pytest -q
make lint
make sim        # giả lập ESP32
make apartment  # giả lập toàn bộ thiết bị trên MQTT thật
```

Bộ test chạy trên **chính graph ứng dụng thật** — validator thật, orchestrator thật,
giao thức WebSocket thật — chỉ thay store bằng bộ nhớ và MQTT bằng loopback. Không
có gì mock phần đang được test; chỉ bỏ phần mạng.

```
151 passed
```

Trọng tâm test: bộ validator (giá trị sai, kẹp giá trị, giờ yên tĩnh, xác nhận,
hạn mức), hành vi khi model sai (JSON hỏng, provider chết, thiết bị bịa ra), và
toàn bộ vòng đời WebSocket.

### Cấu trúc thư mục

```
app/
  main.py            ASGI app, lifespan, middleware, ánh xạ lỗi
  config.py          Settings (có kiểm tra tư thế production)
  container.py       Composition root
  core/              audio, json_stream, eventbus, security, ratelimit, errors
  domain/            home, state, commands, conversation  (mô hình thuần)
  storage/           backend Redis / bộ nhớ
  services/          device_state, conversation, orchestrator, session
  safety/            rules (YAML) + validator      ← lớp bảo vệ
  mqtt/              topics, client (có reconnect), bridge
  ai/                base + prompts + schemas + {stt,tts,llm}/{google,mock}
  api/               deps, schemas, routes_*, ws_protocol, ws_voice
config/              home.yaml, safety.yaml
tools/               simulate_client.py, fake_device.py, check_providers.py
tests/               124 test
```

---

## 13. Ghi chú cho firmware ESP32

1. **Định dạng âm thanh**: I2S → PCM 16-bit, 16 kHz, mono. Gửi thẳng khung nhị
   phân, không bọc WAV. Khung 20 ms (640 byte) là kích thước hợp lý.
2. **Wake word xử lý tại chỗ**: chỉ mở WebSocket và bắt đầu gửi sau khi phát hiện
   từ khoá — tiết kiệm băng thông và chi phí nhận dạng.
3. **Kết thúc câu nói**: cứ gửi liên tục và để server tự ngắt, hoặc gửi `audio.end`
   nếu firmware có VAD riêng.
4. **Phát lại**: `tts.start` cho biết tần số lấy mẫu; các khung nhị phân sau đó đưa
   thẳng vào DAC cho tới khi nhận `tts.end`.
5. **Xác thực**: nhiều stack WebSocket trên ESP32 không gắn được header khi upgrade,
   nên `?token=...&device_id=...` là đường chính thức, không phải đường vòng.
6. **MQTT**: đặt last-will trên topic `availability` với payload `offline`, retained.
   Dashboard sẽ biết thiết bị mất điện mà không cần poll.
7. **Bản hiện thực**: `EnvMonitor-SmarHome/code_ass2/AudioHandler.cpp` (board ESP32-S3) đã
   làm đúng hợp đồng trên — `voiceBegin()` mở phiên, `voiceLoop()` đẩy khung 32 ms
   trong lúc người dùng còn đang nói. Cần thư viện `WebSockets` của Markus Sattler
   (arduinoWebSockets); thiếu nó firmware vẫn biên dịch được và chỉ báo trên Serial.
   Board này xin `reply_encoding: "none"` vì loa Bluetooth A2DP ở board riêng lo
   phần phát tiếng.
8. **Mic hiện không cắm vào board S3.** Mic INMP441 đang nằm chung board ESP32 classic
   với loa A2DP (`EnvMonitor-SmarHome/esp32_connect_loa/`), và board đó gửi tiếng nói
   qua `POST /upload-audio` chứ không qua `/ws/voice` — A2DP đã ăn gần hết RAM, không
   còn chỗ cho thư viện WebSocket. Đường `/ws/voice` ở trên vẫn đúng và vẫn được app
   mobile dùng.
