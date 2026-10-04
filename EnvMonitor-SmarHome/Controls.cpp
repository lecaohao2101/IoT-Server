#include "Controls.h"
#include "Config.h"
#include "AudioHandler.h"
#include "StatusManager.h"
#include <ESP32Servo.h>

static Servo servo_lr;
static Servo servo_bed;

static bool state_lr      = false;
static bool state_kitchen = false;
static bool state_bed     = false;
static bool state_balcony = false;

static int lastBtnState_lr      = HIGH;
static int lastBtnState_kitchen = HIGH;
static int lastBtnState_bed     = HIGH;
static int lastBtnState_balcony = HIGH;

void initControls() {
  int outputs[] = {
    PIN_LED_LR_MAIN, PIN_LED_LR_SOFA, PIN_LED_KIT_MAIN,
    PIN_LED_BED_MAIN, PIN_LED_BED_SIDE, PIN_LED_STUDY,
    PIN_LED_BALCONY, PIN_LED_WC, PIN_BUZZER
  };
  for (int pin : outputs) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);
  }

  pinMode(PIN_BTN_LIVING, INPUT_PULLUP);
  pinMode(PIN_BTN_KITCHEN, INPUT_PULLUP);
  pinMode(PIN_BTN_BED, INPUT_PULLUP);
  pinMode(PIN_BTN_BALCONY, INPUT_PULLUP);

  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  servo_lr.setPeriodHertz(50);
  servo_bed.setPeriodHertz(50);
  servo_lr.attach(PIN_SERVO_LR, 500, 2400);
  servo_bed.attach(PIN_SERVO_BED, 500, 2400);
  servo_lr.write(SERVO_ANGLE_CLOSE);
  servo_bed.write(SERVO_ANGLE_CLOSE);
}

void testBuzzerAndLEDs() {
  Serial.println("--- BẮT ĐẦU TEST KHỞI ĐỘNG (LED & BUZZER) ---");
  int allLeds[] = {
    PIN_LED_LR_MAIN, PIN_LED_LR_SOFA, PIN_LED_KIT_MAIN,
    PIN_LED_BED_MAIN, PIN_LED_BED_SIDE, PIN_LED_STUDY,
    PIN_LED_BALCONY, PIN_LED_WC
  };

  for (int i = 0; i < 8; i++) {
    digitalWrite(allLeds[i], HIGH);
    digitalWrite(PIN_BUZZER, HIGH);
    delay(100);
    digitalWrite(PIN_BUZZER, LOW);
    delay(100);
  }
  delay(500);
  for (int i = 0; i < 8; i++) {
    digitalWrite(allLeds[i], LOW);
  }
  Serial.println("--- CẤU HÌNH SẴN SÀNG ---");
}

void handleButtons() {
  int current_lr      = digitalRead(PIN_BTN_LIVING);
  int current_kitchen = digitalRead(PIN_BTN_KITCHEN);
  int current_bed     = digitalRead(PIN_BTN_BED);
  int current_balcony = digitalRead(PIN_BTN_BALCONY);

  if (lastBtnState_lr == HIGH && current_lr == LOW) {
    state_lr = !state_lr;
    digitalWrite(PIN_LED_LR_MAIN, state_lr ? HIGH : LOW);
    digitalWrite(PIN_LED_LR_SOFA, state_lr ? HIGH : LOW);
    int angle = state_lr ? SERVO_ANGLE_OPEN : SERVO_ANGLE_CLOSE;
    servo_lr.write(angle);

    String boolStr = state_lr ? "true" : "false";
    String json = "{\"buttons\":{\"living\":" + boolStr + "}," +
                  "\"leds\":{\"lr_main\":" + boolStr + ",\"lr_sofa\":" + boolStr + "}," +
                  "\"servos\":{\"lr_angle\":" + String(angle) + "}}";
    sendPartialStatus(json);

    digitalWrite(PIN_BUZZER, HIGH); 
    delay(30); 
    digitalWrite(PIN_BUZZER, LOW);
    Serial.printf("[Click] Điều hòa Phòng Khách: %s\n", state_lr ? "BAT" : "TAT");
    delay(50);
  }
  lastBtnState_lr = current_lr;

  if (lastBtnState_kitchen == HIGH && current_kitchen == LOW) {
    state_kitchen = !state_kitchen;
    digitalWrite(PIN_LED_KIT_MAIN, state_kitchen ? HIGH : LOW);

    String boolStr = state_kitchen ? "true" : "false";
    String json = "{\"buttons\":{\"kitchen\":" + boolStr + "}," +
                  "\"leds\":{\"kit_main\":" + boolStr + "}}";
    sendPartialStatus(json);

    digitalWrite(PIN_BUZZER, HIGH); delay(30); digitalWrite(PIN_BUZZER, LOW);
    Serial.printf("[Click] Đèn Nhà Bếp: %s\n", state_kitchen ? "BAT" : "TAT");
    delay(50);
  }
  lastBtnState_kitchen = current_kitchen;

  if (lastBtnState_bed == HIGH && current_bed == LOW) {
    state_bed = !state_bed;
    digitalWrite(PIN_LED_BED_MAIN, state_bed ? HIGH : LOW);
    digitalWrite(PIN_LED_BED_SIDE, state_bed ? HIGH : LOW);

    int angle = state_bed ? SERVO_ANGLE_OPEN : SERVO_ANGLE_CLOSE;
    servo_bed.write(angle);

    String boolStr = state_bed ? "true" : "false";
    String json = "{\"buttons\":{\"bed\":" + boolStr + "}," +
                  "\"leds\":{\"bed_main\":" + boolStr + ",\"bed_side\":" + boolStr + ",\"study\":" + boolStr + "}," +
                  "\"servos\":{\"bed_angle\":" + String(angle) + "}}";
    sendPartialStatus(json);

    digitalWrite(PIN_BUZZER, HIGH); delay(30); digitalWrite(PIN_BUZZER, LOW);
    Serial.printf("[Click] Điều hòa Phòng Ngủ: %s\n", state_bed ? "BAT" : "TAT");
    delay(50);
  }
  lastBtnState_bed = current_bed;

  if (lastBtnState_balcony == HIGH && current_balcony == LOW) {
    state_balcony = !state_balcony;
    digitalWrite(PIN_LED_BALCONY, state_balcony ? HIGH : LOW);
    
    String boolStr = state_balcony ? "true" : "false";
    String json = "{\"buttons\":{\"balcony\":" + boolStr + "}," +
                  "\"leds\":{\"balcony\":" + boolStr + "}}";
    sendPartialStatus(json);

    digitalWrite(PIN_BUZZER, HIGH); delay(30); digitalWrite(PIN_BUZZER, LOW);
    
    // Thu âm 5 giây khi bấm nút Ban Công
    // Serial.println("[Click] Kích hoạt thu âm Ban Công (5 giây)...");
    // recordAndSendAudio(5);
    
    delay(50);
  }
  lastBtnState_balcony = current_balcony;
}

