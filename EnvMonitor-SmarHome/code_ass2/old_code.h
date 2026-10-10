#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <DHT.h>
#include <ESP32Servo.h> // Thư viện điều khiển Servo trên ESP32
#include "WiFiConfig.h" // Import module WiFi vừa tạo

// ==================== KHAI BÁO CHÂN GPIO ====================
// Nút nhấn (Input)
const int PIN_BTN_LIVING  = 12;
const int PIN_BTN_KITCHEN = 16;
const int PIN_BTN_BED     = 21;
const int PIN_BTN_BALCONY = 38;

// Đèn LED (Output)
const int PIN_LED_LR_MAIN = 10;
const int PIN_LED_LR_SOFA = 11;
const int PIN_LED_KIT_MAIN= 14;
const int PIN_LED_BED_MAIN= 17;
const int PIN_LED_BED_SIDE= 18;
const int PIN_LED_STUDY   = 36;
const int PIN_LED_BALCONY = 37;
const int PIN_LED_WC      = 39;

// Cảm biến Siêu âm
const int PIN_TRIG = 4;
const int PIN_ECHO = 5;

// Cảm biến DHT
const int PIN_DHT  = 7;
#define DHTTYPE DHT11

// Microphone
const int PIN_MIC_AO = 1;
const int PIN_MIC_DO = 2;

// Còi Buzzer
const int PIN_BUZZER = 6;


// Màn hình I2C (SDA=8, SCL=9)
const int PIN_SDA = 8;
const int PIN_SCL = 9;

// Động cơ Servo Cánh lật Điều hòa
const int PIN_SERVO_LR  = 47; // Servo Phòng khách
const int PIN_SERVO_BED = 48; // Servo Phòng ngủ

// Góc quay mặc định cho Servo (30 độ khi mở, 0 độ khi đóng)
const int SERVO_ANGLE_OPEN  = 30;
const int SERVO_ANGLE_CLOSE = 0;

// ==================== KHAI BÁO THIẾT BỊ ====================
TwoWire I2C_LR  = TwoWire(0); 
TwoWire I2C_BED = TwoWire(1); 

LiquidCrystal_I2C lcd_lr(0x27, 16, 2);   // LCD Phòng khách
LiquidCrystal_I2C lcd_bed(0x27, 16, 2);  // LCD Phòng ngủ
DHT dht(PIN_DHT, DHTTYPE);

Servo servo_lr;   // Đối tượng Servo Phòng khách
Servo servo_bed;  // Đối tượng Servo Phòng ngủ

// ==================== BIẾN TRẠNG THÁI NÚT & ĐÈN ====================
bool state_lr      = false;
bool state_kitchen = false;
bool state_bed     = false;
bool state_balcony = false;

int lastBtnState_lr      = HIGH;
int lastBtnState_kitchen = HIGH;
int lastBtnState_bed     = HIGH;
int lastBtnState_balcony = HIGH;

unsigned long lastSensorRead = 0;
const long sensorInterval = 1000;

// ==================== HÀM PHỤ TRỢ ====================
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

// ==================== HÀM XỬ LÝ CLICK NÚT NHẤN & XOAY SERVO ====================
void handleButtons() {
  int current_lr      = digitalRead(PIN_BTN_LIVING);
  int current_kitchen = digitalRead(PIN_BTN_KITCHEN);
  int current_bed     = digitalRead(PIN_BTN_BED);
  int current_balcony = digitalRead(PIN_BTN_BALCONY);

  // 1. Nút Phòng Khách: Bật/Tắt Đèn + Xoay Servo Phòng khách 30 độ
  if (lastBtnState_lr == HIGH && current_lr == LOW) {
    state_lr = !state_lr;
    digitalWrite(PIN_LED_LR_MAIN, state_lr ? HIGH : LOW);
    digitalWrite(PIN_LED_LR_SOFA, state_lr ? HIGH : LOW);
    
    // Xoay Servo Phòng khách (30 độ khi BAT, 0 độ khi TAT)
    servo_lr.write(state_lr ? SERVO_ANGLE_OPEN : SERVO_ANGLE_CLOSE);

    digitalWrite(PIN_BUZZER, HIGH); delay(30); digitalWrite(PIN_BUZZER, LOW);
    Serial.printf("[Click] Điều hòa Phòng Khách: %s | Servo: %d deg\n", 
                  state_lr ? "BAT" : "TAT", state_lr ? SERVO_ANGLE_OPEN : SERVO_ANGLE_CLOSE);
    delay(50);
  }
  lastBtnState_lr = current_lr;

  // 2. Nút Nhà Bếp
  if (lastBtnState_kitchen == HIGH && current_kitchen == LOW) {
    state_kitchen = !state_kitchen;
    digitalWrite(PIN_LED_KIT_MAIN, state_kitchen ? HIGH : LOW);
    
    digitalWrite(PIN_BUZZER, HIGH); delay(30); digitalWrite(PIN_BUZZER, LOW);
    Serial.printf("[Click] Đèn Nhà Bếp: %s\n", state_kitchen ? "BAT" : "TAT");
    delay(50);
  }
  lastBtnState_kitchen = current_kitchen;

  // 3. Nút Phòng Ngủ: Bật/Tắt Đèn + Xoay Servo Phòng ngủ 30 độ
  if (lastBtnState_bed == HIGH && current_bed == LOW) {
    state_bed = !state_bed;
    digitalWrite(PIN_LED_BED_MAIN, state_bed ? HIGH : LOW);
    digitalWrite(PIN_LED_BED_SIDE, state_bed ? HIGH : LOW);
    
    // Xoay Servo Phòng ngủ (30 độ khi BAT, 0 độ khi TAT)
    servo_bed.write(state_bed ? SERVO_ANGLE_OPEN : SERVO_ANGLE_CLOSE);

    digitalWrite(PIN_BUZZER, HIGH); delay(30); digitalWrite(PIN_BUZZER, LOW);
    Serial.printf("[Click] Điều hòa Phòng Ngủ: %s | Servo: %d deg\n", 
                  state_bed ? "BAT" : "TAT", state_bed ? SERVO_ANGLE_OPEN : SERVO_ANGLE_CLOSE);
    delay(50);
  }
  lastBtnState_bed = current_bed;

  // 4. Nút Ban Công
  if (lastBtnState_balcony == HIGH && current_balcony == LOW) {
    state_balcony = !state_balcony;
    digitalWrite(PIN_LED_BALCONY, state_balcony ? HIGH : LOW);
    
    digitalWrite(PIN_BUZZER, HIGH); delay(30); digitalWrite(PIN_BUZZER, LOW);
    Serial.printf("[Click] Đèn Ban Công: %s\n", state_balcony ? "BAT" : "TAT");
    delay(50);
  }
  lastBtnState_balcony = current_balcony;
}

