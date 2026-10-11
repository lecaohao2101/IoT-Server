#include "Controls.h"
#include "Config.h"
#include "AudioHandler.h"
#include "StatusManager.h"
#include "MqttHandler.h"
#include "DisplayHandler.h"
#include <WiFi.h>
#include <ESP32Servo.h>

static Servo servo_lr;
static Servo servo_bed;

// Tách riêng biệt trạng thái đèn và trạng thái điều hòa / servo
static bool state_lr_light      = false;
static bool state_kitchen_light = false;
static bool state_bed_light     = false;
static bool state_balcony_light = false;

static bool state_lr_ac         = false;
static int  current_lr_angle    = SERVO_ANGLE_CLOSE;
static bool state_bed_ac        = false;
static int  current_bed_angle   = SERVO_ANGLE_CLOSE;

// Quản lý tự động ngắt xung PWM (detach) sau khi servo quay xong vị trí
// Giúp chống sụt áp nguồn 5V và triệt tiêu tiếng rè/rung (jitter) của servo SG90
static unsigned long servo_lr_detach_ms  = 0;
static unsigned long servo_bed_detach_ms = 0;

static int lastBtnState_lr      = HIGH;
static int lastBtnState_kitchen = HIGH;
static int lastBtnState_bed     = HIGH;
static int lastBtnState_balcony = HIGH;

// Thời gian debounce chống dội phím và chống nhiễu xung điện trên chân nút bấm
static unsigned long lastDebounce_lr      = 0;
static unsigned long lastDebounce_kitchen = 0;
static unsigned long lastDebounce_bed     = 0;
static unsigned long lastDebounce_balcony = 0;
static constexpr unsigned long DEBOUNCE_DELAY_MS = 200;

static void moveServoLR(int angle) {
  angle = constrain(angle, 0, 180);
  current_lr_angle = angle;
  if (!servo_lr.attached()) {
    servo_lr.attach(PIN_SERVO_LR, 500, 2400);
  }
  servo_lr.write(angle);
  servo_lr_detach_ms = millis() + 500;
}

static void moveServoBed(int angle) {
  angle = constrain(angle, 0, 180);
  current_bed_angle = angle;
  if (!servo_bed.attached()) {
    servo_bed.attach(PIN_SERVO_BED, 500, 2400);
  }
  servo_bed.write(angle);
  servo_bed_detach_ms = millis() + 500;
}

void updateServos() {
  unsigned long now = millis();
  if (servo_lr_detach_ms != 0 && (long)(now - servo_lr_detach_ms) >= 0) {
    servo_lr.detach();
    servo_lr_detach_ms = 0;
  }
  if (servo_bed_detach_ms != 0 && (long)(now - servo_bed_detach_ms) >= 0) {
    servo_bed.detach();
    servo_bed_detach_ms = 0;
  }
}

