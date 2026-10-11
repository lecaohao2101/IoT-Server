#include "MicUplink.h"

#include <WiFi.h>
#include <driver/i2s.h>

#define MIC_I2S_PORT I2S_NUM_0

// 256 mẫu @16 kHz = 16 ms = 512 byte PCM16.
//
// Board ESP32-S3 dùng khung 512 mẫu, ở đây cố tình nhỏ hơn một nửa: mỗi mẫu I2S
// chiếm 4 byte lúc đọc thô, nên khung 512 kéo theo 3 KB bộ đệm tĩnh cộng 8 KB
// đệm DMA. Trên board này, nơi A2DP đã ăn gần hết RAM, 16 ms độ trễ thêm rẻ hơn
// nhiều so với vài KB nhớ.
static constexpr int FRAME_SAMPLES = 256;
static int32_t i2s_raw[FRAME_SAMPLES];
static int16_t pcm_frame[FRAME_SAMPLES];

static int  sound_level = 0;
static bool driver_ready = false;

// Đọc một khung từ DMA. `wait_ms` = 0 nghĩa là không chặn: hết dữ liệu thì trả 0
// ngay để loop() còn đi phục vụ web server và bơm ring buffer của loa.
static int captureFrame(TickType_t wait_ms) {
  if (!driver_ready) return 0;

  size_t bytes_read = 0;
  if (i2s_read(MIC_I2S_PORT, i2s_raw, sizeof(i2s_raw), &bytes_read, pdMS_TO_TICKS(wait_ms)) != ESP_OK) {
    return 0;
  }

  int count = bytes_read / sizeof(int32_t);
  if (count <= 0) return 0;

  int64_t sum = 0;
  for (int i = 0; i < count; i++) {
    // INMP441 trả mẫu 24-bit căn trái trong khung 32-bit. Dịch 14 bit là đúng
    // hệ số mà firmware ESP32-S3 đang dùng -- giữ nguyên để mức âm thanh của hai
    // board so sánh được với nhau.
    int32_t val = i2s_raw[i] >> 14;
    pcm_frame[i] = (int16_t)val;
    sum += abs(val);
  }
  sound_level = map(constrain((int)(sum / count), 0, 2000), 0, 2000, 0, 100);
  return count;
}

// Cài driver I2S. Chỉ giữ trong lúc thật sự đọc mic -- xem ghi chú ở MicUplink.h.
static bool micInstall() {
  if (driver_ready) return true;

  i2s_config_t cfg = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = MIC_SAMPLE_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = i2s_comm_format_t(I2S_COMM_FORMAT_STAND_I2S),
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    // 4 x 256 mẫu = 64 ms tiếng nằm sẵn trong DMA, tốn 4 KB. Đủ để một lần
    // client.write() bị nghẽn mạng không làm thủng đoạn ghi, mà vẫn còn chỗ cho
    // socket -- hai thứ này tranh nhau cùng một vùng nhớ.
    .dma_buf_count = 4,
    .dma_buf_len = FRAME_SAMPLES,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
  };

  i2s_pin_config_t pins = {
    .bck_io_num = MIC_PIN_SCK,
    .ws_io_num = MIC_PIN_WS,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = MIC_PIN_SD
  };

  esp_err_t err = i2s_driver_install(MIC_I2S_PORT, &cfg, 0, NULL);
  if (err != ESP_OK) {
    Serial.printf("[MIC] Khong cai duoc driver I2S (ma loi %d). Heap trong: %u byte.\n",
                  (int)err, (unsigned)ESP.getFreeHeap());
    return false;
  }

  err = i2s_set_pin(MIC_I2S_PORT, &pins);
  if (err != ESP_OK) {
    Serial.printf("[MIC] Khong gan duoc chan I2S (ma loi %d).\n", (int)err);
    i2s_driver_uninstall(MIC_I2S_PORT);
    return false;
  }

  driver_ready = true;
  return true;
}

static void micUninstall() {
  if (!driver_ready) return;
  i2s_driver_uninstall(MIC_I2S_PORT);
  driver_ready = false;
}