void setDeviceActuator(const String& deviceId, bool power, int value) {
  if (deviceId == "living_room_light") {
    digitalWrite(PIN_LED_LR_MAIN, power ? HIGH : LOW);
  } else if (deviceId == "living_room_sofa_light") {
    digitalWrite(PIN_LED_LR_SOFA, power ? HIGH : LOW);
  } else if (deviceId == "kitchen_light") {
    digitalWrite(PIN_LED_KIT_MAIN, power ? HIGH : LOW);
    state_kitchen = power;
  } else if (deviceId == "bedroom_light") {
    digitalWrite(PIN_LED_BED_MAIN, power ? HIGH : LOW);
    state_bed = power;
  } else if (deviceId == "bedroom_side_light") {
    digitalWrite(PIN_LED_BED_SIDE, power ? HIGH : LOW);
  } else if (deviceId == "study_light") {
    digitalWrite(PIN_LED_STUDY, power ? HIGH : LOW);
  } else if (deviceId == "balcony_light") {
    digitalWrite(PIN_LED_BALCONY, power ? HIGH : LOW);
    state_balcony = power;
  } else if (deviceId == "bathroom_light") {
    digitalWrite(PIN_LED_WC, power ? HIGH : LOW);
  } else if (deviceId == "living_room_ac") {
    int angle = (value >= 0) ? value : (power ? SERVO_ANGLE_OPEN : SERVO_ANGLE_CLOSE);
    servo_lr.write(angle);
    state_lr = (angle > 0);
  } else if (deviceId == "bedroom_ac") {
    int angle = (value >= 0) ? value : (power ? SERVO_ANGLE_OPEN : SERVO_ANGLE_CLOSE);
    servo_bed.write(angle);
  }
}

void syncAllFromStates(int lr_main, int lr_sofa, int kit_main, int bed_main, int bed_side, int study, int balcony, int wc, int lr_angle, int bed_angle) {
  if (lr_main >= 0) digitalWrite(PIN_LED_LR_MAIN, lr_main ? HIGH : LOW);
  if (lr_sofa >= 0) digitalWrite(PIN_LED_LR_SOFA, lr_sofa ? HIGH : LOW);
  if (kit_main >= 0) {
    digitalWrite(PIN_LED_KIT_MAIN, kit_main ? HIGH : LOW);
    state_kitchen = (kit_main != 0);
  }
  if (bed_main >= 0) {
    digitalWrite(PIN_LED_BED_MAIN, bed_main ? HIGH : LOW);
    state_bed = (bed_main != 0);
  }
  if (bed_side >= 0) digitalWrite(PIN_LED_BED_SIDE, bed_side ? HIGH : LOW);
  if (study >= 0) digitalWrite(PIN_LED_STUDY, study ? HIGH : LOW);
  if (balcony >= 0) {
    digitalWrite(PIN_LED_BALCONY, balcony ? HIGH : LOW);
    state_balcony = (balcony != 0);
  }
  if (wc >= 0) digitalWrite(PIN_LED_WC, wc ? HIGH : LOW);

  if (lr_angle >= 0) {
    servo_lr.write(lr_angle);
    state_lr = (lr_angle > 0);
  }
  if (bed_angle >= 0) {
    servo_bed.write(bed_angle);
  }
}