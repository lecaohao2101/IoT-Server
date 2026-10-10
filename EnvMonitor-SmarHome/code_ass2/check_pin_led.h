#include <Arduino.h>

// Struct chứa thông tin các chân LED cần dò
struct LEDPin {
  int pin;
  const char* varName;
  const char* description;
};

// Danh sách các chân LED theo sơ đồ thiết kế
LEDPin leds[] = {
  {10, "PIN_LED_LR_MAIN", "LED Chính - Phòng khách"},
  {11, "PIN_LED_LR_SOFA", "LED Sofa - Phòng khách"},
  {14, "PIN_LED_KIT_MAIN", "LED Chính - Nhà bếp"},
  {17, "PIN_LED_BED_MAIN", "LED Chính - Phòng ngủ"},
  {18, "PIN_LED_BED_SIDE", "LED Đầu giường - Phòng ngủ"},
  {36, "PIN_LED_STUDY",    "LED - Phòng làm việc"},
  {37, "PIN_LED_BALCONY",  "LED - Ban công"},
  {39, "PIN_LED_WC",       "LED - Nhà vệ sinh"}
};

const int totalLeds = sizeof(leds) / sizeof(leds[0]);

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\n==============================================");
  Serial.println("  CHƯƠNG TRÌNH DÒ VÀ CHẨN ĐOÁN CHÂN LED (ESP32-S3)");
  Serial.println("==============================================");

  // Cấu hình tất cả các chân LED là OUTPUT và tắt hết
  for (int i = 0; i < totalLeds; i++) {
    pinMode(leds[i].pin, OUTPUT);
    digitalWrite(leds[i].pin, LOW);
  }
}

void loop() {
  for (int i = 0; i < totalLeds; i++) {
    // Đảm bảo tất cả các chân đều TẮT trước khi bật chân cần dò
    for (int j = 0; j < totalLeds; j++) {
      digitalWrite(leds[j].pin, LOW);
    }

    // Bật duy nhất chân GPIO hiện tại
    digitalWrite(leds[i].pin, HIGH);

    Serial.println("----------------------------------------------");
    Serial.printf("[DÒ CHÂN] >>> ĐANG CẤP NGUỒN CHO GPIO %d <<<\n", leds[i].pin);
    Serial.printf("Tên biến trong Code : %s\n", leds[i].varName);
    Serial.printf("Vị trí thiết kế     : %s\n", leds[i].description);
    Serial.println("-> Quan sát xem bóng LED thực tế nào đang sáng để cắm lại dây.");

    // Đếm ngược 10 giây
    for (int sec = 1; sec > 0; sec--) {
      Serial.printf("Chuyển chân sau: %d giây...\n", sec);
      delay(1000);
    }

    // Tắt LED sau khi hết 10s
    digitalWrite(leds[i].pin, LOW);
    delay(500); // Tạm dừng 0.5s giữa các lần chuyển chân
  }

  Serial.println("\n==============================================");
  Serial.println("  ĐÃ KIỂM TRA HẾT CÁC CHÂN. BẮT ĐẦU LẠI CHU KỲ...");
  Serial.println("==============================================\n");
  delay(2000);
}