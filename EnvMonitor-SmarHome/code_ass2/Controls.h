#ifndef CONTROLS_H
#define CONTROLS_H

#include <Arduino.h>   // String, byte... dung trong header nay

void initControls();
void testBuzzerAndLEDs();
void handleButtons();
void updateServos();
void refreshLCDDevices(bool wifiOk);

// value: vị trí rèm 0..100% (thiết bị rèm) — bỏ qua với các thiết bị khác.
// targetTemp: nhiệt độ điều hòa người dùng yêu cầu, chỉ để hiện lên LCD.
void setDeviceActuator(const String& deviceId, bool power, int value = -1, int targetTemp = -1);
void syncAllFromStates(int lr_main, int lr_sofa, int kit_main, int bed_main, int bed_side, int study, int balcony, int wc, int lr_curtain = -1, int bed_curtain = -1);
void publishAllDeviceStates();

// Điều hòa KHÔNG có phần cứng nào đi kèm: nhiệt độ ở đây thuần tuý là con số
// người dùng vừa yêu cầu, giữ lại để LCD hiển thị và để báo ngược lên MQTT.
int getLivingRoomACTemp();
int getBedroomACTemp();
bool getLivingRoomACState();
bool getBedroomACState();

// Vị trí rèm hiện tại (0 = đóng hẳn, 100 = mở hết) — chính là góc servo đang giữ.
int getLivingRoomCurtainPosition();
int getBedroomCurtainPosition();

#endif
