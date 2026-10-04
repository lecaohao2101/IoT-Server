#ifndef DISPLAY_HANDLER_H
#define DISPLAY_HANDLER_H

#include <LiquidCrystal_I2C.h>

extern LiquidCrystal_I2C lcd_lr;
extern LiquidCrystal_I2C lcd_bed;

void initLCDs();
void updateLCDLivingRoom(float temp, float hum, bool isDhtValid);
void updateLCDBedroom(float dist, int soundLevel);

#endif