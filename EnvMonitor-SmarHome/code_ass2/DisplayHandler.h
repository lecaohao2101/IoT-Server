#ifndef DISPLAY_HANDLER_H
#define DISPLAY_HANDLER_H

#include <LiquidCrystal_I2C.h>

extern LiquidCrystal_I2C lcd_lr;
extern LiquidCrystal_I2C lcd_bed;

void initLCDs();

// Cập nhật trạng thái thiết bị thời gian thực (Dòng 0).
// `acTemp` là nhiệt độ NGƯỜI DÙNG VỪA YÊU CẦU, không phải số đo của cảm biến
// nào: điều hòa trong nhà này chỉ tồn tại trên màn hình. Nhiệt độ thật của
// phòng nằm ở dòng 1, do DHT11 đo.
void updateLCDLivingRoomDevices(bool lightOn, bool acOn, int acTemp, bool wifiOk);
void updateLCDBedroomDevices(bool lightOn, bool acOn, int acTemp, bool extraLight = false);

// Cập nhật cảm biến (Dòng 1)
void updateLCDLivingRoomSensors(float temp, float hum, bool isDhtValid);
void updateLCDBedroomSensors(float dist, int soundLevel);

// Hiển thị thông báo tức thời khi bấm nút hoặc có lệnh (tự xóa sau timeout)
void showLCDNotify(int screen, const char* msg, unsigned long durationMs = 2000);
void checkLCDNotifyTimeout();

// Các hàm tương thích ngược
void updateLCDLivingRoom(float temp, float hum, bool isDhtValid);
void updateLCDBedroom(float dist, int soundLevel);
void updateLCDWiFiStatus(bool connected);

#endif
