#ifndef WIFI_CONFIG_H
#define WIFI_CONFIG_H

#include "Config.h"   // WIFI_SSID / WIFI_PASSWORD
#include <WiFi.h>

#if __has_include(<WiFiManager.h>)
#include <WiFiManager.h>
#define HAVE_WIFI_MANAGER 1
#else
#define HAVE_WIFI_MANAGER 0
#endif

#include <LiquidCrystal_I2C.h>

/**
 * @brief Khởi tạo WiFi với Pop-up cấu hình Captive Portal (hoặc kết nối trực tiếp nếu không có WiFiManager)
 * @param apName Tên WiFi AP phát ra
 * @param apPassword Mật khẩu AP
 * @param timeoutSec Thời gian chờ nhập thông tin (giây)
 * @param lcd Con trỏ tới màn hình LCD để hiển thị trạng thái (Tùy chọn)
 */
inline bool initWiFiPortal(const char* apName = "ESP32_SmartHome_AP", 
                           const char* apPassword = "12345678", 
                           int timeoutSec = 180,
                           LiquidCrystal_I2C* lcd = nullptr) {
#if HAVE_WIFI_MANAGER
  WiFiManager wm;
  wm.setConfigPortalTimeout(timeoutSec);
#endif

  if (lcd) {
    lcd->clear();
    lcd->setCursor(0, 0);
    lcd->print("WiFi Connecting");
    lcd->setCursor(0, 1);
    lcd->print(apName);
  }

  Serial.println("[WiFi] Dang kiem tra ket noi...");

  // Uu tien mang cau hinh san trong Config.h.
  if (strlen(WIFI_SSID) > 0) {
    Serial.printf("[WiFi] Thu ket noi thang toi '%s'...\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    delay(150);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    unsigned long t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < WIFI_CONNECT_TIMEOUT_MS) {
      delay(500);
      Serial.print(".");
    }
    Serial.println();
    if (WiFi.status() != WL_CONNECTED) {
      int st = WiFi.status();
      Serial.printf("[WiFi] Khong vao duoc mang '%s' (Ma trang thai: %d)\n", WIFI_SSID, st);
      if (st == 1) { // WL_NO_SSID_AVAIL
        Serial.printf("[WiFi] ==> LỖI: Không tìm thấy mạng '%s'!\n", WIFI_SSID);
        Serial.println("[WiFi] ==> LƯU Ý: ESP32 chỉ bắt được Wi-Fi 2.4GHz. Nếu phát từ iPhone, phải bật 'Tối đa hóa khả năng tương thích'.");
      } else if (st == 4) { // WL_CONNECT_FAILED
        Serial.println("[WiFi] ==> LỖI: Sai mật khẩu Wi-Fi!");
      }

      // "Không vào được mạng" có hai nguyên nhân hoàn toàn khác nhau: mạng không
      // tồn tại trước mắt ESP32 (sai tên, hoặc router chỉ phát 5 GHz), và mạng
      // có đó nhưng không cho vào (sai mật khẩu, lọc MAC). Một lần quét phân biệt
      // dứt điểm hai thứ đó, đỡ phải đoán.
      Serial.println("[WiFi] Quét các mạng 2.4 GHz mà ESP32 nhìn thấy:");
      int found = WiFi.scanNetworks();
      if (found <= 0) {
        Serial.println("[WiFi]   (không thấy mạng nào -- kiểm tra ăng-ten hoặc nguồn cấp)");
      }
      bool target_visible = false;
      for (int i = 0; i < found; i++) {
        bool match = (WiFi.SSID(i) == String(WIFI_SSID));
        if (match) target_visible = true;
        Serial.printf("[WiFi]   %2d. %-32s %4d dBm %s\n", i + 1,
                      WiFi.SSID(i).c_str(), WiFi.RSSI(i), match ? "<== MANG CAN TIM" : "");
      }
      if (found > 0 && !target_visible) {
        Serial.printf("[WiFi] ==> Khong thay '%s' trong danh sach tren.\n", WIFI_SSID);
        Serial.println("[WiFi] ==> Kiem tra chinh ta tung ky tu, hoac router dang phat o bang 5 GHz.");
        Serial.println("[WiFi] ==> ESP32 chi bat duoc 2.4 GHz.");
      } else if (target_visible) {
        Serial.println("[WiFi] ==> Mang CO TON TAI va ESP32 thay duoc.");
        Serial.println("[WiFi] ==> Vay van de nam o mat khau hoac o router (loc MAC, gioi han so thiet bi).");
      }
      WiFi.scanDelete();
    }
  }

#if HAVE_WIFI_MANAGER
  bool res = (WiFi.status() == WL_CONNECTED) || wm.autoConnect(apName, apPassword);
#else
  bool res = (WiFi.status() == WL_CONNECTED);
#endif

  if (!res) {
    Serial.println("[WiFi] Khong ket noi duoc WiFi -> Chạy chế độ Offline!");
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
 * @brief Giữ Wi-Fi sống sau khi setup(). Gọi mỗi vòng loop().
 *
 * Không có hàm này thì WiFi.begin() chỉ chạy đúng một lần lúc khởi động: router
 * khởi động lại, hoặc mang board ra khỏi vùng phủ sóng một phút, là offline cho
 * tới khi có người bấm nút reset. Trả về true khi đang có mạng.
 */
inline bool wifiEnsureConnected() {
  static unsigned long lastAttempt = 0;
  static int lastState = -1;

  bool connected = (WiFi.status() == WL_CONNECTED);

  // Chỉ nói khi trạng thái ĐỔI. In mỗi vòng loop sẽ nhấn chìm mọi log khác.
  int state = connected ? 1 : 0;
  if (state != lastState) {
    lastState = state;
    if (connected) {
      Serial.printf("[WiFi] Đã kết nối lại | IP: %s | RSSI: %d dBm\n",
                    WiFi.localIP().toString().c_str(), WiFi.RSSI());
    } else {
      Serial.println("[WiFi] Mất kết nối. Sẽ thử lại mỗi 10 giây.");
    }
  }

  if (connected) return true;
  if (strlen(WIFI_SSID) == 0) return false;

  unsigned long now = millis();
  if (now - lastAttempt < 10000) return false;
  lastAttempt = now;

  Serial.printf("[WiFi] Thử kết nối lại '%s'...\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  return false;
}

/**
 * @brief Xóa WiFi đã lưu
 */
inline void resetWiFiSaved() {
#if HAVE_WIFI_MANAGER
  WiFiManager wm;
  wm.resetSettings();
#else
  WiFi.disconnect(true, true);
#endif
  Serial.println("[WiFi] Đã xóa thông tin WiFi cũ!");
}

#endif