int micLevel() { return sound_level; }

// ------------------------------------------------------------------ self-test
struct MicSelfTest {
  uint32_t frames;
  uint32_t samples;
  int16_t  min_sample;
  int16_t  max_sample;
  uint32_t avg_abs;
  bool     all_zero;
};

static MicSelfTest micSelfTest(uint32_t duration_ms) {
  MicSelfTest r = {0, 0, INT16_MAX, INT16_MIN, 0, true};
  if (!driver_ready) return r;

  // Khung đầu sau khi cài driver hay dính rác của lần khởi tạo. Bỏ vài khung cho
  // sạch, nếu không "mic hỏng" và "mic vừa mới bật" trông giống hệt nhau.
  for (int i = 0; i < 4; i++) captureFrame(60);

  uint64_t sum_abs = 0;
  unsigned long t0 = millis();
  while (millis() - t0 < duration_ms) {
    int n = captureFrame(60);
    if (n <= 0) continue;

    r.frames++;
    r.samples += n;
    for (int i = 0; i < n; i++) {
      int16_t s = pcm_frame[i];
      if (s < r.min_sample) r.min_sample = s;
      if (s > r.max_sample) r.max_sample = s;
      if (s != 0) r.all_zero = false;
      sum_abs += abs(s);
    }
  }

  if (r.samples > 0) r.avg_abs = (uint32_t)(sum_abs / r.samples);
  if (r.frames == 0) { r.min_sample = 0; r.max_sample = 0; }
  return r;
}

static void micPrintSelfTest(const MicSelfTest& r) {
  Serial.println("---------------- TU KIEM TRA MIC INMP441 ----------------");
  Serial.printf("[MIC] Khung doc duoc: %u | Mau: %u\n", (unsigned)r.frames, (unsigned)r.samples);
  Serial.printf("[MIC] Bien do: min=%d max=%d trung binh=%u\n",
                r.min_sample, r.max_sample, (unsigned)r.avg_abs);

  // Mỗi nhánh dưới đây là một lỗi phần cứng khác hẳn nhau. Nói thẳng ra việc cần
  // làm, thay vì in một con số rồi để người đọc tự đoán.
  if (r.frames == 0) {
    Serial.println("[MIC] ==> KHONG DOC DUOC KHUNG NAO.");
    Serial.printf("[MIC] ==> Kiem tra SCK (D%d) va WS (D%d). Thieu xung clock thi DMA khong bao gio day.\n",
                  MIC_PIN_SCK, MIC_PIN_WS);
  } else if (r.all_zero) {
    Serial.println("[MIC] ==> CO XUNG CLOCK NHUNG MOI MAU DEU BANG 0.");
    Serial.printf("[MIC] ==> Kiem tra day SD (D%d), va chan L/R cua mic PHAI noi GND.\n", MIC_PIN_SD);
    Serial.println("[MIC] ==> Noi L/R vao 3V3 la mic phat o kenh phai, cau hinh nay chi doc kenh trai.");
  } else if (r.avg_abs < 15) {
    Serial.println("[MIC] ==> Co tin hieu nhung rat nho. Day la muc cua phong yen tinh.");
    Serial.println("[MIC] ==> Vo tay sat mic roi khoi dong lai de doi chung truoc khi ket luan.");
  } else {
    Serial.println("[MIC] ==> MIC HOAT DONG BINH THUONG.");
  }
  Serial.println("---------------------------------------------------------");
}

bool micBegin() {
  size_t heap_before = ESP.getFreeHeap();

  if (!micInstall()) return false;
  Serial.printf("[MIC] I2S san sang | SD=D%d SCK=D%d WS=D%d\n",
                MIC_PIN_SD, MIC_PIN_SCK, MIC_PIN_WS);

  MicSelfTest r = micSelfTest(1500);
  micPrintSelfTest(r);

  // Nhả driver ngay. Phần nhớ này phải để dành cho A2DP và cho socket -- mic chỉ
  // cần tới nó trong đúng ba giây của mỗi lần test.
  micUninstall();
  Serial.printf("[MIC] Da nha driver I2S, tra lai bo nho | Heap %u -> %u byte\n",
                (unsigned)heap_before, (unsigned)ESP.getFreeHeap());

  return r.frames > 0 && !r.all_zero;
}

