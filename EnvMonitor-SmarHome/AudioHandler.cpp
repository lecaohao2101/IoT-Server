#include "AudioHandler.h"
#include "Config.h"
#include "Controls.h"
#include <driver/i2s.h>
#include <WiFi.h>

// Tiếng nói đi theo luồng: mỗi 32 ms mic lại đẩy một khung lên server trong lúc
// người dùng vẫn còn đang nói. Server tự nhận ra khoảng lặng và chốt câu, nên
// firmware không còn phải đoán trước "thu bao nhiêu giây".
//
// Hai bộ đệm tĩnh dưới đây là toàn bộ RAM mà đường tiếng nói dùng -- 3 KB cố
// định, nói 2 giây hay 2 phút cũng vậy. Bản cũ cấp phát cả câu nói vào heap
// (5 giây = 160 KB) rồi mở thêm một kết nối TLS thứ hai trong lúc vẫn đang giữ
// khối đó, nên câu càng dài càng dễ hết heap giữa chừng.
static constexpr int FRAME_SAMPLES = 512;  // 512 mẫu @16 kHz = 32 ms = 1024 byte
static int32_t i2s_raw[FRAME_SAMPLES];
static int16_t pcm_frame[FRAME_SAMPLES];

static int sound_level = 0;

void setupINMP441() {
  i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = 16000,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = i2s_comm_format_t(I2S_COMM_FORMAT_STAND_I2S),
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 4,
    .dma_buf_len = 512,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
  };

  i2s_pin_config_t pin_config = {
    .bck_io_num = PIN_I2S_SCK,
    .ws_io_num = PIN_I2S_WS,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = PIN_I2S_SD
  };

  i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
  i2s_set_pin(I2S_PORT, &pin_config);
}

int readINMP441SoundLevel() {
  return sound_level;
}

// Đọc một khung từ DMA của mic. Không chặn: hết dữ liệu thì trả 0 ngay để loop()
// còn đi quét nút và bơm MQTT. Bản cũ dùng portMAX_DELAY và khoá cứng loop()
// suốt cả câu nói.
static int captureFrame() {
  size_t bytes_read = 0;
  if (i2s_read(I2S_PORT, i2s_raw, sizeof(i2s_raw), &bytes_read, 0) != ESP_OK) return 0;

  int count = bytes_read / sizeof(int32_t);
  if (count <= 0) return 0;

  int64_t sum = 0;
  for (int i = 0; i < count; i++) {
    int32_t val = i2s_raw[i] >> 14;
    pcm_frame[i] = (int16_t)val;
    sum += abs(val);
  }
  sound_level = map(constrain((int)(sum / count), 0, 2000), 0, 2000, 0, 100);
  return count;
}

#if __has_include(<WebSocketsClient.h>)
#include <WebSocketsClient.h>

static WebSocketsClient ws;
static bool ws_ready = false;        // server đã trả session.ready
static bool talking = false;         // đang mở một câu nói
static bool force_talk = false;      // nút bấm ép mở câu, bỏ qua ngưỡng âm lượng
static int  gate_blocks = 0;
static unsigned long talk_started_ms = 0;
static unsigned long last_voice_ms = 0;
static unsigned long last_ping_ms = 0;

bool voiceIsConnected() { return ws_ready; }
bool voiceIsTalking()   { return talking; }
void voiceRequestTalk() { force_talk = true; }

// ----------------------------------------------------------------- JSON tối giản
// Cùng lối đọc bằng indexOf như phần còn lại của firmware: đủ cho các khung điều
// khiển phẳng của /ws/voice và không kéo thêm thư viện nào vào bộ nhớ.
static String jsonStr(const String& src, const char* key) {
  String needle = String("\"") + key + "\":\"";
  int at = src.indexOf(needle);
  if (at == -1) return String();
  int from = at + needle.length();
  int end = src.indexOf('"', from);
  return (end == -1) ? String() : src.substring(from, end);
}

static int jsonInt(const String& src, const char* key, int fallback) {
  String needle = String("\"") + key + "\":";
  int at = src.indexOf(needle);
  if (at == -1) return fallback;
  return src.substring(at + needle.length()).toInt();
}

