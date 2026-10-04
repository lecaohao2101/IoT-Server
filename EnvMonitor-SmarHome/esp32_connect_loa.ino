#include <WiFi.h>
#include <WebServer.h>
#include <WiFiManager.h>      // Thư viện tạo Popup Wifi
#include <ESPmDNS.h>          // Để truy cập http://esp32-audio.local
#include "BluetoothA2DPSource.h"
#include <math.h>
#include <HTTPClient.h>

// ============================================================================
// 1. CẤU HÌNH & KHAI BÁO BIẾN TOÀN CỤC (PHẢI ĐẶT Ở ĐẦU)
// ============================================================================
#define SAMPLE_RATE 44100
#define FREQUENCY 440

// --- ĐIỀN IP MÁY TÍNH CHẠY PYTHON FASTAPI TẠI ĐÂY ---
const char* FASTAPI_IP = "192.168.1.12"; 
const int FASTAPI_PORT = 8000;

BluetoothA2DPSource a2dp_source;
WebServer server(80);
WiFiClient audioClient;

static float m_time = 0.0;
bool is_bt_connected = false;
bool is_playing_test_sound = false;
String current_youtube_url = "";
String connected_speaker_name = "Đang tìm kiếm...";
String connected_speaker_mac = "";

// Cấu hình Ring Buffer cho luồng Audio
const int AUDIO_BUFFER_SIZE = 8192;
uint8_t audioBuffer[AUDIO_BUFFER_SIZE];
int head = 0, tail = 0;

int availableBuffer() {
  return (tail >= head) ? (tail - head) : (AUDIO_BUFFER_SIZE - head + tail);
}

// ============================================================================
// 2. CALLBACK CẤP DỮ LIỆU ÂM THANH (DUY NHẤT)
// ============================================================================
int32_t get_sound_data(uint8_t *data, int32_t len) {
  if (!is_bt_connected) {
    memset(data, 0, len);
    return len;
  }

  // Nếu đang bật âm thử nghiệm (440Hz)
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

  // Đọc dữ liệu stream từ Python Backend đưa ra loa
  int bytesRead = 0;
  while (bytesRead < len && availableBuffer() > 0) {
    data[bytesRead++] = audioBuffer[head];
    head = (head + 1) % AUDIO_BUFFER_SIZE;
  }

  // Nếu buffer bị trống, chèn yên lặng để tránh giật tiếng
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
    Serial.printf("===> CHỌN LOA: '%s' (%s)\n", ssid, macStr);
    Serial.println("===> Đang tiến hành ghép nối A2DP...");
    Serial.println("----------------------------------------------");

    return true; 
  }
  return false; 
}

void connection_state_changed(esp_a2d_connection_state_t state, void *ptr) {
  if (state == ESP_A2D_CONNECTION_STATE_CONNECTED) {
    is_bt_connected = true;
    Serial.println("\n[A2DP] ===> KẾT NỐI LOA THÀNH CÔNG!");
  } else if (state == ESP_A2D_CONNECTION_STATE_DISCONNECTED) {
    is_bt_connected = false;
    connected_speaker_name = "Đang tìm kiếm...";
    Serial.println("\n[A2DP] Ngắt kết nối với Loa. Đang chờ quét lại...");
  }
}

// ============================================================================
// 4. GIAO DIỆN WEB VÀ XỬ LÝ (HTML/JS)
// ============================================================================
void handleRoot() {
  String html = "<!DOCTYPE html><html><head><meta charset='UTF-8'>";
  html += "<meta name='viewport' content='width=device-width, initial-scale=1.0'>";
  html += "<title>ESP32 Smart Audio Hub</title>";
  html += "<style>";
  html += "body { font-family: Arial, sans-serif; text-align: center; background: #121212; color: #fff; padding: 20px; }";
  html += ".card { background: #1e1e1e; padding: 25px; border-radius: 12px; max-width: 420px; margin: auto; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }";
  html += "input[type=text] { width: 90%; padding: 12px; margin: 12px 0; border-radius: 6px; border: 1px solid #333; background: #2a2a2a; color: #fff; }";
  html += "button { background: #ff0000; color: white; border: none; padding: 12px 20px; border-radius: 6px; cursor: pointer; font-weight: bold; margin: 6px; }";
  html += "button.test { background: #008cba; }";
  html += ".status-box { background: #2a2a2a; padding: 12px; border-radius: 8px; margin: 15px 0; font-size: 14px; text-align: left; }";
  html += "</style></head><body>";
  
  html += "<div class='card'>";
  html += "<h2>🎵 ESP32 Audio Controller</h2>";
  
  html += "<div class='status-box'>";
  html += "<b>🔊 Loa Bluetooth:</b> " + connected_speaker_name + "<br>";
  if (connected_speaker_mac.length() > 0) {
    html += "<b>📍 MAC:</b> " + connected_speaker_mac + "<br>";
  }
  html += "<b>Status:</b> " + String(is_bt_connected ? "<span style='color:#4caf50'>Đã kết nối</span>" : "<span style='color:#ff9800'>Đang quét tìm...</span>");
  html += "</div>";

  html += "<form action='/play' method='POST'>";
  html += "<input type='text' name='yt_url' placeholder='Dán link YouTube vào đây...' value='" + current_youtube_url + "' required><br>";
  html += "<button type='submit'>▶ Phát Nhạc YouTube</button>";
  html += "</form>";

  html += "<hr style='border-color: #333; margin: 20px 0;'>";
  html += "<a href='/test_sound'><button class='test'>" + String(is_playing_test_sound ? "⏹ Tắt Âm Thử Nghiệm" : "🔔 Phát Âm Thử Nghiệm (440Hz)") + "</button></a><br>";
  html += "<a href='/reset_wifi'><button style='background:#555;'>⚙ Cấu hình lại Wi-Fi</button></a>";
  
  if (current_youtube_url.length() > 0) {
    html += "<p style='color:#4caf50; font-size:13px; margin-top:15px;'>Đang chọn bài: " + current_youtube_url + "</p>";
  }
  html += "</div></body></html>";

  server.send(200, "text/html", html);
}

