#include <WiFi.h>
#include <WebServer.h>
#include <WiFiManager.h>      // Thư viện tạo Popup Wifi
#include <ESPmDNS.h>          // Để truy cập http://esp32-audio.local
#include "BluetoothA2DPSource.h"
#include <math.h>
#include <HTTPClient.h>
#include "esp_bt.h"

// ============================================================================
// 1. CẤU HÌNH & KHAI BÁO BIẾN TOÀN CỤC
// ============================================================================
#define SAMPLE_RATE 44100
#define FREQUENCY 440

// --- CẤU HÌNH KẾT NỐI SERVER FASTAPI / CLOUD ---
// Đặt USE_CLOUD = true để kết nối thẳng tới server đã deploy trên Fly.io
// Đặt USE_CLOUD = false nếu muốn kết nối tới IP máy tính cục bộ trong mạng LAN
const bool USE_CLOUD = true;

// Server Cloud (Fly.io) - Khuyến nghị Port 80 (HTTP) để tiết kiệm RAM tối đa khi chạy Bluetooth A2DP
const char* CLOUD_HOST = "smart-apartment-server.fly.dev";
const int CLOUD_PORT = 80; 

// Server Local (Máy tính cá nhân)
const char* LOCAL_HOST = "192.168.1.12"; 
const int LOCAL_PORT = 8000;

BluetoothA2DPSource a2dp_source;
WebServer server(80);

// Cổng 80: không dựng TLS. Giữ một WiFiClientSecure ở đây chỉ tốn RAM, mà RAM
// là thứ board này không có. Muốn chạy 443 thì phải tính lại ngân sách bộ nhớ.
WiFiClient localAudioClient;

static float m_time = 0.0;
bool is_bt_connected = false;
bool is_stream_connected = false;
bool is_playing_test_sound = false;
String current_test_speech = "";
String current_youtube_url = "";
String connected_speaker_name = "Đang tìm kiếm...";
String connected_speaker_mac = "";

unsigned long lastStreamReconnect = 0;

// Cấu hình Ring Buffer cho luồng Audio (8KB hoặc 16KB tuỳ RAM)
const int AUDIO_BUFFER_SIZE = 16384;
uint8_t audioBuffer[AUDIO_BUFFER_SIZE];
int head = 0, tail = 0;

int availableBuffer() {
  return (tail >= head) ? (tail - head) : (AUDIO_BUFFER_SIZE - head + tail);
}

Client& getAudioClient() {
  return localAudioClient;
}

// ============================================================================
// 2. CALLBACK CẤP DỮ LIỆU ÂM THANH CHO LOA BLUETOOTH A2DP
// ============================================================================
int32_t get_sound_data(uint8_t *data, int32_t len) {
  if (!is_bt_connected) {
    memset(data, 0, len);
    return len;
  }

  // Nếu đang bật âm thử nghiệm (440Hz sin wave)
  if (is_playing_test_sound) {
    int16_t *pcm = (int16_t*)data;
    int sample_count = len / 2;
    float time_step = 1.0 / SAMPLE_RATE;
    for (int i = 0; i < sample_count; i += 2) {
      int16_t sample = (int16_t)(sin(2.0 * M_PI * FREQUENCY * m_time) * 8000.0);
      pcm[i] = sample;
      pcm[i + 1] = sample;
      m_time += time_step;
    }
    return len;
  }

  // Đọc dữ liệu stream từ Backend (Google TTS 44.1kHz Stereo) đưa ra loa
  int bytesRead = 0;
  while (bytesRead < len && availableBuffer() > 0) {
    data[bytesRead++] = audioBuffer[head];
    head = (head + 1) % AUDIO_BUFFER_SIZE;
  }

  // Nếu buffer tạm trống, chèn yên lặng để tránh nổ bụp / giật tiếng
  if (bytesRead < len) {
    memset(data + bytesRead, 0, len - bytesRead);
  }

  return len;
}