// ==================== SETUP ====================
void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000);
  // Tham số: (Tên_AP, Mật_Khẩu_AP, Thời_gian_chờ_giây)
  bool wifiConnected = initWiFiPortal("ESP32_SmartHome_AP", "12345678", 180);

  if (wifiConnected) {
    // Code hiển thị LCD / Xử lý khi có WiFi ở đây...
  } else {
    // Code xử lý khi không có WiFi ở đây...
  }

  Serial.println("Đang khởi tạo hệ thống ESP32-S3...");

  // 1. Output Pins
  int outputs[] = {
    PIN_LED_LR_MAIN, PIN_LED_LR_SOFA, PIN_LED_KIT_MAIN,
    PIN_LED_BED_MAIN, PIN_LED_BED_SIDE, PIN_LED_STUDY,
    PIN_LED_BALCONY, PIN_LED_WC, PIN_TRIG, PIN_BUZZER
  };
  for (int pin : outputs) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, LOW);
  }

  // 2. Input Pins
  pinMode(PIN_BTN_LIVING, INPUT_PULLUP);
  pinMode(PIN_BTN_KITCHEN, INPUT_PULLUP);
  pinMode(PIN_BTN_BED, INPUT_PULLUP);
  pinMode(PIN_BTN_BALCONY, INPUT_PULLUP);
  pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_MIC_DO, INPUT);

  // 3. Khởi tạo Servo (Chân 47 & 48)
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  
  servo_lr.setPeriodHertz(50);    // Tần số 50Hz chuẩn cho Servo SG90/MG995
  servo_bed.setPeriodHertz(50);

  servo_lr.attach(PIN_SERVO_LR, 500, 2400);   // Gán chân 47
  servo_bed.attach(PIN_SERVO_BED, 500, 2400); // Gán chân 48

  // 3. I2C & LCD
  Wire.begin(PIN_SDA, PIN_SCL);

  lcd_lr.init();
  lcd_lr.backlight();
  lcd_lr.setCursor(0, 0);
  lcd_lr.print("AC LivingRoom");

  lcd_bed.init();
  lcd_bed.backlight();
  lcd_bed.setCursor(0, 0);
  lcd_bed.print("AC Bedroom");

  // 5. DHT
  dht.begin();

  testBuzzerAndLEDs();
}

// ==================== LOOP ====================
void loop() {
  handleButtons();

  unsigned long currentMillis = millis();
  if (currentMillis - lastSensorRead >= sensorInterval) {
    lastSensorRead = currentMillis;

    float temp = dht.readTemperature();
    float hum  = dht.readHumidity();
    float dist = readUltrasonicDistance();
    int micAnalog = analogRead(PIN_MIC_AO);
    int micDigital = digitalRead(PIN_MIC_DO);

    Serial.println("==========================================");
    if (!isnan(temp) && !isnan(hum)) {
      Serial.printf("DHT11   | Nhiệt độ: %.1f *C | Độ ẩm: %.1f %%\n", temp, hum);
    } else {
      Serial.println("DHT11   | Lỗi đọc dữ liệu!");
    }

    if (dist >= 0) {
      Serial.printf("Siêu âm | Khoảng cách: %.1f cm\n", dist);
    } else {
      Serial.println("Siêu âm | Quá tầm / Lỗi!");
    }

    Serial.printf("Mic     | Analog: %d | Digital: %s\n", micAnalog, micDigital == HIGH ? "SOUND DETECTED" : "QUIET");

    // Hiển thị LCD Phòng khách
    lcd_lr.setCursor(0, 1);
    if (!isnan(temp)) {
      lcd_lr.printf("T:%.1fC  H:%.0f%%   ", temp, hum);
    } else {
      lcd_lr.print("DHT Error       ");
    }

    // Hiển thị LCD Phòng ngủ
    lcd_bed.setCursor(0, 1);
    lcd_bed.printf("D:%.0fcm Mic:%d   ", dist, micAnalog);
  }
}