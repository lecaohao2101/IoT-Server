#include "MqttHandler.h"
#include "Config.h"
#include "Controls.h"
#include <WiFi.h>
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
  t.toLowerCase();
  p.trim();
  String pLower = p;
  pLower.toLowerCase();

  // Nhận diện trạng thái power: hỗ trợ cả bare string ("on", "off", "1", "0", "true", "false")
  // lẫn JSON ({"set":{"power":"on"}}, {"power":"on"}, {"state":"open"}, v.v.)
  bool powerOn = (pLower == "on" || pLower == "\"on\"" || pLower == "true" || pLower == "1" ||
                  pLower.indexOf("\"power\":\"on\"") != -1 || pLower.indexOf("\"power\": \"on\"") != -1 ||
                  pLower.indexOf("\"state\":\"on\"") != -1 || pLower.indexOf("\"state\": \"on\"") != -1 ||
                  pLower.indexOf("\"power\":true") != -1 || pLower.indexOf("\"power\": true") != -1 ||
                  pLower.indexOf("\"state\":\"open\"") != -1);

  bool powerOff = (pLower == "off" || pLower == "\"off\"" || pLower == "false" || pLower == "0" ||
                   pLower.indexOf("\"power\":\"off\"") != -1 || pLower.indexOf("\"power\": \"off\"") != -1 ||
                   pLower.indexOf("\"state\":\"off\"") != -1 || pLower.indexOf("\"state\": \"off\"") != -1 ||
                   pLower.indexOf("\"power\":false") != -1 || pLower.indexOf("\"power\": false") != -1 ||
                   pLower.indexOf("\"state\":\"closed\"") != -1);

  // Chỉ thay đổi power khi bản tin thực sự mang lệnh power, tránh bị tắt nhầm khi nhận bản tin khác
  bool hasPowerCmd = (powerOn || powerOff);
  bool power = powerOn;

  int val = -1;
  int angleIdx = pLower.indexOf("\"vane_angle\":");
  if (angleIdx != -1) {
    val = pLower.substring(angleIdx + 13).toInt();
  }

  const char* pState = power ? "{\"power\":\"on\"}" : "{\"power\":\"off\"}";

  // Khớp topic thiết bị, kích hoạt phần cứng và phản hồi trạng thái ngược lên MQTT
  if (t.indexOf("living_room_light") != -1) {
    if (hasPowerCmd) {
      setDeviceActuator("living_room_light", power);
      mqttPublishState("living_room", "living_room_light", pState);
    }
  } else if (t.indexOf("living_room_sofa_light") != -1) {
    if (hasPowerCmd) {
      setDeviceActuator("living_room_sofa_light", power);
      mqttPublishState("living_room", "living_room_sofa_light", pState);
    }
  } else if (t.indexOf("kitchen_light") != -1) {
    if (hasPowerCmd) {
      setDeviceActuator("kitchen_light", power);
      mqttPublishState("kitchen", "kitchen_light", pState);
    }
  } else if (t.indexOf("bedroom_light") != -1) {
    if (hasPowerCmd) {
      setDeviceActuator("bedroom_light", power);
      mqttPublishState("bedroom", "bedroom_light", pState);
    }
  } else if (t.indexOf("bedroom_side_light") != -1) {
    if (hasPowerCmd) {
      setDeviceActuator("bedroom_side_light", power);
      mqttPublishState("bedroom", "bedroom_side_light", pState);
    }
  } else if (t.indexOf("study_light") != -1) {
    if (hasPowerCmd) {
      setDeviceActuator("study_light", power);
      mqttPublishState("bedroom", "study_light", pState);
    }
  } else if (t.indexOf("balcony_light") != -1) {
    if (hasPowerCmd) {
      setDeviceActuator("balcony_light", power);
      mqttPublishState("balcony", "balcony_light", pState);
    }
  } else if (t.indexOf("bathroom_light") != -1) {
    if (hasPowerCmd) {
      setDeviceActuator("bathroom_light", power);
      mqttPublishState("bathroom", "bathroom_light", pState);
    }
  } else if (t.indexOf("living_room_ac") != -1) {
    if (hasPowerCmd || val >= 0) {
      setDeviceActuator("living_room_ac", power, val);
      int angle = (val >= 0) ? val : (power ? SERVO_ANGLE_OPEN : SERVO_ANGLE_CLOSE);
      String acJson = "{\"power\":\"" + String(power ? "on" : "off") + "\",\"vane_angle\":" + String(angle) + "}";
      mqttPublishState("living_room", "living_room_ac", acJson.c_str());
    }
  } else if (t.indexOf("bedroom_ac") != -1) {
    if (hasPowerCmd || val >= 0) {
      setDeviceActuator("bedroom_ac", power, val);
      int angle = (val >= 0) ? val : (power ? SERVO_ANGLE_OPEN : SERVO_ANGLE_CLOSE);
      String acJson = "{\"power\":\"" + String(power ? "on" : "off") + "\",\"vane_angle\":" + String(angle) + "}";
      mqttPublishState("bedroom", "bedroom_ac", acJson.c_str());
    }
  }
}