// -------------------------------------------------------------- xử lý khung đến
static void applyStateChanged(const String& msg) {
  String deviceId = jsonStr(msg, "device_id");
  if (deviceId.isEmpty()) return;

  String power = jsonStr(msg, "power");
  int vane = jsonInt(msg, "vane_angle", -1);
  if (power.isEmpty() && vane < 0) return;

  Serial.printf("[VOICE-STATE] %s -> %s (góc: %d)\n", deviceId.c_str(),
                power.isEmpty() ? "-" : power.c_str(), vane);
  setDeviceActuator(deviceId, power == "on", vane);
}

static void handleServerMessage(const String& msg) {
  String type = jsonStr(msg, "type");

  if (type == "session.ready") {
    ws_ready = true;
    Serial.printf("[VOICE-WS] Phiên sẵn sàng (%s). Cứ nói tự nhiên, server tự cắt câu.\n",
                  jsonStr(msg, "session_id").c_str());
  } else if (type == "stt.partial") {
    Serial.printf("[VOICE-STT] ... %s\n", jsonStr(msg, "text").c_str());
  } else if (type == "stt.final") {
    Serial.printf("[VOICE-STT] Bạn đã nói: \"%s\"\n", jsonStr(msg, "text").c_str());
  } else if (type == "assistant.final") {
    Serial.printf("[VOICE-AI] Trợ lý trả lời: \"%s\"\n", jsonStr(msg, "text").c_str());
    digitalWrite(PIN_BUZZER, HIGH); delay(20); digitalWrite(PIN_BUZZER, LOW);
  } else if (type == "state.changed") {
    applyStateChanged(msg);
  } else if (type == "error" || type == "notice") {
    Serial.printf("[VOICE-WS] %s: %s\n", jsonStr(msg, "code").c_str(),
                  jsonStr(msg, "message").c_str());
  }
}

static void sendHello() {
  String hello = String("{\"type\":\"hello\",\"room\":\"") + VOICE_ROOM +
                 "\",\"device_id\":\"" + VOICE_DEVICE_ID +
                 "\",\"sample_rate\":16000,\"channels\":1,\"codec\":\"pcm16\"," +
                 // Loa ngoài A2DP lo phần phát tiếng, board mic không cần nhận
                 // PCM trả về -- tiết kiệm cả RAM lẫn băng thông.
                 "\"reply_encoding\":\"none\"}";
  ws.sendTXT(hello);
}

static void onWsEvent(WStype_t type, uint8_t* payload, size_t length) {
  (void)length;
  switch (type) {
    case WStype_CONNECTED:
      Serial.printf("[VOICE-WS] Đã kết nối %s | Heap trống: %u byte\n",
                    VOICE_WS_HOST, (unsigned)ESP.getFreeHeap());
      sendHello();
      break;

    case WStype_DISCONNECTED: {
      // Bắt tay hỏng thì cũng chỉ hiện ra ở đây, nên nói rõ chỗ cần xem lại thay
      // vì im lặng thử lại mỗi 5 giây.
      static int failed_handshakes = 0;
      if (ws_ready) {
        failed_handshakes = 0;
        Serial.println("[VOICE-WS] Mất kết nối, sẽ tự thử lại sau 5 giây...");
      } else if (++failed_handshakes == 3) {
        Serial.println("[VOICE-WS] Không bắt tay được. Kiểm tra VOICE_WS_TOKEN có khớp API_KEY");
        Serial.println("[VOICE-WS] của server không, và VOICE_WS_HOST/VOICE_WS_PORT đã đúng chưa.");
      }
      ws_ready = false;
      talking = false;
      gate_blocks = 0;
      break;
    }

    case WStype_TEXT:
      // Thư viện luôn kết thúc payload text bằng NUL.
      handleServerMessage(String((char*)payload));
      break;

    case WStype_ERROR:
      Serial.println("[VOICE-WS] Lỗi socket.");
      break;

    default:
      break;
  }
}

