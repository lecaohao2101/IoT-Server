#ifndef WIFI_CONFIG_H
#define WIFI_CONFIG_H

#include <WiFi.h>
#include <WiFiManager.h>
#include <LiquidCrystal_I2C.h>

/**
 * @brief Khởi tạo WiFi với Pop-up cấu hình Captive Portal
 * @param apName Tên WiFi AP phát ra
 * @param apPassword Mật khẩu AP
 * @param timeoutSec Thời gian chờ nhập thông tin (giây)
 * @param lcd Con trỏ tới màn hình LCD để hiển thị trạng thái (Tùy chọn)
 */
inline bool initWiFiPortal(const char* apName = "ESP32_SmartHome_AP", 
                           const char* apPassword = "12345678", 
                           int timeoutSec = 180,
                           LiquidCrystal_I2C* lcd = nullptr) {
  WiFiManager wm;
  wm.setConfigPortalTimeout(timeoutSec);

  if (lcd) {
    lcd->clear();
    lcd->setCursor(0, 0);
    lcd->print("WiFi AP Mode:");
    lcd->setCursor(0, 1);
    lcd->print(apName);
  }

  Serial.println("[WiFi] Đang kiểm tra kết nối...");
  bool res = wm.autoConnect(apName, apPassword);

  if (!res) {
    Serial.println("[WiFi] Hết thời gian chờ -> Chạy chế độ Offline!");
    if (lcd) {
      lcd->clear();
      lcd->setCursor(0, 0);
      lcd->print("WiFi: Offline");
      delay(1500);
    }
    return false;
  }

  Serial.println("[WiFi] Đã kết nối Wi-Fi thành công!");
  Serial.printf("[WiFi] IP: %s | RSSI: %d dBm | Gateway: %s\n", 
                WiFi.localIP().toString().c_str(), WiFi.RSSI(), WiFi.gatewayIP().toString().c_str());
  Serial.printf("[WiFi] Server Target: %s\n", SERVER_API_URL);

  if (lcd) {
    lcd->clear();
    lcd->setCursor(0, 0);
    lcd->print("WiFi Connected!");
    lcd->setCursor(0, 1);
    lcd->print(WiFi.localIP().toString());
    delay(2000);
  }

  return true;
}

/**
 * @brief Xóa WiFi đã lưu
 */
inline void resetWiFiSaved() {
  WiFiManager wm;
  wm.resetSettings();
  Serial.println("[WiFi] Đã xóa thông tin WiFi cũ!");
}

#endif