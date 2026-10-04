#ifndef STATUS_MANAGER_H
#define STATUS_MANAGER_H

#include <Arduino.h>

struct SystemStatus {
  float temp = -999.0;
  float hum = -999.0;
  float dist = -999.0;
  int sound_level = -999;
};

extern SystemStatus globalStatus;

void sendPartialStatus(const String& jsonPayload);

// Thêm tham số isDhtValid vào hàm kiểm tra
void checkAndSendSensors(float newTemp, float newHum, bool isDhtValid, float newDist, int newSound);

#endif