void refreshLCDDevices(bool wifiOk) {
  updateLCDLivingRoomDevices(state_lr_light, state_lr_ac, current_lr_angle, wifiOk);
  updateLCDBedroomDevices(state_bed_light, state_bed_ac, current_bed_angle, (digitalRead(PIN_LED_STUDY) == HIGH));
}

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
  moveServoLR(SERVO_ANGLE_CLOSE);
  moveServoBed(SERVO_ANGLE_CLOSE);
  refreshLCDDevices(false);
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
  updateServos();
  checkLCDNotifyTimeout();

  int current_lr      = digitalRead(PIN_BTN_LIVING);
  int current_kitchen = digitalRead(PIN_BTN_KITCHEN);
  int current_bed     = digitalRead(PIN_BTN_BED);
  int current_balcony = digitalRead(PIN_BTN_BALCONY);

  if (lastBtnState_lr == HIGH && current_lr == LOW) {
    unsigned long now = millis();
    if (now - lastDebounce_lr >= DEBOUNCE_DELAY_MS) {
      lastDebounce_lr = now;
      state_lr_light = !state_lr_light;
      digitalWrite(PIN_LED_LR_MAIN, state_lr_light ? HIGH : LOW);
      digitalWrite(PIN_LED_LR_SOFA, state_lr_light ? HIGH : LOW);

      // Hiển thị thông báo tức thời và làm mới màn hình LCD
      showLCDNotify(1, state_lr_light ? "[PK]  BAT DEN   " : "[PK]  TAT DEN   ", 2000);
      refreshLCDDevices(WiFi.status() == WL_CONNECTED);

      String boolStr = state_lr_light ? "true" : "false";
      String json = "{\"buttons\":{\"living\":" + boolStr + "}," +
                    "\"leds\":{\"lr_main\":" + boolStr + ",\"lr_sofa\":" + boolStr + "}}";
      sendPartialStatus(json);

      // Bắn trạng thái tức thời lên MQTT broker (CHỈ ĐÈN, KHÔNG ĐỤNG SERVO/AC)
      mqttPublishState("living_room", "living_room_light", state_lr_light ? "{\"power\":\"on\"}" : "{\"power\":\"off\"}");
      mqttPublishState("living_room", "living_room_sofa_light", state_lr_light ? "{\"power\":\"on\"}" : "{\"power\":\"off\"}");

      digitalWrite(PIN_BUZZER, HIGH); 
      delay(30); 
      digitalWrite(PIN_BUZZER, LOW);
      Serial.printf("[BTN-CLICK] Nút Phòng Khách -> Đèn: %s\n", state_lr_light ? "BAT" : "TAT");
    }
  }
  lastBtnState_lr = current_lr;

  if (lastBtnState_kitchen == HIGH && current_kitchen == LOW) {
    unsigned long now = millis();
    if (now - lastDebounce_kitchen >= DEBOUNCE_DELAY_MS) {
      lastDebounce_kitchen = now;
      state_kitchen_light = !state_kitchen_light;
      digitalWrite(PIN_LED_KIT_MAIN, state_kitchen_light ? HIGH : LOW);

      showLCDNotify(1, state_kitchen_light ? "[BEP] BAT DEN   " : "[BEP] TAT DEN   ", 2000);
      refreshLCDDevices(WiFi.status() == WL_CONNECTED);

      String boolStr = state_kitchen_light ? "true" : "false";
      String json = "{\"buttons\":{\"kitchen\":" + boolStr + "}," +
                    "\"leds\":{\"kit_main\":" + boolStr + "}}";
      sendPartialStatus(json);

      // Bắn trạng thái tức thời lên MQTT broker
      mqttPublishState("kitchen", "kitchen_light", state_kitchen_light ? "{\"power\":\"on\"}" : "{\"power\":\"off\"}");

      digitalWrite(PIN_BUZZER, HIGH); delay(30); digitalWrite(PIN_BUZZER, LOW);
      Serial.printf("[BTN-CLICK] Nút Nhà Bếp -> Đèn: %s\n", state_kitchen_light ? "BAT" : "TAT");
    }
  }
  lastBtnState_kitchen = current_kitchen;

  if (lastBtnState_bed == HIGH && current_bed == LOW) {
    unsigned long now = millis();
    if (now - lastDebounce_bed >= DEBOUNCE_DELAY_MS) {
      lastDebounce_bed = now;
      state_bed_light = !state_bed_light;
      digitalWrite(PIN_LED_BED_MAIN, state_bed_light ? HIGH : LOW);
      digitalWrite(PIN_LED_BED_SIDE, state_bed_light ? HIGH : LOW);

      showLCDNotify(2, state_bed_light ? "[PN]  BAT DEN   " : "[PN]  TAT DEN   ", 2000);
      refreshLCDDevices(WiFi.status() == WL_CONNECTED);

      String boolStr = state_bed_light ? "true" : "false";
      String json = "{\"buttons\":{\"bed\":" + boolStr + "}," +
                    "\"leds\":{\"bed_main\":" + boolStr + ",\"bed_side\":" + boolStr + ",\"study\":" + boolStr + "}}";
      sendPartialStatus(json);

      // Bắn trạng thái tức thời lên MQTT broker (CHỈ ĐÈN, KHÔNG ĐỤNG SERVO/AC)
      mqttPublishState("bedroom", "bedroom_light", state_bed_light ? "{\"power\":\"on\"}" : "{\"power\":\"off\"}");
      mqttPublishState("bedroom", "bedroom_side_light", state_bed_light ? "{\"power\":\"on\"}" : "{\"power\":\"off\"}");

      digitalWrite(PIN_BUZZER, HIGH); delay(30); digitalWrite(PIN_BUZZER, LOW);
      Serial.printf("[BTN-CLICK] Nút Phòng Ngủ -> Đèn: %s\n", state_bed_light ? "BAT" : "TAT");
    }
  }
  lastBtnState_bed = current_bed;

  if (lastBtnState_balcony == HIGH && current_balcony == LOW) {
    unsigned long now = millis();
    if (now - lastDebounce_balcony >= DEBOUNCE_DELAY_MS) {
      lastDebounce_balcony = now;
      state_balcony_light = !state_balcony_light;
      digitalWrite(PIN_LED_BALCONY, state_balcony_light ? HIGH : LOW);
      
      showLCDNotify(1, "[MIC] MO TRO LY ", 2000);
      refreshLCDDevices(WiFi.status() == WL_CONNECTED);

      String boolStr = state_balcony_light ? "true" : "false";
      String json = "{\"buttons\":{\"balcony\":" + boolStr + "}," +
                    "\"leds\":{\"balcony\":" + boolStr + "}}";
      sendPartialStatus(json);

      // Bắn trạng thái tức thời lên MQTT broker
      mqttPublishState("balcony", "balcony_light", state_balcony_light ? "{\"power\":\"on\"}" : "{\"power\":\"off\"}");

      digitalWrite(PIN_BUZZER, HIGH); delay(30); digitalWrite(PIN_BUZZER, LOW);
      Serial.printf("[BTN-CLICK] Nút Ban Công -> Đèn: %s\n", state_balcony_light ? "BAT" : "TAT");
      
      // Mở ngay một câu nói trên luồng WebSocket đang mở sẵn.
      Serial.println("[Click] Mở micro gửi trợ lý AI (nói tự nhiên, server tự cắt câu)...");
      voiceRequestTalk();
    }
  }
  lastBtnState_balcony = current_balcony;
}

