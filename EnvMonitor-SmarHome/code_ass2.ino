#include "Config.h"
#include "Sensors.h"
#include "AudioHandler.h"
#include "DisplayHandler.h"
#include "Controls.h"
#include "WiFiConfig.h"
#include "StatusManager.h"
#include "MqttHandler.h"

unsigned long lastSensorRead = 0;
const long sensorInterval = 1000;

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000);

  Serial.println("\n=======================================================");
  Serial.println("=== ESP32 SMART HOME CONTROLLER - BOOT SEQUENCE     ===");
  Serial.println("=== Firmware Version: 2.1 (Full Telemetry & Dual-Sync) ===");
  Serial.println("=======================================================");

  // 1. Khởi tạo phần cứng
  Serial.println("[INIT] 1/4. Khởi tạo hệ thống điều khiển & Servo...");
  initControls();
  Serial.println("[INIT] 2/4. Khởi tạo Micro I2S INMP441...");
  setupINMP441();
  Serial.println("[INIT] 3/4. Khởi tạo màn hình LCD I2C...");
  initLCDs();
  Serial.println("[INIT] 4/4. Khởi tạo cảm biến DHT11 & Siêu âm...");
  initSensors();

  // 2. WiFi Config
  Serial.println("[INIT] Đang thiết lập kết nối Wi-Fi...");
  initWiFiPortal("ESP32_SmartHome_AP", "12345678", 180, &lcd_lr);

  // 3. Test phần cứng
  Serial.println("[INIT] Chạy chu kỳ test Buzzer và LED...");
  testBuzzerAndLEDs();

  // 4. Khởi tạo MQTT HiveMQ Cloud
  Serial.println("[INIT] Khởi tạo kết nối MQTT TLS HiveMQ Cloud...");
  setupMQTT();

  Serial.println("[SYSTEM] ===> KHỞI ĐỘNG HOÀN TẤT! BẮT ĐẦU VÒNG LẶP GIÁM SÁT <===");
}

void loop() {
  // Lắng nghe MQTT (Non-blocking)
  loopMQTT();

  // Đọc nút bấm liên tục (Real-time)
  handleButtons();

  // Đọc cảm biến định kỳ mỗi 1 giây
  unsigned long currentMillis = millis();
  if (currentMillis - lastSensorRead >= sensorInterval) {
    lastSensorRead = currentMillis;

    float temp = 0.0, hum = 0.0;
    bool isDhtValid = readDHTData(temp, hum);
    float dist = readUltrasonicDistance();
    int soundLevel = readINMP441SoundLevel();

    Serial.println("==========================================");
    if (isDhtValid) {
      Serial.printf("DHT11   | Nhiệt độ: %.1f *C | Độ ẩm: %.1f %%\n", temp, hum);
    } else {
      Serial.println("DHT11   | Lỗi đọc dữ liệu (bỏ qua gửi API)!");
    }

    if (dist >= 0) {
      Serial.printf("Siêu âm | Khoảng cách: %.1f cm\n", dist);
      //updateWCLedByDistance(dist); // Bật/tắt đèn WC tự động
    } else {
      Serial.println("Siêu âm | Quá tầm / Lỗi!");
    }

    Serial.printf("INMP441 | Mức âm thanh: %d\n", soundLevel);

    // Cập nhật màn hình LCD tại chỗ (không gửi API HTTP)
    updateLCDLivingRoom(temp, hum, isDhtValid);
    updateLCDBedroom(dist, soundLevel);

    // Lọc biến động cảm biến và chỉ gửi API khi dữ liệu HỢP LỆ & THAY ĐỔI
    checkAndSendSensors(temp, hum, isDhtValid, dist, soundLevel);
  }
}