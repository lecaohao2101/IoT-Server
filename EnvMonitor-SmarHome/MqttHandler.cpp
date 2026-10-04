#include "MqttHandler.h"
#include "Config.h"
#include "Controls.h"
#include <WiFi.h>

#if __has_include(<PubSubClient.h>)
#include <WiFiClientSecure.h>
#include <PubSubClient.h>

static WiFiClientSecure espTlsClient;
static PubSubClient mqttClient(espTlsClient);
static unsigned long lastMqttRetry = 0;

static void mqttCallback(char* topic, byte* payload, unsigned int length) {
  char msg[256];
  if (length >= sizeof(msg)) length = sizeof(msg) - 1;
  memcpy(msg, payload, length);
  msg[length] = '\0';

  Serial.printf("[MQTT RX] %s -> %s\n", topic, msg);

  String t = String(topic);
  String p = String(msg);

  // Nhận diện trạng thái power: "on" / "off" / true / false
  bool powerOn = (p.indexOf("\"on\"") != -1 || p.indexOf("true") != -1 || p.indexOf(":\"on\"") != -1);
  bool powerOff = (p.indexOf("\"off\"") != -1 || p.indexOf("false") != -1 || p.indexOf(":\"off\"") != -1);
  bool power = powerOn ? true : (powerOff ? false : false);

  int val = -1;
  int angleIdx = p.indexOf("\"vane_angle\":");
  if (angleIdx != -1) {
    val = p.substring(angleIdx + 13).toInt();
  }

  // Khớp topic thiết bị và kích hoạt phần cứng thực tế
  if (t.indexOf("living_room_light") != -1) {
    setDeviceActuator("living_room_light", power);
  } else if (t.indexOf("living_room_sofa_light") != -1) {
    setDeviceActuator("living_room_sofa_light", power);
  } else if (t.indexOf("kitchen_light") != -1) {
    setDeviceActuator("kitchen_light", power);
  } else if (t.indexOf("bedroom_light") != -1) {
    setDeviceActuator("bedroom_light", power);
  } else if (t.indexOf("bedroom_side_light") != -1) {
    setDeviceActuator("bedroom_side_light", power);
  } else if (t.indexOf("study_light") != -1) {
    setDeviceActuator("study_light", power);
  } else if (t.indexOf("balcony_light") != -1) {
    setDeviceActuator("balcony_light", power);
  } else if (t.indexOf("bathroom_light") != -1) {
    setDeviceActuator("bathroom_light", power);
  } else if (t.indexOf("living_room_ac") != -1) {
    setDeviceActuator("living_room_ac", power, val);
  } else if (t.indexOf("bedroom_ac") != -1) {
    setDeviceActuator("bedroom_ac", power, val);
  }
}

void setupMQTT() {
  espTlsClient.setInsecure(); // Chấp nhận TLS chứng chỉ từ HiveMQ Cloud không cần nạp CA cert thủ công
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setCallback(mqttCallback);
  mqttClient.setBufferSize(512);
  Serial.println("[MQTT] Đã khởi tạo cấu hình MQTT TLS (HiveMQ Cloud).");
}

void loopMQTT() {
  if (WiFi.status() != WL_CONNECTED) return;

  if (!mqttClient.connected()) {
    unsigned long now = millis();
    if (now - lastMqttRetry > 5000) {
      lastMqttRetry = now;
      Serial.print("[MQTT] Đang kết nối HiveMQ Cloud (TLS 8883)...");
      if (mqttClient.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS)) {
        Serial.println(" THÀNH CÔNG!");
        // Lắng nghe lệnh điều khiển từ server & mobile app
        mqttClient.subscribe("home/+/+/set");
        mqttClient.subscribe("home/+/+/set/+");
        // Báo trạng thái online lên broker
        mqttClient.publish("home/esp32/availability", "online", true);
      } else {
        Serial.printf(" Thất bại, rc=%d (sẽ thử lại sau 5s)\n", mqttClient.state());
      }
    }
  } else {
    mqttClient.loop();
  }
}

bool isMqttConnected() {
  return mqttClient.connected();
}

#else

void setupMQTT() {
  Serial.println("[MQTT] Ghi chú: Cài đặt thư viện 'PubSubClient' trong Arduino IDE để bật MQTT Real-time.");
  Serial.println("[MQTT] Hiện tại hệ thống đang tự động đồng bộ 2 chiều qua HTTP REST API.");
}

void loopMQTT() {}

bool isMqttConnected() {
  return false;
}

#endif