// -------------------------------------------------------------- JSON tối giản
// Cùng lối đọc bằng indexOf như phần còn lại của firmware: đủ cho phản hồi phẳng
// của /upload-audio và không kéo thêm thư viện nào vào bộ nhớ.
static String jsonField(const String& src, const char* key) {
  String needle = String("\"") + key + "\":\"";
  int at = src.indexOf(needle);
  if (at == -1) return String();
  int from = at + needle.length();
  String out;
  for (int i = from; i < (int)src.length(); i++) {
    char c = src[i];
    if (c == '\\' && i + 1 < (int)src.length()) { out += src[++i]; continue; }
    if (c == '"') break;
    out += c;
  }
  return out;
}

// ------------------------------------------------------------------- upload
MicUploadResult micRecordAndUpload(const char* host, int port, uint32_t duration_ms) {
  MicUploadResult res = {false, 0, 0, 0, 0, String(), String(), String()};
  unsigned long started = millis();

  if (WiFi.status() != WL_CONNECTED) {
    res.error = "wifi_down";
    Serial.println("[MIC-UP] Chua co Wi-Fi, bo qua.");
    return res;
  }

  // Content-Length phải biết trước thì mới gửi theo luồng được. Tính đúng theo
  // khung để không bao giờ cắt lẻ một mẫu 16-bit làm lech can le ca doan sau.
  const uint32_t bytes_per_frame = FRAME_SAMPLES * sizeof(int16_t);
  uint32_t frames_total = (uint32_t)((uint64_t)MIC_SAMPLE_RATE * 2ULL * duration_ms / 1000ULL / bytes_per_frame);
  if (frames_total == 0) frames_total = 1;
  const uint32_t content_length = frames_total * bytes_per_frame;

  // Không đụng tới client.setTimeout(): trên core ESP32 tham số này là GIÂY chứ
  // không phải mili-giây như Stream của Arduino, nên một con số "15000" vô hại
  // trông lại hoá ra 15000 giây. Vòng đọc phía dưới tự giữ hạn 20 giây.
  WiFiClient client;
  if (!client.connect(host, port)) {
    size_t heap = ESP.getFreeHeap();
    res.error = "connect_failed";
    if (heap < 20000) {
      Serial.printf("[MIC-UP] Khong mo duoc socket vi HET RAM (%u byte). Mang khong phai thu pham.\n",
                    (unsigned)heap);
    } else {
      Serial.printf("[MIC-UP] Khong ket noi duoc %s:%d\n", host, port);
    }
    return res;
  }

  // Socket mở TRƯỚC rồi mới cấp đệm DMA. Hai thứ này tranh cùng một vùng nhớ, và
  // nếu thiếu thì thà hỏng ở bước mở socket -- lỗi đó nói rõ nguyên nhân hơn hẳn
  // một lần i2s_driver_install thất bại.
  if (!micInstall()) {
    res.error = "mic_install_failed";
    Serial.printf("[MIC-UP] Khong cap duoc dem DMA cho mic. Heap con %u byte.\n",
                  (unsigned)ESP.getFreeHeap());
    client.stop();
    return res;
  }

  // HTTP/1.0 + Connection: close, cùng lý do như luồng audio: không có chunked,
  // và server đóng socket khi hết body nên biết chắc chỗ kết thúc phản hồi.
  String header = String("POST /upload-audio HTTP/1.0\r\n") +
                  "Host: " + String(host) + "\r\n" +
                  "User-Agent: ESP32-A2DP-Mic\r\n" +
                  "Content-Type: application/octet-stream\r\n" +
                  "Content-Length: " + String(content_length) + "\r\n" +
                  "Connection: close\r\n\r\n";
  client.print(header);

  Serial.printf("\n[MIC-UP] Bat dau thu %u ms (%u byte) -> %s:%d/upload-audio\n",
                (unsigned)duration_ms, (unsigned)content_length, host, port);
  Serial.println("[MIC-UP] NOI VAO MIC NGAY BAY GIO...");

  // Đổ thẳng từ DMA ra socket. Không có bộ đệm cả câu nói: 3 giây tiếng là 96 KB,
  // board này không có khoản đó trong khi A2DP đang chạy.
  uint32_t sent = 0;
  int16_t peak = 0;
  static int16_t silence[FRAME_SAMPLES] = {0};

  while (sent < content_length && client.connected()) {
    int n = captureFrame(60);
    const uint8_t* payload;
    uint32_t len;

    if (n > 0) {
      for (int i = 0; i < n; i++) {
        int16_t a = abs(pcm_frame[i]);
        if (a > peak) peak = a;
      }
      payload = (const uint8_t*)pcm_frame;
      len = (uint32_t)n * sizeof(int16_t);
    } else {
      // DMA hụt thì vẫn phải gửi đủ Content-Length, nếu không server treo chờ
      // phần thân còn thiếu cho tới khi hết giờ. Chèn im lặng là cách trung thực
      // nhất: đoạn đó đúng là không có tiếng nào đọc được.
      payload = (const uint8_t*)silence;
      len = bytes_per_frame;
    }

    if (sent + len > content_length) len = content_length - sent;
    int written = client.write(payload, len);
    if (written <= 0) break;
    sent += (uint32_t)written;
  }

  res.bytes_sent = sent;
  res.local_peak = peak;
  client.flush();

  // Nhả đệm DMA ngay khi gửi xong. Sau đây là quãng chờ server chạy STT + trợ lý
  // + TTS, có thể vài giây -- không có lý do gì giữ 4 KB trong lúc đó, nhất là
  // khi luồng audio trả lời sắp cần mở lại socket.
  micUninstall();

  Serial.printf("[MIC-UP] Da gui %u/%u byte | Bien do lon nhat tai cho: %d\n",
                (unsigned)sent, (unsigned)content_length, peak);
  if (peak < 100) {
    Serial.println("[MIC-UP] ==> CANH BAO: doan vua gui gan nhu im lang. Server se khong nhan dang duoc gi.");
  }

  // Server còn phải chạy STT + LLM + TTS trước khi trả lời, nên chờ lâu hơn hẳn
  // một request thường.
  String body;
  bool in_body = false;
  unsigned long deadline = millis() + 20000;
  while (millis() < deadline) {
    if (!client.available()) {
      if (!client.connected()) break;
      delay(10);
      continue;
    }
    String line = client.readStringUntil('\n');
    if (!in_body) {
      if (res.http_status == 0 && line.startsWith("HTTP/")) {
        int sp = line.indexOf(' ');
        if (sp > 0) res.http_status = line.substring(sp + 1, sp + 4).toInt();
      }
      String trimmed = line;
      trimmed.trim();
      if (trimmed.length() == 0) in_body = true;
    } else {
      body += line;
    }
  }
  client.stop();

  res.elapsed_ms = millis() - started;

  if (body.isEmpty()) {
    res.error = "no_response";
    Serial.printf("[MIC-UP] Khong nhan duoc phan hoi (HTTP %d) sau %u ms.\n",
                  res.http_status, (unsigned)res.elapsed_ms);
    return res;
  }

  res.transcript = jsonField(body, "transcript");
  res.reply      = jsonField(body, "response");
  res.error      = jsonField(body, "error");
  res.ok         = (body.indexOf("\"success\":true") >= 0);

  Serial.printf("[MIC-UP] HTTP %d sau %u ms\n", res.http_status, (unsigned)res.elapsed_ms);
  if (res.transcript.length() > 0) {
    Serial.printf("[MIC-UP] ==> SERVER NGHE DUOC: \"%s\"\n", res.transcript.c_str());
  } else {
    Serial.println("[MIC-UP] ==> Server khong nhan dang duoc cau nao.");
  }
  if (res.reply.length() > 0) {
    Serial.printf("[MIC-UP] ==> TRO LY TRA LOI: \"%s\"\n", res.reply.c_str());
    Serial.println("[MIC-UP] ==> Cau tra loi nay dang duoc day ra loa Bluetooth qua /api/audio/stream.");
  }
  if (res.error.length() > 0) {
    Serial.printf("[MIC-UP] ==> Server bao loi: %s\n", res.error.c_str());
    if (res.error == "stt_empty") {
      Serial.println("[MIC-UP] ==> Am thanh len toi noi nhung khong ro tieng. Noi to va gan mic hon.");
    } else if (res.error == "stt_failed") {
      Serial.println("[MIC-UP] ==> Duong truyen OK, loi nam o dich vu nhan dang phia server.");
    } else if (res.error == "empty_audio") {
      Serial.println("[MIC-UP] ==> Server nhan duoc qua it byte. Kiem tra lai Content-Length.");
    }
  }
  return res;
}

