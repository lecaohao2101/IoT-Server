#include "SpeakerStream.h"

#include <WiFi.h>
#include <string.h>

#include "AudioBus.h"

// 512 byte = 256 mẫu = 16 ms. Cùng cỡ khung với mic, nên một vòng loop() xử lý
// đúng một "nhịp" của cả hai chiều.
static constexpr size_t READ_CHUNK_SAMPLES = 256;

static WiFiClient     stream_client;
static String         srv_host;
static int            srv_port = 80;
static bool           header_done = false;
static unsigned long  last_attempt_ms = 0;
static constexpr unsigned long RETRY_EVERY_MS = 5000;

// Trạng thái bộ bóc khung chunked. `remaining` là số byte dữ liệu còn lại của
// khối đang đọc dở; 0 nghĩa là đang đứng ở ranh giới và phải đọc dòng kích thước.
static uint32_t chunk_remaining = 0;
static bool     chunked_body = false;

// Một byte lẻ còn thừa từ lần đọc trước.
//
// Ranh giới khối chunked KHÔNG trùng với ranh giới mẫu 16-bit: server cắt khối
// theo kích thước gói, không theo cặp byte. Nửa mẫu bị cắt rời phải được giữ lại
// và ghép với byte đầu của lần đọc sau. Vứt nó đi thì mọi mẫu từ đó trở về sau
// lệch một byte -- byte thấp ghép với byte cao của mẫu kế tiếp, và tiếng nói
// biến thành tiếng rè đều đặn.
static uint8_t carry_byte = 0;
static bool    has_carry = false;

static SpeakerStats stats = {false, 0, 0, 0, 0, 0};

bool speakerStreamConnected() { return stream_client.connected() && header_done; }

SpeakerStats speakerStreamStats() {
  stats.connected = speakerStreamConnected();
  return stats;
}

// Đọc một dòng kết thúc bằng \r\n, KHÔNG chặn quá `timeout_ms`.
//
// Không dùng client.readStringUntil(): hàm đó chặn theo timeout mặc định của
// Stream, mà ở giữa luồng âm thanh thời gian thực thì một lần chặn là một lần
// đứt tiếng.
static bool readLine(String& out, uint32_t timeout_ms) {
  out = "";
  unsigned long deadline = millis() + timeout_ms;
  while (millis() < deadline) {
    if (!stream_client.available()) {
      if (!stream_client.connected()) return false;
      delay(1);
      continue;
    }
    char c = (char)stream_client.read();
    if (c == '\n') {
      if (out.endsWith("\r")) out.remove(out.length() - 1);
      return true;
    }
    out += c;
    if (out.length() > 256) return false;   // dòng tiêu đề dài bất thường
  }
  return false;
}

static void closeStream(const char* why) {
  if (stream_client.connected() || header_done) {
    Serial.printf("[LOA] Dong luong: %s\n", why);
  }
  stream_client.stop();
  header_done = false;
  chunk_remaining = 0;
  chunked_body = false;
  // Nửa mẫu còn treo lại thuộc về luồng vừa đứt. Mang sang luồng sau là ghép
  // byte của hai câu nói khác nhau vào cùng một mẫu.
  has_carry = false;
  stats.connected = false;
}