void setDeviceActuator(const String& deviceId, bool power, int value) {
  Serial.printf("[ACTUATOR] %s -> %s (val=%d)\n", deviceId.c_str(), power ? "ON" : "OFF", value);
  if (deviceId == "living_room_light") {
    digitalWrite(PIN_LED_LR_MAIN, power ? HIGH : LOW);
    state_lr_light = power;
  } else if (deviceId == "living_room_sofa_light") {
    digitalWrite(PIN_LED_LR_SOFA, power ? HIGH : LOW);
  } else if (deviceId == "kitchen_light") {
    digitalWrite(PIN_LED_KIT_MAIN, power ? HIGH : LOW);
    state_kitchen_light = power;
  } else if (deviceId == "bedroom_light") {
    digitalWrite(PIN_LED_BED_MAIN, power ? HIGH : LOW);
    state_bed_light = power;
  } else if (deviceId == "bedroom_side_light") {
    digitalWrite(PIN_LED_BED_SIDE, power ? HIGH : LOW);
  } else if (deviceId == "study_light") {
    digitalWrite(PIN_LED_STUDY, power ? HIGH : LOW);
  } else if (deviceId == "balcony_light") {
    digitalWrite(PIN_LED_BALCONY, power ? HIGH : LOW);
    state_balcony_light = power;
  } else if (deviceId == "bathroom_light") {
    digitalWrite(PIN_LED_WC, power ? HIGH : LOW);
  } else if (deviceId == "living_room_ac") {
    int angle = (value >= 0) ? value : (power ? SERVO_ANGLE_OPEN : SERVO_ANGLE_CLOSE);
    moveServoLR(angle);
    state_lr_ac = power || (angle > 0);
  } else if (deviceId == "bedroom_ac") {
    int angle = (value >= 0) ? value : (power ? SERVO_ANGLE_OPEN : SERVO_ANGLE_CLOSE);
    moveServoBed(angle);
    state_bed_ac = power || (angle > 0);
  }
  refreshLCDDevices(WiFi.status() == WL_CONNECTED);
}