void voiceBegin() {
  String path = String(VOICE_WS_PATH) + "?device_id=" + VOICE_DEVICE_ID + "&room=" + VOICE_ROOM;
  if (strlen(VOICE_WS_TOKEN) > 0) path += String("&token=") + VOICE_WS_TOKEN;

  ws.onEvent(onWsEvent);
#if VOICE_WS_TLS
  ws.beginSSL(VOICE_WS_HOST, VOICE_WS_PORT, path.c_str());
#else
  ws.begin(VOICE_WS_HOST, VOICE_WS_PORT, path.c_str());
#endif
  ws.setReconnectInterval(5000);
  ws.enableHeartbeat(15000, 3000, 2);

  Serial.printf("[VOICE-WS] Đang mở %s%s:%d%s\n", VOICE_WS_TLS ? "wss://" : "ws://",
                VOICE_WS_HOST, VOICE_WS_PORT, path.c_str());
}

static bool pumpFrame() {
  int samples = captureFrame();
  if (samples <= 0) return false;
  if (!ws_ready || WiFi.status() != WL_CONNECTED) return true;

  bool loud = sound_level >= VOICE_GATE_LEVEL;
  unsigned long now = millis();

  if (!talking) {
    // Cổng âm thanh: chỉ truyền khi thật sự có người nói. Không có nó, mic đẩy
    // 32 KB/s suốt ngày và mỗi phút im lặng vẫn bị Google STT tính tiền.
    gate_blocks = loud ? gate_blocks + 1 : 0;
    if (!force_talk && gate_blocks < VOICE_GATE_BLOCKS) return true;

    talking = true;
    force_talk = false;
    gate_blocks = 0;
    talk_started_ms = now;
    last_voice_ms = now;
    ws.sendTXT("{\"type\":\"audio.start\"}");
    Serial.printf("[VOICE-MIC] >>> Bắt đầu truyền (mức âm thanh %d) <<<\n", sound_level);
  }

  ws.sendBIN((uint8_t*)pcm_frame, samples * sizeof(int16_t));
  if (loud) last_voice_ms = now;

  bool silent_enough = (now - last_voice_ms) >= VOICE_SILENCE_MS;
  bool too_long = (now - talk_started_ms) >= VOICE_MAX_UTTER_MS;
  if (silent_enough || too_long) {
    ws.sendTXT("{\"type\":\"audio.end\"}");
    talking = false;
    Serial.printf("[VOICE-MIC] <<< Chốt câu sau %lu ms%s, đang chờ trợ lý trả lời\n",
                  now - talk_started_ms, too_long ? " (chạm trần thời lượng)" : "");
  }
  return true;
}

void voiceLoop() {
  ws.loop();

  // Rút tối đa 4 khung mỗi vòng để bắt kịp DMA sau những đoạn loop() bị chặn lâu
  // (ví dụ một POST HTTPS trạng thái lúc bấm nút). DMA chỉ giữ được 128 ms tiếng.
  for (int i = 0; i < 4 && pumpFrame(); i++) {}

  // Server đóng phiên sau 120 giây không nhận được gì ở tầng ứng dụng, và ping
  // của tầng WebSocket không tính. Giữ phiên sống để câu sau không phải bắt tay lại.
  unsigned long now = millis();
  if (ws_ready && (now - last_ping_ms) >= 30000) {
    last_ping_ms = now;
    ws.sendTXT("{\"type\":\"ping\"}");
  }
}

#else

// Thiếu thư viện thì cả đường tiếng nói biến mất khỏi firmware. Trước đây chuyện
// đó chỉ lộ ra trên Serial lúc chạy, nên đọc như "board không gọi server".
// Cảnh báo ngay lúc biên dịch để không bao giờ nạp nhầm một bản firmware câm.
#warning "Khong tim thay WebSocketsClient.h -- tro ly giong noi bi loai khoi firmware. Cai thu vien 'WebSockets' (Markus Sattler)."

void voiceBegin() {
  Serial.println("[VOICE] ===> CANH BAO: Firmware nay KHONG co phan tro ly giong noi! <===");
  Serial.println("[VOICE] Thieu thu vien 'WebSockets' cua Markus Sattler luc bien dich.");
}

void voiceLoop() {
  // Vẫn rút mic để mức âm thanh trên LCD và telemetry tiếp tục cập nhật.
  captureFrame();
}

void voiceRequestTalk() {
  Serial.println("[VOICE] Chưa có thư viện WebSockets -- bỏ qua yêu cầu nói.");
}

bool voiceIsConnected() { return false; }
bool voiceIsTalking()   { return false; }

#endif