// ==================== LẮNG NGHE LIÊN TỤC ====================

bool micListenBegin() {
  if (!micInstall()) {
    Serial.println("[MIC] Khong cai duoc I2S cho che do lang nghe.");
    return false;
  }
  Serial.printf("[MIC] Lang nghe lien tuc | nguong mo cau: %d/100 | im %lu ms thi chot\n",
                MIC_GATE_LEVEL, MIC_SILENCE_MS);
  return true;
}

// Trạng thái của một câu nói đang được thu và gửi dở.
static WiFiClient  up_client;
static bool          up_talking = false;
static unsigned long up_started_ms = 0;
static unsigned long up_last_voice_ms = 0;
static uint32_t      up_bytes = 0;
static int16_t       up_peak = 0;
static int           up_gate_frames = 0;

// Một khối chunked: "<do dai he 16>\r\n<du lieu>\r\n".
static bool sendChunk(const uint8_t* payload, size_t len) {
  char head[16];
  int n = snprintf(head, sizeof(head), "%X\r\n", (unsigned)len);
  if (up_client.write((const uint8_t*)head, n) <= 0) return false;
  if (up_client.write(payload, len) <= 0) return false;
  return up_client.write((const uint8_t*)"\r\n", 2) > 0;
}

static bool openUtterance(const char* host, int port) {
  if (WiFi.status() != WL_CONNECTED) return false;

  if (!up_client.connect(host, port, 8000)) {
    Serial.printf("[MIC-UP] Khong mo duoc socket toi %s:%d (heap %u byte).\n",
                  host, port, (unsigned)ESP.getFreeHeap());
    return false;
  }

  // HTTP/1.1 + chunked: không phải khai tổng độ dài trước. Đây chính là thứ cho
  // phép nói dài ngắn tuỳ ý thay vì cố định 3 giây như bản dùng Content-Length.
  String head = String("POST /upload-audio HTTP/1.1\r\n") +
                "Host: " + String(host) + "\r\n" +
                "User-Agent: ESP32-Mic\r\n" +
                "Content-Type: application/octet-stream\r\n" +
                "Transfer-Encoding: chunked\r\n" +
                "Connection: close\r\n\r\n";
  up_client.print(head);

  up_talking = true;
  up_started_ms = millis();
  up_last_voice_ms = up_started_ms;
  up_bytes = 0;
  up_peak = 0;
  Serial.println("\n[MIC-UP] >>> Nghe thay tieng, dang thu va gui... (cu noi tu nhien)");
  return true;
}

