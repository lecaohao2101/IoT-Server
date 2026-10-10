#include "DisplayHandler.h"
#include "Config.h"
#include "StatusManager.h"
#include <Wire.h>

LiquidCrystal_I2C lcd_lr(0x27, 16, 2);
LiquidCrystal_I2C lcd_bed(0x26, 16, 2);

void initLCDs() {
  Wire.begin(PIN_SDA, PIN_SCL);
  lcd_lr.init();
  lcd_lr.backlight();
  lcd_bed.init();
  lcd_bed.backlight();

  lcd_lr.clear();
  lcd_lr.setCursor(0, 0);
  lcd_lr.print("AC LivingRoom");

  lcd_bed.clear();
  lcd_bed.setCursor(0, 0);
  lcd_bed.print("AC Bedroom");
}

void updateLCDWiFiStatus(bool connected) {
  // Chỉ ghi lại khi trạng thái đổi: LCD đi qua I2C, viết 16 ký tự mỗi giây là
  // tự chuốc lấy nhấp nháy và chiếm bus của hai màn hình.
  static int last_state = -1;
  int state = connected ? 1 : 0;
  if (state == last_state) return;
  last_state = state;

  lcd_lr.setCursor(0, 0);
  lcd_lr.print(connected ? "AC LR    WiFi:OK" : "AC LR   WiFi:OFF");
}

void updateLCDLivingRoom(float temp, float hum, bool isDhtValid) {
  lcd_lr.setCursor(0, 1);
  char line2[17];
  if (isDhtValid) {
    snprintf(line2, sizeof(line2), "T:%.1fC  H:%.0f%%   ", temp, hum);
  } else {
    snprintf(line2, sizeof(line2), "DHT Error       ");
  }
  lcd_lr.print(line2);
}

void updateLCDBedroom(float dist, int soundLevel) {
  lcd_bed.setCursor(0, 1);
  char line2[17];
  if (dist >= 0) {
    snprintf(line2, sizeof(line2), "D:%.0fcm Mic:%d   ", dist, soundLevel);
  } else {
    snprintf(line2, sizeof(line2), "D:Err  Mic:%d   ", soundLevel);
  }
  lcd_bed.print(line2);
}