// ============================================================================
// 3. QUÉT VÀ TỰ ĐỘNG CHỌN LOA BLUETOOTH (DISCOVERY)
// ============================================================================
bool ssid_callback(const char *ssid, esp_bd_addr_t address, int rssi) {
  char macStr[18];
  snprintf(macStr, sizeof(macStr), "%02X:%02X:%02X:%02X:%02X:%02X",
           address[0], address[1], address[2], address[3], address[4], address[5]);

  Serial.printf("[BT SCAN] Bắt được: '%s' | MAC: %s | RSSI: %d dBm\n", 
                (ssid && strlen(ssid) > 0) ? ssid : "Không rõ tên", macStr, rssi);

  // Chọn thiết bị đầu tiên có tên hợp lệ để kết nối
  if (ssid != NULL && strlen(ssid) > 0) {
    connected_speaker_name = String(ssid);
    connected_speaker_mac = String(macStr);

    Serial.println("----------------------------------------------");
    Serial.printf("===> CHỌN LOA BLUETOOTH: '%s' (%s)\n", ssid, macStr);
    Serial.println("===> Đang tiến hành ghép nối A2DP...");
    Serial.println("----------------------------------------------");

    return true; 
  }
  return false; 
}

void connection_state_changed(esp_a2d_connection_state_t state, void *ptr) {
  if (state == ESP_A2D_CONNECTION_STATE_CONNECTED) {
    is_bt_connected = true;
    Serial.println("\n[A2DP] ===> KẾT NỐI LOA BLUETOOTH THÀNH CÔNG!");
  } else if (state == ESP_A2D_CONNECTION_STATE_DISCONNECTED) {
    is_bt_connected = false;
    connected_speaker_name = "Đang tìm kiếm...";
    Serial.println("\n[A2DP] Ngắt kết nối với Loa. Đang chờ quét lại...");
  }
}

// ============================================================================
// 4. KẾT NỐI LUỒNG ÂM THANH PERSISTENT VỚI SERVER FASTAPI
// ============================================================================
void connectAudioStream() {
  Client& client = getAudioClient();
  if (client.connected()) return;

  const char* host = USE_CLOUD ? CLOUD_HOST : LOCAL_HOST;
  int port = USE_CLOUD ? CLOUD_PORT : LOCAL_PORT;

  Serial.printf("[STREAM] Free Heap: %d bytes | Đang kết nối luồng Audio TTS tới %s:%d...\n", 
                ESP.getFreeHeap(), host, port);

  if (client.connect(host, port)) {
    // Yêu cầu luồng âm thanh 44.1kHz Stereo (tương thích trực tiếp chuẩn A2DP Bluetooth)
    String request = String("GET /api/audio/stream?rate=44100&channels=2 HTTP/1.1\r\n") +
                     "Host: " + String(host) + "\r\n" +
                     "User-Agent: ESP32-A2DP-Speaker\r\n" +
                     "Accept: application/octet-stream\r\n" +
                     "Connection: keep-alive\r\n\r\n";
    client.print(request);

    // Bỏ qua HTTP Response Headers
    unsigned long timeout = millis();
    while (client.connected() && millis() - timeout < 4000) {
      if (client.available()) {
        String line = client.readStringUntil('\n');
        if (line == "\r" || line == "") {
          is_stream_connected = true;
          Serial.println("[STREAM] ===> KẾT NỐI LUỒNG AUDIO SERVER THÀNH CÔNG! Sẵn sàng phát tiếng Trợ lý AI.");
          break;
        }
      }
    }
  } else {
    is_stream_connected = false;
    size_t heap = ESP.getFreeHeap();
    if (heap < 20000) {
      // Mở một TCP socket cần vài KB liền kề. Dưới ngưỡng này thì không phải
      // mạng hỏng mà là hết RAM -- hai lỗi cần sửa theo hai hướng khác hẳn nhau.
      Serial.printf("[STREAM] Kết nối thất bại vì HẾT RAM (%u bytes). Mạng không phải thủ phạm.
",
                    (unsigned)heap);
    } else {
      Serial.println("[STREAM] Kết nối Server thất bại, sẽ thử lại sau 5s...");
    }
  }
}

