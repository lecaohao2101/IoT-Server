// Sketch chẩn đoán: CHỈ ghép nối loa Bluetooth A2DP, không gì khác.
//
// Mục đích duy nhất là tách bạch hai nguyên nhân mà sketch đầy đủ không phân biệt
// được, vì ở đó Wi-Fi, web server, luồng TCP và Bluetooth cùng chạy một lúc:
//
//   Ghép được ở đây, không ghép được ở sketch đầy đủ
//       -> loa và thư viện đều ổn. Thủ phạm là Wi-Fi giành sóng 2.4 GHz với
//          Bluetooth, hoặc RAM không đủ lúc bắt tay.
//
//   Không ghép được cả ở đây
//       -> vấn đề nằm ở chính con loa hoặc trạng thái của nó. Gần như luôn là
//          loa đang kết nối sẵn với điện thoại, hoặc chưa vào chế độ ghép nối.
//
// Ở đây không có Wi-Fi nên Bluetooth độc chiếm ăng-ten, và heap còn gần như
// nguyên vẹn -- điều kiện thuận lợi nhất có thể. Ghép không nổi trong hoàn cảnh
// này thì sửa phần mềm bao nhiêu cũng vô ích.
//
// Nạp cho ESP32 thường, Partition Scheme: Huge APP (3MB No OTA/1MB SPIFFS).

#include "BluetoothA2DPSource.h"
#include "esp_bt.h"
#include <math.h>

// Để rỗng ("") thì chấp nhận thiết bị A2DP đầu tiên quét được -- chỉ nên dùng khi
// nghi ngờ chính cái tên bị sai, vì lúc đó board sẽ thử ghép cả tai nghe, TV
// hàng xóm, và những thứ đó không phải loa A2DP nên sẽ thất bại lặp vô tận.
#define TARGET_SPEAKER_NAME "G-10"

#define SAMPLE_RATE 44100
#define TONE_HZ     440

BluetoothA2DPSource a2dp_source;

static bool  connected = false;
static float m_time = 0.0f;
static unsigned long last_report = 0;
static int   scan_hits = 0;
static int   connect_attempts = 0;

// Phát một nốt 440 Hz liên tục. Nghe thấy tiếng này tức là toàn bộ chặng
// board -> Bluetooth -> loa đã thông.
int32_t get_sound_data(uint8_t* data, int32_t len) {
  if (!connected) {
    memset(data, 0, len);
    return len;
  }
  int16_t* pcm = (int16_t*)data;
  int samples = len / 2;
  float step = 1.0f / SAMPLE_RATE;
  for (int i = 0; i < samples; i += 2) {
    int16_t s = (int16_t)(sin(2.0 * M_PI * TONE_HZ * m_time) * 8000.0);
    pcm[i] = s;
    pcm[i + 1] = s;
    m_time += step;
  }
  return len;
}

bool ssid_callback(const char* ssid, esp_bd_addr_t address, int rssi) {
  char mac[18];
  snprintf(mac, sizeof(mac), "%02X:%02X:%02X:%02X:%02X:%02X",
           address[0], address[1], address[2], address[3], address[4], address[5]);

  scan_hits++;
  bool match = (strlen(TARGET_SPEAKER_NAME) == 0) ||
               (strcasestr(ssid, TARGET_SPEAKER_NAME) != NULL);

  Serial.printf("[SCAN] '%s' | %s | RSSI %d dBm %s\n",
                ssid, mac, rssi, match ? "<== THU GHEP NOI" : "(bo qua)");

  if (match) {
    connect_attempts++;
    Serial.printf("[SCAN] Lan thu ghep noi thu %d | Heap: %u byte\n",
                  connect_attempts, (unsigned)ESP.getFreeHeap());
  }
  return match;
}

void connection_state_changed(esp_a2d_connection_state_t state, void* ptr) {
  switch (state) {
    case ESP_A2D_CONNECTION_STATE_CONNECTED:
      connected = true;
      Serial.println("\n==========================================");
      Serial.println("[A2DP] ===> GHEP NOI THANH CONG!");
      Serial.println("[A2DP] Ban phai nghe thay mot not 440 Hz lien tuc tu loa.");
      Serial.println("==========================================\n");
      break;

    case ESP_A2D_CONNECTION_STATE_CONNECTING:
      Serial.println("[A2DP] Dang bat tay...");
      break;

    case ESP_A2D_CONNECTION_STATE_DISCONNECTING:
      Serial.println("[A2DP] Dang ngat...");
      break;

    case ESP_A2D_CONNECTION_STATE_DISCONNECTED:
      // Sau một lần thử ghép thất bại cũng rơi vào đây, nên phân biệt bằng cờ
      // `connected`: mất kết nối đang có, hay chưa bao giờ nối được.
      if (connected) {
        Serial.println("[A2DP] Mat ket noi dang co.");
      } else {
        Serial.println("[A2DP] Bat tay THAT BAI (chua tung ket noi duoc).");
      }
      connected = false;
      break;
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\n==========================================");
  Serial.println("=== CHAN DOAN GHEP NOI LOA BLUETOOTH   ===");
  Serial.println("=== Khong Wi-Fi, khong web, khong mic   ===");
  Serial.println("==========================================");
  Serial.printf("[INFO] Dang tim loa ten chua: '%s'\n", TARGET_SPEAKER_NAME);
  Serial.printf("[INFO] Heap truoc khi bat Bluetooth: %u byte\n", (unsigned)ESP.getFreeHeap());

  a2dp_source.set_ssid_callback(ssid_callback);
  a2dp_source.set_on_connection_state_changed(connection_state_changed);
  a2dp_source.set_data_callback(get_sound_data);
  a2dp_source.set_auto_reconnect(false);

  // Thư viện mặc định TẮT Secure Simple Pairing và lùi về ghép nối kiểu cũ với mã
  // PIN "1234". Loa hiện đại dùng SSP, và khi tắt thì thư viện bỏ qua luôn sự
  // kiện loa hỏi xác nhận -- bắt tay treo tới khi hết giờ mà không báo lỗi gì.
  a2dp_source.set_ssp_enabled(true);

  // A2DP chỉ cần Bluetooth Classic. Trả lại phần RAM dành sẵn cho BLE trước khi
  // controller khởi tạo -- giống hệt sketch đầy đủ, để điều kiện so sánh được.
  esp_bt_controller_mem_release(ESP_BT_MODE_BLE);

  a2dp_source.start();
  Serial.printf("[INFO] Heap sau khi bat Bluetooth: %u byte\n", (unsigned)ESP.getFreeHeap());
  Serial.println("[INFO] Dang quet...\n");
}

void loop() {
  unsigned long now = millis();
  if (now - last_report < 5000) return;
  last_report = now;

  Serial.printf("[TRANG THAI] %s | Lan quet thay loa: %d | Lan thu ghep: %d | Heap: %u byte\n",
                connected ? "DA NOI - dang phat not 440 Hz" : "chua noi",
                scan_hits, connect_attempts, (unsigned)ESP.getFreeHeap());

  if (!connected && connect_attempts >= 3) {
    Serial.println("[KET LUAN] Da thay loa va thu ghep nhieu lan nhung khong xong bat tay.");
    Serial.println("[KET LUAN] Khong Wi-Fi, heap con rat nhieu -- nen khong phai loi tai nguyen.");
    Serial.println("[KET LUAN] ==> Gan nhu chac chan loa dang ket noi voi mot thiet bi khac");
    Serial.println("[KET LUAN]     (dien thoai), hoac chua vao che do ghep noi.");
  }
}