bool speakerStreamBegin(const char* host, int port) {
  srv_host = host;
  srv_port = port;

  if (WiFi.status() != WL_CONNECTED) return false;

  closeStream("mo lai");
  last_attempt_ms = millis();

  if (!stream_client.connect(host, port, 8000)) {
    Serial.printf("[LOA] Khong ket noi duoc %s:%d (heap %u byte).\n",
                  host, port, (unsigned)ESP.getFreeHeap());
    return false;
  }

  // HTTP/1.1 vì uvicorn trả chunked dù xin phiên bản nào -- xem ghi chú ở header.
  // Đã phải tự bóc khung rồi thì xin đúng phiên bản mình nói còn trung thực hơn.
  String req = String("GET /api/audio/stream?rate=") + AUDIO_SAMPLE_RATE +
               "&channels=1 HTTP/1.1\r\n" +
               "Host: " + srv_host + "\r\n" +
               "User-Agent: ESP32-Speaker-I2S\r\n" +
               "Accept: application/octet-stream\r\n" +
               "Connection: close\r\n\r\n";
  stream_client.print(req);

  // Đọc tiêu đề. Phải biết có chunked hay không TRƯỚC khi chạm vào byte đầu tiên
  // của thân, nếu không mấy ký tự kích thước khối sẽ đi thẳng ra loa.
  int status_code = 0;
  chunked_body = false;
  String line;
  unsigned long deadline = millis() + 8000;
  while (millis() < deadline) {
    if (!readLine(line, 2000)) { closeStream("khong doc duoc tieu de"); return false; }
    if (status_code == 0 && line.startsWith("HTTP/")) {
      int sp = line.indexOf(' ');
      if (sp > 0) status_code = line.substring(sp + 1, sp + 4).toInt();
    }
    String lower = line;
    lower.toLowerCase();
    if (lower.startsWith("transfer-encoding:") && lower.indexOf("chunked") >= 0) {
      chunked_body = true;
    }
    if (line.length() == 0) break;   // dòng trống = hết tiêu đề
  }

  if (status_code != 200) {
    Serial.printf("[LOA] Server tra HTTP %d, khong phai 200.\n", status_code);
    closeStream("status khong phai 200");
    return false;
  }

  header_done = true;
  chunk_remaining = 0;
  stats.reconnects++;
  Serial.printf("[LOA] Dang nghe %s:%d/api/audio/stream @%d Hz mono | khung: %s\n",
                srv_host.c_str(), srv_port, AUDIO_SAMPLE_RATE,
                chunked_body ? "chunked (phai boc)" : "tho");
  Serial.println("[LOA] Im lang luc nay la BINH THUONG: server chi gui khi tro ly noi.");
  return true;
}

// Đọc tối đa `want` byte dữ liệu thuần (đang nằm trong thân khối) rồi đẩy ra loa.
static void pumpPayload(uint32_t want) {
  static uint8_t  buf[READ_CHUNK_SAMPLES * 2 + 1];
  static int16_t  pcm[READ_CHUNK_SAMPLES];

  while (want > 0) {
    int avail = stream_client.available();
    if (avail <= 0) return;

    size_t budget = (size_t)avail;
    if (budget > want) budget = want;
    if (budget > READ_CHUNK_SAMPLES * 2) budget = READ_CHUNK_SAMPLES * 2;
    if (budget == 0) return;

    size_t offset = has_carry ? 1 : 0;
    if (offset) buf[0] = carry_byte;

    int got = stream_client.read(buf + offset, budget);
    if (got <= 0) return;

    // MỌI byte đọc được đều phải trừ vào khung chunked, kể cả byte lẻ phải để
    // dành. Trừ thiếu một byte là ranh giới khối lệch đi một, và từ đó dòng kích
    // thước "200\r\n" của khối sau bị đọc nhầm thành tiếng nói.
    want -= (uint32_t)got;
    if (chunked_body) chunk_remaining -= (uint32_t)got;
    stats.bytes_from_server += (uint32_t)got;

    size_t total = (size_t)got + offset;
    size_t usable = total - (total % 2);
    has_carry = (total % 2) != 0;
    if (has_carry) carry_byte = buf[total - 1];
    if (usable == 0) continue;

    // memcpy chứ không ép kiểu con trỏ: khi có byte để dành thì dữ liệu bắt đầu
    // ở địa chỉ lẻ, mà Xtensa không đọc được số 16-bit ở địa chỉ lẻ.
    memcpy(pcm, buf, usable);

    size_t samples = usable / 2;
    for (size_t i = 0; i < samples; i++) {
      if (pcm[i] != 0) { stats.last_audio_ms = millis(); break; }
    }

    // Chờ tối đa 20 ms thôi. DMA đầy nghĩa là board đang tụt lại phía sau luồng
    // thời gian thực; chặn thêm chỉ làm mic trễ theo. Bỏ phần thừa và ghi nhận.
    size_t wrote = audioBusWrite(pcm, samples, 20);
    stats.bytes_to_speaker += (uint32_t)wrote * 2;
    if (wrote < samples) stats.dropped_bytes += (uint32_t)(samples - wrote) * 2;
  }
}