static void closeUtterance(MicUploadResult& out) {
  up_client.print("0\r\n\r\n");          // khối rỗng = hết thân request
  up_client.flush();

  out.bytes_sent = up_bytes;
  out.local_peak = up_peak;
  out.elapsed_ms = millis() - up_started_ms;

  Serial.printf("[MIC-UP] <<< Chot cau sau %u ms, da gui %u byte | bien do lon nhat: %d\n",
                (unsigned)out.elapsed_ms, (unsigned)up_bytes, up_peak);
  if (up_peak < 100) {
    Serial.println("[MIC-UP] ==> CANH BAO: doan vua gui gan nhu im lang.");
  }

  String body;
  bool in_body = false;
  unsigned long deadline = millis() + 25000;
  while (millis() < deadline) {
    if (!up_client.available()) {
      if (!up_client.connected()) break;
      delay(5);
      continue;
    }
    String line = up_client.readStringUntil('\n');
    if (!in_body) {
      if (out.http_status == 0 && line.startsWith("HTTP/")) {
        int sp = line.indexOf(' ');
        if (sp > 0) out.http_status = line.substring(sp + 1, sp + 4).toInt();
      }
      String t = line; t.trim();
      if (t.length() == 0) in_body = true;
    } else {
      body += line;
    }
  }
  up_client.stop();
  up_talking = false;

  if (body.isEmpty()) {
    out.error = "no_response";
    Serial.printf("[MIC-UP] Khong nhan duoc phan hoi (HTTP %d).\n", out.http_status);
    return;
  }

  out.transcript = jsonField(body, "transcript");
  out.reply      = jsonField(body, "response");
  out.error      = jsonField(body, "error");
  out.ok         = (body.indexOf("\"success\":true") >= 0);

  if (out.transcript.length() > 0) {
    Serial.printf("[MIC-UP] ==> SERVER NGHE DUOC: \"%s\"\n", out.transcript.c_str());
  } else {
    Serial.println("[MIC-UP] ==> Server khong nhan dang duoc cau nao.");
  }
  if (out.reply.length() > 0) {
    Serial.printf("[MIC-UP] ==> TRO LY TRA LOI: \"%s\"\n", out.reply.c_str());
  }
  if (out.error.length() > 0) {
    Serial.printf("[MIC-UP] ==> Server bao loi: %s\n", out.error.c_str());
  }
}