// ============================================================================
// 5. GIAO DIỆN WEB QUẢN TRỊ & THỬ NGHIỆM
// ============================================================================
void handleRoot() {
  String html = "<!DOCTYPE html><html><head><meta charset='UTF-8'>";
  html += "<meta name='viewport' content='width=device-width, initial-scale=1.0'>";
  html += "<title>ESP32 Smart Audio Hub</title>";
  html += "<style>";
  html += "body { font-family: Arial, sans-serif; text-align: center; background: #0f172a; color: #fff; padding: 20px; }";
  html += ".card { background: #1e293b; padding: 25px; border-radius: 12px; max-width: 460px; margin: auto; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }";
  html += "input[type=text] { width: 90%; padding: 12px; margin: 10px 0; border-radius: 6px; border: 1px solid #334155; background: #0f172a; color: #fff; }";
  html += "button { background: #38bdf8; color: #0f172a; border: none; padding: 12px 20px; border-radius: 6px; cursor: pointer; font-weight: bold; margin: 6px; }";
  html += "button.test { background: #008cba; color: white; }";
  html += "button.tts { background: #22c55e; color: white; }";
  html += ".status-box { background: #0f172a; padding: 14px; border-radius: 8px; margin: 15px 0; font-size: 14px; text-align: left; border: 1px solid #334155; }";
  html += "</style></head><body>";
  
  html += "<div class='card'>";
  html += "<h2>🔊 ESP32 Smart Audio Hub</h2>";
  html += "<p style='color:#94a3b8; font-size:13px;'>Phát giọng nói Google TTS & Trợ lý ảo AI ra Loa Bluetooth</p>";
  
  html += "<div class='status-box'>";
  html += "<b>📡 Loa Bluetooth:</b> " + connected_speaker_name + "<br>";
  if (connected_speaker_mac.length() > 0) {
    html += "<b>📍 MAC:</b> " + connected_speaker_mac + "<br>";
  }
  html += "<b>Bluetooth:</b> " + String(is_bt_connected ? "<span style='color:#22c55e'>Đã kết nối</span>" : "<span style='color:#f59e0b'>Đang quét tìm...</span>") + "<br>";
  html += "<b>Server Stream:</b> " + String(is_stream_connected ? "<span style='color:#22c55e'>Đang lắng nghe</span>" : "<span style='color:#ef4444'>Chưa kết nối</span>") + "<br>";
  html += "<b>Host:</b> " + String(USE_CLOUD ? CLOUD_HOST : LOCAL_HOST) + "<br>";
  html += "</div>";

  // Thử nghiệm phát câu nói TTS trực tiếp
  html += "<form action='/say' method='POST'>";
  html += "<input type='text' name='text' placeholder='Nhập câu để AI nói thử...' value='Xin chào, tôi là trợ lý nhà thông minh.' required><br>";
  html += "<button type='submit' class='tts'>🗣️ Thử Giọng Nói AI (TTS)</button>";
  html += "</form>";

  html += "<hr style='border-color: #334155; margin: 20px 0;'>";

  html += "<form action='/play' method='POST'>";
  html += "<input type='text' name='yt_url' placeholder='Dán link YouTube (nếu có)...' value='" + current_youtube_url + "'><br>";
  html += "<button type='submit'>▶ Phát Nhạc</button>";
  html += "</form>";

  html += "<hr style='border-color: #334155; margin: 20px 0;'>";
  html += "<a href='/test_sound'><button class='test'>" + String(is_playing_test_sound ? "⏹ Tắt Âm Thử (440Hz)" : "🔔 Phát Âm Thử (440Hz)") + "</button></a> ";
  html += "<a href='/reset_wifi'><button style='background:#64748b; color:white;'>⚙ Wi-Fi</button></a>";
  
  html += "</div></body></html>";

  server.send(200, "text/html", html);
}

void handleSay() {
  if (server.hasArg("text")) {
    String textToSay = server.arg("text");
    Serial.printf("\n[WEB UI] Yêu cầu phát TTS: '%s'\n", textToSay.c_str());

    // Cùng cổng 80 như luồng audio. Dựng TLS ở đây là chỗ dễ hết heap nhất trên
    // board này: handshake cần vài chục KB trong khi A2DP đã ăn gần hết.
    HTTPClient http;
    WiFiClient webClient;
    String serverUrl = String("http://") + (USE_CLOUD ? String(CLOUD_HOST) + ":" + String(CLOUD_PORT)
                                                      : String(LOCAL_HOST) + ":" + String(LOCAL_PORT)) +
                       "/api/audio/play";
    http.begin(webClient, serverUrl);

    http.addHeader("Content-Type", "application/json");
    String jsonPayload = "{\"text\":\"" + textToSay + "\"}";
    int code = http.POST(jsonPayload);
    Serial.printf("[HTTP /api/audio/play] Mã phản hồi: %d\n", code);
    http.end();
  }

  server.sendHeader("Location", "/");
  server.send(303);
}