void speakerStreamPoll() {
  if (!header_done) {
    // Thử lại có nhịp. Server không với tới mà cứ gọi mỗi vòng loop() thì board
    // dành cả đời cho việc bắt tay TCP hỏng.
    if (srv_host.length() && millis() - last_attempt_ms >= RETRY_EVERY_MS) {
      speakerStreamBegin(srv_host.c_str(), srv_port);
    }
    return;
  }

  if (!stream_client.connected() && !stream_client.available()) {
    closeStream("server dong ket noi");
    return;
  }

  if (!chunked_body) {
    pumpPayload(READ_CHUNK_SAMPLES * 2);
    return;
  }

  // Thân chunked: mỗi khối là "<kich thuoc he 16>\r\n<du lieu>\r\n".
  for (int guard = 0; guard < 8; guard++) {
    if (chunk_remaining == 0) {
      if (stream_client.available() < 3) return;

      String size_line;
      if (!readLine(size_line, 50)) return;
      size_line.trim();
      if (size_line.length() == 0) continue;          // \r\n đuôi của khối trước

      int semi = size_line.indexOf(';');              // phần mở rộng, không dùng
      if (semi >= 0) size_line = size_line.substring(0, semi);

      chunk_remaining = (uint32_t)strtoul(size_line.c_str(), nullptr, 16);
      if (chunk_remaining == 0) {
        closeStream("server bao het luong (khoi rong)");
        return;
      }
    }

    uint32_t before = chunk_remaining;
    pumpPayload(chunk_remaining);
    if (chunk_remaining == before) return;            // chưa có thêm byte nào
  }
}

bool speakerStreamSay(const char* host, int port, const String& text) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[LOA] Chua co Wi-Fi, khong goi duoc /api/audio/play.");
    return false;
  }

  WiFiClient client;
  if (!client.connect(host, port, 8000)) {
    Serial.printf("[LOA] Khong ket noi duoc %s:%d de gui lenh doc.\n", host, port);
    return false;
  }

  // JSON dựng tay: thêm ArduinoJson vào đây chỉ để bọc một chuỗi là không đáng.
  // Chỉ thoát dấu " và \, đủ cho câu thử do chính firmware này đặt ra.
  String escaped;
  for (unsigned int i = 0; i < text.length(); i++) {
    char c = text[i];
    if (c == '"' || c == '\\') escaped += '\\';
    escaped += c;
  }
  String body = String("{\"text\":\"") + escaped + "\"}";

  client.print(String("POST /api/audio/play HTTP/1.1\r\n") +
               "Host: " + String(host) + "\r\n" +
               "Content-Type: application/json\r\n" +
               "Content-Length: " + String(body.length()) + "\r\n" +
               "Connection: close\r\n\r\n" + body);

  int status_code = 0;
  unsigned long deadline = millis() + 15000;
  while (millis() < deadline) {
    if (!client.available()) {
      if (!client.connected()) break;
      delay(5);
      continue;
    }
    String line = client.readStringUntil('\n');
    if (line.startsWith("HTTP/")) {
      int sp = line.indexOf(' ');
      if (sp > 0) status_code = line.substring(sp + 1, sp + 4).toInt();
      break;
    }
  }
  client.stop();

  if (status_code == 200) {
    Serial.printf("[LOA] Da nho server doc: \"%s\"\n", text.c_str());
    Serial.println("[LOA] Tieng se toi qua luong /api/audio/stream trong vai giay.");
    return true;
  }
  Serial.printf("[LOA] Server tu choi lenh doc (HTTP %d).\n", status_code);
  return false;
}
