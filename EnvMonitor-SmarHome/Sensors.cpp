#include "Sensors.h"
#include "Config.h"

static DHT dht(PIN_DHT, DHTTYPE);

void initSensors() {
  pinMode(PIN_TRIG, OUTPUT);
  digitalWrite(PIN_TRIG, LOW);
  pinMode(PIN_ECHO, INPUT);
  dht.begin();
}

float readUltrasonicDistance() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  
  long duration = pulseIn(PIN_ECHO, HIGH, 30000);
  if (duration == 0) return -1;
  return (duration * 0.0343) / 2.0;
}

bool readDHTData(float &temp, float &hum) {
  temp = dht.readTemperature();
  hum  = dht.readHumidity();
  return (!isnan(temp) && !isnan(hum));
}