void handlePlay() {
  if (server.hasArg("yt_url")) {
    current_youtube_url = server.arg("yt_url");
    is_playing_test_sound = false;
  }
  server.sendHeader("Location", "/");
  server.send(303);
}

void handleTestSound() {
  is_playing_test_sound = !is_playing_test_sound;
  server.sendHeader("Location", "/");
  server.send(303);
}

void handleResetWifi() {
  server.send(200, "text/html", "<h3>Đã xóa cấu hình Wi-Fi! ESP32 đang khởi động lại...</h3>");
  delay(1000);
  WiFiManager wm;
  wm.resetSettings();
  ESP.restart();
}

// ============================================================================
// 6. KHỞI TẠO HỆ THỐNG VÀ LOOP
// ============================================================================
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== KHỞI ĐỘNG HỆ THỐNG ESP32 SMART AUDIO HUB ===");

  WiFiManager wm;
  wm.setConfigPortalTimeout(180);

  Serial.println("[WIFI] Đang kiểm tra Wi-Fi...");
  if (!wm.autoConnect("ESP32-Audio-Setup")) {
    Serial.println("[WIFI] Kết nối thất bại. Khởi động lại...");
    ESP.restart();
  }
  
  Serial.println("[WIFI] Đã kết nối Wi-Fi thành công!");
  Serial.print("[WIFI] IP ESP32: http://");
  Serial.println(WiFi.localIP());

  if (MDNS.begin("esp32-audio")) {
    Serial.println("[mDNS] Truy cập Web Server tại: http://esp32-audio.local");
  }

  server.on("/", handleRoot);
  server.on("/say", HTTP_POST, handleSay);
  server.on("/play", HTTP_POST, handlePlay);
  server.on("/test_sound", handleTestSound);
  server.on("/reset_wifi", handleResetWifi);
  server.begin();
  Serial.println("[WEB] Web Server quản trị đã sẵn sàng.");

  // Cấu hình Bluetooth A2DP Source tới Loa ngoài
  a2dp_source.set_ssid_callback(ssid_callback);                     
  a2dp_source.set_on_connection_state_changed(connection_state_changed); 
  a2dp_source.set_data_callback(get_sound_data);                    
  a2dp_source.set_auto_reconnect(false);                            

  // A2DP chỉ cần Bluetooth Classic (BR/EDR). Controller mặc định giữ sẵn cả phần
  // RAM cho BLE -- ở đây không bao giờ dùng tới. Trả lại trước khi controller khởi
  // tạo, nếu không heap còn ~8 KB sau khi ghép loa và không mở nổi một TCP socket.
  size_t heap_before = ESP.getFreeHeap();
  esp_err_t released = esp_bt_controller_mem_release(ESP_BT_MODE_BLE);
  Serial.printf("[A2DP] Trả lại RAM của BLE: %s | Heap %u -> %u bytes
",
                released == ESP_OK ? "OK" : "bỏ qua",
                (unsigned)heap_before, (unsigned)ESP.getFreeHeap());

  Serial.println("[A2DP] Bắt đầu quét các loa Bluetooth xung quanh...");
  a2dp_source.start(); 
}

void loop() {
  server.handleClient();

  // Tự động kết nối và duy trì luồng âm thanh từ Server
  Client& client = getAudioClient();
  unsigned long now = millis();

  if (WiFi.status() == WL_CONNECTED && !client.connected()) {
    is_stream_connected = false;
    if (now - lastStreamReconnect > 5000) {
      lastStreamReconnect = now;
      connectAudioStream();
    }
  }

  // Đọc dữ liệu audio stream từ Server đưa vào Ring Buffer để phát ra Loa
  while (client.connected() && client.available()) {
    // Vùng trống liền kề tới cuối mảng, để đọc được cả khối một lần. 44.1 kHz
    // stereo là 176 KB/s -- gọi read() từng byte là phí CPU mà board không dư.
    int room = (tail >= head) ? (AUDIO_BUFFER_SIZE - tail - (head == 0 ? 1 : 0))
                              : (head - tail - 1);
    if (room <= 0) break;

    int got = client.read(audioBuffer + tail, room);
    if (got <= 0) break;
    tail = (tail + got) % AUDIO_BUFFER_SIZE;
  }
}