#include "DisplayHandler.h"
#include "Config.h"
#include <Wire.h>

LiquidCrystal_I2C lcd_lr(0x27, 16, 2);
LiquidCrystal_I2C lcd_bed(0x26, 16, 2);

// Cache chuỗi để chỉ ghi I2C khi nội dung thực sự thay đổi (chống nhấp nháy, không nghẽn I2C)
static char last_line0_lr[17]   = "";
static char last_line1_lr[17]   = "";
static char last_line0_bed[17]  = "";
static char last_line1_bed[17]  = "";

// Cache trạng thái thiết bị gần nhất
// Mỗi màn hình chỉ giữ thiết bị của phòng mình: trộn phòng nọ sang phòng kia
// là cách chắc chắn khiến người đứng trước màn hình đọc nhầm số của phòng bên.
static bool cached_lr_light = false;
static bool cached_lr_ac    = false;
static int  cached_lr_temp  = 26;
static bool cached_wifi_ok  = false;

static bool cached_bed_light = false;
static bool cached_bed_ac    = false;
static int  cached_bed_temp  = 26;
static bool cached_bed_extra = false;

// Quản lý thông báo popup tạm thời (Notification timeout)
static unsigned long lr_notify_until_ms  = 0;
static unsigned long bed_notify_until_ms = 0;

void initLCDs() {
  Wire.begin(PIN_SDA, PIN_SCL);
  lcd_lr.init();
  lcd_lr.backlight();
  lcd_bed.init();
  lcd_bed.backlight();

  lcd_lr.clear();
  lcd_lr.setCursor(0, 0);
  lcd_lr.print("SMART HOME - LR ");
  lcd_lr.setCursor(0, 1);
  lcd_lr.print("Khoi tao he thong");

  lcd_bed.clear();
  lcd_bed.setCursor(0, 0);
  lcd_bed.print("SMART HOME - BR ");
  lcd_bed.setCursor(0, 1);
  lcd_bed.print("Khoi tao he thong");
  
  strncpy(last_line0_lr, "SMART HOME - LR ", sizeof(last_line0_lr));
  strncpy(last_line0_bed, "SMART HOME - BR ", sizeof(last_line0_bed));
}

void showLCDNotify(int screen, const char* msg, unsigned long durationMs) {
  char buf[17];
  snprintf(buf, sizeof(buf), "%-16s", msg);

  if (screen == 1 || screen == 0) {
    lr_notify_until_ms = millis() + durationMs;
    strncpy(last_line0_lr, buf, sizeof(last_line0_lr));
    lcd_lr.setCursor(0, 0);
    lcd_lr.print(buf);
  }
  if (screen == 2 || screen == 0) {
    bed_notify_until_ms = millis() + durationMs;
    strncpy(last_line0_bed, buf, sizeof(last_line0_bed));
    lcd_bed.setCursor(0, 0);
    lcd_bed.print(buf);
  }
}

void checkLCDNotifyTimeout() {
  unsigned long now = millis();
  if (lr_notify_until_ms != 0 && (long)(now - lr_notify_until_ms) >= 0) {
    lr_notify_until_ms = 0;
    last_line0_lr[0] = '\0'; // Ép cập nhật lại trạng thái thiết bị
    updateLCDLivingRoomDevices(cached_lr_light, cached_lr_ac, cached_lr_temp, cached_wifi_ok);
  }
  if (bed_notify_until_ms != 0 && (long)(now - bed_notify_until_ms) >= 0) {
    bed_notify_until_ms = 0;
    last_line0_bed[0] = '\0'; // Ép cập nhật lại trạng thái thiết bị
    updateLCDBedroomDevices(cached_bed_light, cached_bed_ac, cached_bed_temp, cached_bed_extra);
  }
}

