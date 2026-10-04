#ifndef SENSORS_H
#define SENSORS_H

#include <DHT.h>

void initSensors();
float readUltrasonicDistance();
bool readDHTData(float &temp, float &hum);

#endif