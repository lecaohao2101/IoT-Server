#ifndef MQTT_HANDLER_H
#define MQTT_HANDLER_H

#include <Arduino.h>
#include <PubSubClient.h>

// Khởi tạo kết nối MQTT bảo mật TLS đến HiveMQ Cloud
void setupMQTT();

// Xử lý duy trì kết nối và nhận lệnh (gọi trong loop())
void loopMQTT();

// Kiểm tra trạng thái kết nối MQTT
bool isMqttConnected();

// Publish trạng thái thiết bị lên MQTT (Retained message)
void mqttPublishState(const char* room, const char* deviceId, const char* jsonPayload);

// Publish trạng thái trực tuyến của thiết bị
void mqttPublishDeviceAvailability(const char* room, const char* deviceId, bool online);

#endif