void mqttPublishState(const char* room, const char* deviceId, const char* jsonPayload) {
  if (mqttClient.connected()) {
    String topic = "home/" + String(room) + "/" + String(deviceId) + "/state";
    mqttClient.publish(topic.c_str(), jsonPayload, true); // true = Retained message
    Serial.printf("[MQTT-TX] %s -> %s (Retained)\n", topic.c_str(), jsonPayload);
  }
}

void mqttPublishDeviceAvailability(const char* room, const char* deviceId, bool online) {
  if (mqttClient.connected()) {
    String topic = "home/" + String(room) + "/" + String(deviceId) + "/availability";
    mqttClient.publish(topic.c_str(), online ? "online" : "offline", true);
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
      const char* willTopic = "home/esp32s3/availability";
      const char* willMsg = "offline";
      // Kết nối với Last Will and Testament (LWT)
      if (mqttClient.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS, willTopic, 1, true, willMsg)) {
        Serial.printf("[MQTT] ===> KẾT NỐI BROKER THÀNH CÔNG! ClientID: %s\n", MQTT_CLIENT_ID);
        // Lắng nghe lệnh điều khiển từ server & mobile app
        mqttClient.subscribe("home/+/+/set");
        mqttClient.subscribe("home/+/+/set/+");
        Serial.println("[MQTT] Đã Subscribe: 'home/+/+/set' & 'home/+/+/set/+'");

        // Báo trạng thái online lên broker (Retained) cho cả esp32s3 và esp32
        mqttClient.publish("home/esp32s3/availability", "online", true);
        mqttClient.publish("home/esp32/availability", "online", true);
        Serial.println("[MQTT] Đã Publish LWT: home/esp32s3/availability & home/esp32/availability -> 'online'");

        // Công bố availability cho từng thiết bị
        mqttPublishDeviceAvailability("living_room", "living_room_light", true);
        mqttPublishDeviceAvailability("living_room", "living_room_sofa_light", true);
        mqttPublishDeviceAvailability("kitchen", "kitchen_light", true);
        mqttPublishDeviceAvailability("bedroom", "bedroom_light", true);
        mqttPublishDeviceAvailability("bedroom", "bedroom_side_light", true);
        mqttPublishDeviceAvailability("bedroom", "study_light", true);
        mqttPublishDeviceAvailability("balcony", "balcony_light", true);
        mqttPublishDeviceAvailability("bathroom", "bathroom_light", true);
        mqttPublishDeviceAvailability("living_room", "living_room_ac", true);
        mqttPublishDeviceAvailability("bedroom", "bedroom_ac", true);
        Serial.println("[MQTT] ESP32 sẵn sàng nhận trạng thái đồng bộ từ App!");
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
