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

  const char* pState = power ? "{\"power\":\"on\"}" : "{\"power\":\"off\"}";

  // Khớp topic thiết bị, kích hoạt phần cứng và phản hồi trạng thái ngược lên MQTT
  if (t.indexOf("living_room_light") != -1) {
    setDeviceActuator("living_room_light", power);
    mqttPublishState("living_room", "living_room_light", pState);
  } else if (t.indexOf("living_room_sofa_light") != -1) {
    setDeviceActuator("living_room_sofa_light", power);
    mqttPublishState("living_room", "living_room_sofa_light", pState);
  } else if (t.indexOf("kitchen_light") != -1) {
    setDeviceActuator("kitchen_light", power);
    mqttPublishState("kitchen", "kitchen_light", pState);
  } else if (t.indexOf("bedroom_light") != -1) {
    setDeviceActuator("bedroom_light", power);
    mqttPublishState("bedroom", "bedroom_light", pState);
  } else if (t.indexOf("bedroom_side_light") != -1) {
    setDeviceActuator("bedroom_side_light", power);
    mqttPublishState("bedroom", "bedroom_side_light", pState);
  } else if (t.indexOf("study_light") != -1) {
    setDeviceActuator("study_light", power);
    mqttPublishState("bedroom", "study_light", pState);
  } else if (t.indexOf("balcony_light") != -1) {
    setDeviceActuator("balcony_light", power);
    mqttPublishState("balcony", "balcony_light", pState);
  } else if (t.indexOf("bathroom_light") != -1) {
    setDeviceActuator("bathroom_light", power);
    mqttPublishState("bathroom", "bathroom_light", pState);
  } else if (t.indexOf("living_room_ac") != -1) {
    setDeviceActuator("living_room_ac", power, val);
    int angle = (val >= 0) ? val : (power ? SERVO_ANGLE_OPEN : SERVO_ANGLE_CLOSE);
    String acJson = "{\"power\":\"" + String(power ? "on" : "off") + "\",\"vane_angle\":" + String(angle) + "}";
    mqttPublishState("living_room", "living_room_ac", acJson.c_str());
  } else if (t.indexOf("bedroom_ac") != -1) {
    setDeviceActuator("bedroom_ac", power, val);
    int angle = (val >= 0) ? val : (power ? SERVO_ANGLE_OPEN : SERVO_ANGLE_CLOSE);
    String acJson = "{\"power\":\"" + String(power ? "on" : "off") + "\",\"vane_angle\":" + String(angle) + "}";
    mqttPublishState("bedroom", "bedroom_ac", acJson.c_str());
  }
}

void mqttPublishState(const char* room, const char* deviceId, const char* jsonPayload) {
  if (mqttClient.connected()) {
    String topic = "home/" + String(room) + "/" + String(deviceId) + "/state";
    mqttClient.publish(topic.c_str(), jsonPayload, true); // true = Retained message
    Serial.printf("[MQTT-TX] %s -> %s (Retained)\n", topic.c_str(), jsonPayload);
  }
}

void setupMQTT() {
  espTlsClient.setInsecure(); // Chấp nhận TLS chứng chỉ từ HiveMQ Cloud không cần nạp CA cert thủ công
  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setCallback(mqttCallback);
  mqttClient.setBufferSize(512);
  Serial.printf("[MQTT] Đã cấu hình Broker: %s:%d (TLS)\n", MQTT_BROKER, MQTT_PORT);
}

void loopMQTT() {
  if (WiFi.status() != WL_CONNECTED) return;

  if (!mqttClient.connected()) {
    unsigned long now = millis();
    if (now - lastMqttRetry > 5000) {
      lastMqttRetry = now;
      Serial.printf("[MQTT] Đang kết nối HiveMQ Cloud TLS 8883 (User: %s)...\n", MQTT_USER);
      const char* willTopic = "home/esp32/availability";
      const char* willMsg = "offline";
      // Kết nối với Last Will and Testament (LWT)
      if (mqttClient.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS, willTopic, 1, true, willMsg)) {
        Serial.printf("[MQTT] ===> KẾT NỐI BROKER THÀNH CÔNG! ClientID: %s\n", MQTT_CLIENT_ID);
        // Lắng nghe lệnh điều khiển từ server & mobile app
        mqttClient.subscribe("home/+/+/set");
        mqttClient.subscribe("home/+/+/set/+");
        Serial.println("[MQTT] Đã Subscribe: 'home/+/+/set' & 'home/+/+/set/+'");
        // Báo trạng thái online lên broker (Retained)
        mqttClient.publish("home/esp32/availability", "online", true);
        Serial.println("[MQTT] Đã Publish LWT: home/esp32/availability -> 'online'");
      } else {
        Serial.printf("[MQTT-ERR] Kết nối thất bại, state rc=%d (sẽ thử lại sau 5s)\n", mqttClient.state());
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

void mqttPublishState(const char* room, const char* deviceId, const char* jsonPayload) {}

#endif
