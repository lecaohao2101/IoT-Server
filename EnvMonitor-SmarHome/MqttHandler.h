#ifndef MQTT_HANDLER_H
#define MQTT_HANDLER_H

#include <Arduino.h>

// Khởi tạo kết nối MQTT bảo mật TLS đến HiveMQ Cloud
void setupMQTT();

// Xử lý duy trì kết nối và nhận lệnh (gọi trong loop())
void loopMQTT();

// Kiểm tra trạng thái kết nối MQTT
bool isMqttConnected();

#endif