void handlePlay() {
  if (server.hasArg("yt_url")) {
    current_youtube_url = server.arg("yt_url");
    Serial.printf("\n[WEB UI] Nhận URL YouTube mới: %s\n", current_youtube_url.c_str());

    is_playing_test_sound = false;

    // Sử dụng IP đã khai báo ở đầu file
    HTTPClient http;
    String serverUrl = "http://" + String(FASTAPI_IP) + ":" + String(FASTAPI_PORT) + "/api/audio/play";
    
    http.begin(serverUrl);
    http.addHeader("Content-Type", "application/json");
    
    String jsonPayload = "{\"url\":\"" + current_youtube_url + "\"}";
    int httpResponseCode = http.POST(jsonPayload);

    if (httpResponseCode > 0) {
      Serial.printf("[HTTP] Gửi URL sang Backend thành công, mã phản hồi: %d\n", httpResponseCode);
      
      // Mở kết nối đọc Stream PCM từ FastAPI
      // Mở kết nối đọc Stream PCM từ FastAPI
      if (audioClient.connect(FASTAPI_IP, FASTAPI_PORT)) {
        // Dùng HTTP/1.0 để tắt tính năng Transfer-Encoding: chunked
        audioClient.print(String("GET /api/audio/stream HTTP/1.0\r\n") +
                          "Host: " + String(FASTAPI_IP) + "\r\n" +
                          "Connection: close\r\n\r\n");
        
        // Vòng lặp đọc và vứt bỏ phần Header HTTP, chỉ giữ lại phần nhạc
        unsigned long timeout = millis();
        while (audioClient.connected() && millis() - timeout < 3000) {
          if (audioClient.available()) {
            String line = audioClient.readStringUntil('\n');
            if (line == "\r" || line == "") {
              Serial.println("[STREAM] Đã bỏ qua Header, bắt đầu nhận Audio PCM!");
              break; 
            }
          }
        }
      }
    } else {
      Serial.printf("[HTTP] Lỗi gửi tới Backend: %s\n", http.errorToString(httpResponseCode).c_str());
    }
    http.end();
  }
  
  server.sendHeader("Location", "/");
  server.send(333);
}

void handleTestSound() {
  is_playing_test_sound = !is_playing_test_sound;
  server.sendHeader("Location", "/");
  server.send(333);
}

void handleResetWifi() {
  server.send(200, "text/html", "<h3>Đã xóa cài đặt Wi-Fi! ESP32 đang khởi động lại...</h3>");
  delay(1000);
  WiFiManager wm;
  wm.resetSettings();
  ESP.restart();
}

// ============================================================================
// 5. KHỞI TẠO HỆ THỐNG VÀ LOOP
// ============================================================================
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== KHỞI ĐỘNG HỆ THỐNG ESP32 SMART AUDIO HUB ===");

  WiFiManager wm;
  wm.setConfigPortalTimeout(180);

  Serial.println("[WIFI] Kiểm tra Wi-Fi...");
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
  server.on("/play", HTTP_POST, handlePlay);
  server.on("/test_sound", handleTestSound);
  server.on("/reset_wifi", handleResetWifi);
  server.begin();
  Serial.println("[WEB] Web Server đã sẵn sàng.");

  a2dp_source.set_ssid_callback(ssid_callback);                     
  a2dp_source.set_on_connection_state_changed(connection_state_changed); 
  a2dp_source.set_data_callback(get_sound_data);                    
  a2dp_source.set_auto_reconnect(false);                            

  Serial.println("[A2DP] Bắt đầu quét các loa Bluetooth xung quanh...");
  a2dp_source.start(); 
}

void loop() {
  server.handleClient(); 

  // Đọc stream từ Backend đưa vào bộ đệm âm thanh
  while (audioClient.connected() && audioClient.available()) {
    int nextTail = (tail + 1) % AUDIO_BUFFER_SIZE;
    if (nextTail != head) { 
      audioBuffer[tail] = audioClient.read();
      tail = nextTail;
    } else {
      break; 
    }
  }
}