void updateLCDLivingRoomDevices(bool lightOn, bool acOn, int acTemp, bool wifiOk) {
  cached_lr_light = lightOn;
  cached_lr_ac    = acOn;
  cached_lr_temp  = acTemp;
  cached_wifi_ok  = wifiOk;

  unsigned long now = millis();
  if (lr_notify_until_ms != 0 && (long)(now - lr_notify_until_ms) < 0) {
    return; // Đang hiển thị popup thông báo tạm, không ghi đè
  }
  lr_notify_until_ms = 0;

  char line0[17];

  // "LR:ON  AC:24* OK" -- đèn phòng khách, nhiệt độ điều hòa NGƯỜI DÙNG YÊU
  // CẦU, và Wi-Fi. Số sau "AC:" không đo từ đâu cả -- điều hòa trong nhà này
  // không có phần cứng, nó là mức người dùng vừa đặt. Nhiệt độ THẬT của
  // phòng nằm ở dòng 1, do DHT11 đo.
  const char* light_str = lightOn ? "ON" : "OFF";

  char ac_buf[5];
  if (acOn && acTemp > 0) {
    snprintf(ac_buf, sizeof(ac_buf), "%d*", acTemp);
  } else {
    snprintf(ac_buf, sizeof(ac_buf), "OFF");
  }

  const char* wifi_str = wifiOk ? "OK" : "--";
  snprintf(line0, sizeof(line0), "LR:%-3s AC:%-3s %2s", light_str, ac_buf, wifi_str);

  if (strcmp(line0, last_line0_lr) != 0) {
    strncpy(last_line0_lr, line0, sizeof(last_line0_lr));
    lcd_lr.setCursor(0, 0);
    lcd_lr.print(line0);
  }
}

void updateLCDBedroomDevices(bool lightOn, bool acOn, int acTemp, bool extraLight) {
  cached_bed_light = lightOn;
  cached_bed_ac    = acOn;
  cached_bed_temp  = acTemp;
  cached_bed_extra = extraLight;

  unsigned long now = millis();
  if (bed_notify_until_ms != 0 && (long)(now - bed_notify_until_ms) < 0) {
    return; // Đang hiển thị popup thông báo tạm, không ghi đè
  }
  bed_notify_until_ms = 0;

  char line0[17];
  const char* bed_str = lightOn ? "ON" : "OFF";
  char ac_buf[5];
  if (acOn && acTemp > 0) {
    snprintf(ac_buf, sizeof(ac_buf), "%d*", acTemp);
  } else {
    snprintf(ac_buf, sizeof(ac_buf), "OFF");
  }
  const char* extra_str = extraLight ? "H1" : "H0";
  snprintf(line0, sizeof(line0), "BR:%-3s AC:%-3s %2s", bed_str, ac_buf, extra_str);

  if (strcmp(line0, last_line0_bed) != 0) {
    strncpy(last_line0_bed, line0, sizeof(last_line0_bed));
    lcd_bed.setCursor(0, 0);
    lcd_bed.print(line0);
  }
}

void updateLCDLivingRoomSensors(float temp, float hum, bool isDhtValid) {
  char line1[17];
  if (isDhtValid) {
    snprintf(line1, sizeof(line1), "T:%4.1fC H:%2.0f%%   ", temp, hum);
  } else {
    snprintf(line1, sizeof(line1), "DHT11 Dang Loi! ");
  }

  if (strcmp(line1, last_line1_lr) != 0) {
    strncpy(last_line1_lr, line1, sizeof(last_line1_lr));
    lcd_lr.setCursor(0, 1);
    lcd_lr.print(line1);
  }
}

void updateLCDBedroomSensors(float dist, int soundLevel) {
  char line1[17];
  if (dist >= 0) {
    snprintf(line1, sizeof(line1), "D:%3.0fcm Mic:%-3d  ", dist, soundLevel);
  } else {
    snprintf(line1, sizeof(line1), "D:---   Mic:%-3d  ", soundLevel);
  }

  if (strcmp(line1, last_line1_bed) != 0) {
    strncpy(last_line1_bed, line1, sizeof(last_line1_bed));
    lcd_bed.setCursor(0, 1);
    lcd_bed.print(line1);
  }
}

// Các hàm tương thích ngược
void updateLCDWiFiStatus(bool connected) {
  updateLCDLivingRoomDevices(cached_lr_light, cached_lr_ac, cached_lr_temp, connected);
}

void updateLCDLivingRoom(float temp, float hum, bool isDhtValid) {
  updateLCDLivingRoomSensors(temp, hum, isDhtValid);
}

void updateLCDBedroom(float dist, int soundLevel) {
  updateLCDBedroomSensors(dist, soundLevel);
}