void syncAllFromStates(int lr_main, int lr_sofa, int kit_main, int bed_main, int bed_side, int study, int balcony, int wc, int lr_angle, int bed_angle) {
  Serial.printf("[ACTUATOR-SYNC] Đồng bộ GPIO: LR[%d,%d,AC:%d°] KIT[%d] BED[%d,%d,AC:%d°] BAL[%d]\n",
                lr_main, lr_sofa, lr_angle, kit_main, bed_main, bed_side, bed_angle, balcony);
  if (lr_main >= 0) {
    digitalWrite(PIN_LED_LR_MAIN, lr_main ? HIGH : LOW);
    state_lr_light = (lr_main != 0);
  }
  if (lr_sofa >= 0) digitalWrite(PIN_LED_LR_SOFA, lr_sofa ? HIGH : LOW);
  if (kit_main >= 0) {
    digitalWrite(PIN_LED_KIT_MAIN, kit_main ? HIGH : LOW);
    state_kitchen_light = (kit_main != 0);
  }
  if (bed_main >= 0) {
    digitalWrite(PIN_LED_BED_MAIN, bed_main ? HIGH : LOW);
    state_bed_light = (bed_main != 0);
  }
  if (bed_side >= 0) digitalWrite(PIN_LED_BED_SIDE, bed_side ? HIGH : LOW);
  if (study >= 0) digitalWrite(PIN_LED_STUDY, study ? HIGH : LOW);
  if (balcony >= 0) {
    digitalWrite(PIN_LED_BALCONY, balcony ? HIGH : LOW);
    state_balcony_light = (balcony != 0);
  }
  if (wc >= 0) digitalWrite(PIN_LED_WC, wc ? HIGH : LOW);

  if (lr_angle >= 0) {
    moveServoLR(lr_angle);
    state_lr_ac = (lr_angle > 0);
  }
  if (bed_angle >= 0) {
    moveServoBed(bed_angle);
    state_bed_ac = (bed_angle > 0);
  }
  refreshLCDDevices(WiFi.status() == WL_CONNECTED);
}

void publishAllDeviceStates() {
  bool lr_main  = (digitalRead(PIN_LED_LR_MAIN) == HIGH);
  bool lr_sofa  = (digitalRead(PIN_LED_LR_SOFA) == HIGH);
  bool kit      = (digitalRead(PIN_LED_KIT_MAIN) == HIGH);
  bool bed      = (digitalRead(PIN_LED_BED_MAIN) == HIGH);
  bool bed_side = (digitalRead(PIN_LED_BED_SIDE) == HIGH);
  bool study    = (digitalRead(PIN_LED_STUDY) == HIGH);
  bool bal      = (digitalRead(PIN_LED_BALCONY) == HIGH);
  bool wc       = (digitalRead(PIN_LED_WC) == HIGH);

  mqttPublishState("living_room", "living_room_light", lr_main ? "{\"power\":\"on\"}" : "{\"power\":\"off\"}");
  mqttPublishState("living_room", "living_room_sofa_light", lr_sofa ? "{\"power\":\"on\"}" : "{\"power\":\"off\"}");
  mqttPublishState("kitchen", "kitchen_light", kit ? "{\"power\":\"on\"}" : "{\"power\":\"off\"}");
  mqttPublishState("bedroom", "bedroom_light", bed ? "{\"power\":\"on\"}" : "{\"power\":\"off\"}");
  mqttPublishState("bedroom", "bedroom_side_light", bed_side ? "{\"power\":\"on\"}" : "{\"power\":\"off\"}");
  mqttPublishState("bedroom", "study_light", study ? "{\"power\":\"on\"}" : "{\"power\":\"off\"}");
  mqttPublishState("balcony", "balcony_light", bal ? "{\"power\":\"on\"}" : "{\"power\":\"off\"}");
  mqttPublishState("bathroom", "bathroom_light", wc ? "{\"power\":\"on\"}" : "{\"power\":\"off\"}");
  mqttPublishState("living_room", "living_room_ac", state_lr_ac ? ("{\"power\":\"on\",\"vane_angle\":" + String(current_lr_angle > 0 ? current_lr_angle : SERVO_ANGLE_OPEN) + "}").c_str() : "{\"power\":\"off\",\"vane_angle\":0}");
  mqttPublishState("bedroom", "bedroom_ac", state_bed_ac ? ("{\"power\":\"on\",\"vane_angle\":" + String(current_bed_angle > 0 ? current_bed_angle : SERVO_ANGLE_OPEN) + "}").c_str() : "{\"power\":\"off\",\"vane_angle\":0}");
  Serial.println("[MQTT-SYNC] Đã đồng bộ trạng thái thực tế toàn bộ thiết bị lên MQTT Broker (Retained)");
}