bool micPoll(const char* host, int port, MicUploadResult& out) {
  if (!driver_ready) return false;

  int n = captureFrame(0);        // không chặn: hết dữ liệu thì thôi
  if (n <= 0) return false;

  bool loud = sound_level >= MIC_GATE_LEVEL;
  unsigned long now = millis();

  if (!up_talking) {
    // Cổng âm thanh: phải đủ số khung liên tiếp có tiếng mới mở câu, để một
    // tiếng cạch bàn không kích hoạt cả một lượt gọi server.
    up_gate_frames = loud ? up_gate_frames + 1 : 0;
    if (up_gate_frames < MIC_GATE_FRAMES) return false;
    up_gate_frames = 0;

    out = MicUploadResult{false, 0, 0, 0, 0, String(), String(), String()};
    if (!openUtterance(host, port)) return false;

    // Đọc lại đồng hồ SAU khi mở socket.
    //
    // `now` ở trên được lấy trước openUtterance(), mà hàm đó phải bắt tay TCP --
    // mất vài trăm mili-giây. Thành ra up_started_ms lớn hơn `now`, và phép trừ
    // `now - up_started_ms` trên kiểu unsigned không cho ra số âm mà vòng về một
    // số khổng lồ. Thế là "đã nói quá 15 giây" đúng ở mili-giây thứ ba.
    now = millis();
  }

  for (int i = 0; i < n; i++) {
    int16_t a = abs(pcm_frame[i]);
    if (a > up_peak) up_peak = a;
  }
  if (!sendChunk((const uint8_t*)pcm_frame, (size_t)n * sizeof(int16_t))) {
    Serial.println("[MIC-UP] Mat ket noi giua chung, bo cau nay.");
    up_client.stop();
    up_talking = false;
    return false;
  }
  up_bytes += (uint32_t)n * sizeof(int16_t);
  if (loud) up_last_voice_ms = now;

  bool silent_enough = (now - up_last_voice_ms) >= MIC_SILENCE_MS;
  bool too_long      = (now - up_started_ms) >= MIC_MAX_UTTER_MS;
  if (silent_enough || too_long) {
    if (too_long) Serial.println("[MIC-UP] Cham tran thoi luong, chot cau.");
    closeUtterance(out);
    return true;
  }
  return false;
}
