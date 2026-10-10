#ifndef DISPLAY_HANDLER_H
#define DISPLAY_HANDLER_H

#include <LiquidCrystal_I2C.h>

extern LiquidCrystal_I2C lcd_lr;
extern LiquidCrystal_I2C lcd_bed;

void initLCDs();
void updateLCDLivingRoom(float temp, float hum, bool isDhtValid);
void updateLCDBedroom(float dist, int soundLevel);

// Dòng 0 của LCD phòng khách. Hai hàm update ở trên chỉ ghi dòng 1, nên nếu
// không có hàm này thì dòng 0 vĩnh viễn giữ lại thông báo cuối cùng của bước
// khởi động Wi-Fi -- kể cả khi mạng đã nối lại từ lâu.
void updateLCDWiFiStatus(bool connected);

#endif