#include "StatusManager.h"
#include "Config.h"
#include "Controls.h"
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

SystemStatus globalStatus;

void sendPartialStatus(const String& jsonPayload) {
  if (WiFi.status() != WL_CONNECTED) return;

  HTTPClient http;
  WiFiClientSecure secureClient;
  bool isHttps = String(SERVER_API_URL).startsWith("https");

  if (isHttps) {
    secureClient.setInsecure();
    http.begin(secureClient, String(SERVER_API_URL) + "/update-status");
  } else {
    http.begin(String(SERVER_API_URL) + "/update-status");
  }
  http.addHeader("Content-Type", "application/json");

  int httpCode = http.POST(jsonPayload);
  if (httpCode > 0) {
    if (httpCode == HTTP_CODE_OK) {
      String response = http.getString();
      int statesPos = response.indexOf("\"states\":");
      if (statesPos != -1) {
        auto parseVal = [&](const char* key) -> int {
          int keyPos = response.indexOf(key, statesPos);
          if (keyPos == -1) return -1;
          int colPos = response.indexOf(':', keyPos);
          if (colPos == -1) return -1;
          return response.substring(colPos + 1).toInt();
        };
        int lr_m  = parseVal("\"lr_main\"");
        int lr_s  = parseVal("\"lr_sofa\"");
        int kit   = parseVal("\"kit_main\"");
        int bed_m = parseVal("\"bed_main\"");
        int bed_s = parseVal("\"bed_side\"");
        int stdy  = parseVal("\"study\"");
        int bal   = parseVal("\"balcony\"");
        int wc_l  = parseVal("\"wc\"");
        int lr_a  = parseVal("\"lr_angle\"");
        int bed_a = parseVal("\"bed_angle\"");
        
        Serial.printf("[HTTP-RX] 200 OK | Đồng bộ: LR[M:%d,S:%d,AC:%d°] KIT[%d] BED[M:%d,S:%d,AC:%d°] BAL[%d] WC[%d]\n",
                      lr_m, lr_s, lr_a, kit, bed_m, bed_s, bed_a, bal, wc_l);
        syncAllFromStates(lr_m, lr_s, kit, bed_m, bed_s, stdy, bal, wc_l, lr_a, bed_a);
      } else {
        Serial.printf("[HTTP-RX] 200 OK | Phản hồi: %s\n", response.c_str());
      }
    } else {
      Serial.printf("[HTTP-RX] Mã HTTP không mong muốn: %d\n", httpCode);
    }
  } else {
    Serial.printf("[HTTP-ERR] Lỗi kết nối Server: %s (code: %d)\n", http.errorToString(httpCode).c_str(), httpCode);
  }
  http.end();
}

void checkAndSendSensors(float newTemp, float newHum, bool isDhtValid, float newDist, int newSound) {
  String json = "{\"sensors\":{";
  bool hasChange = false;

  // 1. Chỉ cập nhật & gửi Nhiệt độ / Độ ẩm NẾU DHT11 đọc hợp lệ
  if (isDhtValid) {
    if (globalStatus.temp == -999.0 || abs(newTemp - globalStatus.temp) >= 0.5) {
      globalStatus.temp = newTemp;
      json += "\"temp\":" + String(newTemp, 1) + ",";
      hasChange = true;
    }
    if (globalStatus.hum == -999.0 || abs(newHum - globalStatus.hum) >= 2.0) {
      globalStatus.hum = newHum;
      json += "\"hum\":" + String(newHum, 1) + ",";
      hasChange = true;
    }
  }

  // 2. Chỉ gửi Cảm biến Siêu âm khi khoảng cách hợp lệ
  if (newDist >= 0) {
    if (globalStatus.dist == -999.0 || abs(newDist - globalStatus.dist) >= 2.0) {
      globalStatus.dist = newDist;
      json += "\"distance_cm\":" + String(newDist, 1) + ",";
      hasChange = true;
    }
  }

  // 3. Cảm biến Âm thanh INMP441
  if (globalStatus.sound_level == -999 || abs(newSound - globalStatus.sound_level) >= 15) {
    globalStatus.sound_level = newSound;
    json += "\"sound_level\":" + String(newSound) + ",";
    hasChange = true;
  }

  if (hasChange) {
    if (json.endsWith(",")) {
      json.remove(json.length() - 1);
    }
    json += "}}";
    sendPartialStatus(json);
  }
}