#ifndef CONFIG_H
#define CONFIG_H

#include <Arduino.h>

// ==================== KHAI BÁO CHÂN GPIO ====================
// Nút nhấn
constexpr int PIN_BTN_LIVING  = 12;
constexpr int PIN_BTN_KITCHEN = 16;
constexpr int PIN_BTN_BED     = 21;
constexpr int PIN_BTN_BALCONY = 38;

// Đèn LED
constexpr int PIN_LED_LR_MAIN  = 10;
constexpr int PIN_LED_LR_SOFA  = 11;
constexpr int PIN_LED_KIT_MAIN = 14;
constexpr int PIN_LED_BED_MAIN = 17;
constexpr int PIN_LED_BED_SIDE = 18;
constexpr int PIN_LED_STUDY    = 36;
constexpr int PIN_LED_BALCONY  = 37;
constexpr int PIN_LED_WC       = 39;

// Cảm biến Siêu âm & DHT
constexpr int PIN_TRIG = 4;
constexpr int PIN_ECHO = 5;
constexpr int PIN_DHT  = 7;
#define DHTTYPE DHT11

// Buzzer
constexpr int PIN_BUZZER = 6;

// Cấu hình chân I2S cho Cảm biến Mic INMP441
#define I2S_PORT I2S_NUM_0
constexpr int PIN_I2S_SD  = 1;  // Chân SD
constexpr int PIN_I2S_SCK = 2;  // Chân SCK / BCLK
constexpr int PIN_I2S_WS  = 15; // Chân WS / LRCK

// Màn hình I2C
constexpr int PIN_SDA = 8;
constexpr int PIN_SCL = 9;

// Servo
constexpr int PIN_SERVO_LR  = 47;
constexpr int PIN_SERVO_BED = 48;

constexpr int SERVO_ANGLE_OPEN  = 30;
constexpr int SERVO_ANGLE_CLOSE = 0;

// API Server URL (Đã cấu hình server deployed trên Cloud Fly.io)
const char SERVER_API_URL[] = "https://smart-apartment-server.fly.dev";

// ==================== CẤU HÌNH HIVEMQ CLOUD MQTT ====================
#define MQTT_BROKER    "navyqueen-54cb285b.a01.euc1.aws.hivemq.cloud"
#define MQTT_PORT      8883
#define MQTT_USER      "esp32_device"
#define MQTT_PASS      "esp32_device"
#define MQTT_CLIENT_ID "ESP32_SmartHome_Master"

#endif