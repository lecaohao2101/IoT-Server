#ifndef CONTROLS_H
#define CONTROLS_H

#include <Arduino.h>   // String, byte... dung trong header nay

void initControls();
void testBuzzerAndLEDs();
void handleButtons();
void updateServos();
void refreshLCDDevices(bool wifiOk);
void setDeviceActuator(const String& deviceId, bool power, int value = -1, int targetTemp = -1);
void syncAllFromStates(int lr_main, int lr_sofa, int kit_main, int bed_main, int bed_side, int study, int balcony, int wc, int lr_angle = -1, int bed_angle = -1);
void publishAllDeviceStates();

// Hàm lấy thông tin nhiệt độ và trạng thái điều hòa thời gian thực
int getLivingRoomACTemp();
int getBedroomACTemp();
bool getLivingRoomACState();
bool getBedroomACState();

#endif
