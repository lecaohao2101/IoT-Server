#include "StatusManager.h"
#include "Config.h"
#include "Controls.h"
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

SystemStatus globalStatus;

// Mot ket noi TLS duy nhat, dung lai cho moi lan gui. Truoc day moi lan goi deu
// tao WiFiClientSecure cuc bo, tuc bat tay TLS lai tu dau -- 1 den 3 giay CHAN
// loop(), nen loopMQTT() khong chay va lenh bat den den muon han.
static WiFiClientSecure statusClient;
static HTTPClient statusHttp;
static bool statusClientReady = false;

void sendPartialStatus(const String& jsonPayload) {
  if (WiFi.status() != WL_CONNECTED) return;

  bool isHttps = String(SERVER_API_URL).startsWith("https");
  if (isHttps && !statusClientReady) {
    statusClient.setInsecure();
    statusClient.setTimeout(4);
    statusClientReady = true;
  }

  String url = String(SERVER_API_URL) + "/update-status";
  bool ok = isHttps ? statusHttp.begin(statusClient, url) : statusHttp.begin(url);
  if (!ok) {
    Serial.println("[HTTP-ERR] Khong mo duoc ket noi trang thai.");
    return;
  }

  statusHttp.setReuse(true);          // giu nguyen phien TLS cho lan sau
  statusHttp.setConnectTimeout(4000);
  statusHttp.setTimeout(4000);
  statusHttp.addHeader("Content-Type", "application/json");

  unsigned long t0 = millis();
  int httpCode = statusHttp.POST(jsonPayload);
  unsigned long took = millis() - t0;

  if (httpCode == HTTP_CODE_OK) {
    // CO Y khong doc "states" roi goi syncAllFromStates() o day nua.
    //
    // Phan hoi nay la anh chup trang thai tai thoi diem server nhan request. Neu
    // mot lenh bat den vua toi qua MQTT trong luc request dang bay, anh chup cu
    // se ghi de nguoc lai toan bo chan LED -> den vua bat lai tat, roi su kien
    // sau bat len -> nhap nhay. Duong lenh chinh thuc la MQTT va WebSocket
    // state.changed; HTTP chi de DAY du lieu cam bien len, khong phai de KEO
    // trang thai ve.
    if (took > 1500) {
      Serial.printf("[HTTP-TX] Gui trang thai OK nhung cham: %lu ms\n", took);
    }
  } else if (httpCode > 0) {
    Serial.printf("[HTTP-RX] Ma HTTP khong mong muon: %d\n", httpCode);
  } else {
    Serial.printf("[HTTP-ERR] Loi ket noi Server: %s (code: %d)\n",
                  statusHttp.errorToString(httpCode).c_str(), httpCode);
    statusClientReady = false;      // ep bat tay lai o lan sau
  }
  statusHttp.end();
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

  // 3. Cam bien Am thanh INMP441.
  // Nguong 15 qua nhay: chi can noi gan mic la muc am thanh nhay lien tuc, lam
  // moi giay lai bung mot request HTTPS chan loop(). Nang nguong va coi day la
  // thong tin phu, khong dang de danh thuc duong mang.
  if (globalStatus.sound_level == -999 || abs(newSound - globalStatus.sound_level) >= 40) {
    globalStatus.sound_level = newSound;
    json += "\"sound_level\":" + String(newSound) + ",";
    hasChange = true;
  }

  // Khoang cach toi thieu giua hai lan gui cam bien. Nut bam khong di qua day
  // nen thao tac tay van tuc thi.
  static unsigned long lastSensorPost = 0;
  const unsigned long SENSOR_POST_MIN_MS = 15000;

  if (hasChange) {
    unsigned long now = millis();
    if (now - lastSensorPost < SENSOR_POST_MIN_MS) return;
    lastSensorPost = now;

    if (json.endsWith(",")) {
      json.remove(json.length() - 1);
    }
    json += "}}";
    sendPartialStatus(json);
  }
}