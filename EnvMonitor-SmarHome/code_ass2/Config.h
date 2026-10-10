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

// ==================== WI-FI ====================
// Thu ket noi thang toi mang nay truoc. Khong vao duoc trong WIFI_CONNECT_TIMEOUT_MS
// thi quay ve Captive Portal cua WiFiManager nhu cu.
// De WIFI_SSID rong ("") neu muon bo hoan toan buoc nay va chi dung portal.
#define WIFI_SSID     "51_Nguyen_Thien_Ke"
#define WIFI_PASSWORD "0986172846"
#define WIFI_CONNECT_TIMEOUT_MS 25000

// ============ TRỢ LÝ GIỌNG NÓI THỜI GIAN THỰC (WebSocket /ws/voice) ============
// Mic đẩy thẳng từng khung 32 ms lên server trong lúc người dùng còn đang nói.
// Không bao giờ giữ cả câu nói trong RAM, nên độ dài câu không còn bị heap chặn.
#define VOICE_WS_HOST   "smart-apartment-server.fly.dev"
#define VOICE_WS_PATH   "/ws/voice"

// TLS giữ thường trực thêm ~40 KB heap, bên cạnh TLS mà MQTT đã giữ. Đặt false để
// chạy ws:// cổng 80 nếu heap căng -- đổi lại token và giọng nói đi dạng rõ trên
// đường truyền, nên chỉ dùng khi demo trong mạng tin cậy.
#define VOICE_WS_TLS    1
#define VOICE_WS_PORT   (VOICE_WS_TLS ? 443 : 80)

// Khớp với API_KEY đặt bằng `fly secrets set API_KEY=...`. Để rỗng nếu server đang
// chạy ALLOW_ANONYMOUS=true.
#define VOICE_WS_TOKEN  "7c1fbbd67e08ac63980f764e833f4171d4e22740970a83d1"
#define VOICE_DEVICE_ID "esp32_master"
#define VOICE_ROOM      "living_room"

// Cổng âm thanh: chỉ truyền khi thực sự có người nói. Thiếu cổng này mic sẽ đẩy
// 32 KB/s suốt ngày và đốt phút tính tiền của Google STT.
constexpr int VOICE_GATE_LEVEL    = 12;  // thang 0..100 của readINMP441SoundLevel()
constexpr int VOICE_GATE_BLOCKS   = 2;   // số khung liên tiếp vượt ngưỡng mới mở câu
constexpr unsigned long VOICE_SILENCE_MS      = 900;    // lặng bấy nhiêu thì chốt câu
constexpr unsigned long VOICE_MAX_UTTER_MS    = 20000;  // chặn trên, server cũng tự chặn

// ==================== CẤU HÌNH HIVEMQ CLOUD MQTT ====================
#define MQTT_BROKER    "navyqueen-54cb285b.a01.euc1.aws.hivemq.cloud"
#define MQTT_PORT      8883
#define MQTT_USER      "esp32_device"
#define MQTT_PASS      "esp32_device"
#define MQTT_CLIENT_ID "ESP32_SmartHome_Master"

#endif