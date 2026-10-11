// Ví dụ CHÍNH CHỦ của thư viện ESP32-A2DP, chép gần như nguyên văn từ
// examples/bt_music_sender, chỉ thêm ba thứ tối thiểu:
//
//   1. Tên loa thật (HAVIT TW967)
//   2. set_ssp_enabled(true) -- không có thì không ghép nối được, đã chứng minh
//   3. Vài dòng log để biết callback có được gọi hay không
//
// Mục đích: loại bỏ HOÀN TOÀN code của chúng ta khỏi phương trình.
//
//   Nghe thấy tiếng -> lỗi nằm trong sketch esp32_connect_loa, và ba khác biệt
//                      so với ví dụ này là nghi phạm: callback kiểu Frame thay
//                      vì byte thô, set_volume(), và delay(1) trong callback.
//   Vẫn im lặng     -> thư viện 1.8.11 không chạy được với core ESP32 3.3.12
//                      trên con loa này. Lúc đó mọi sửa đổi phía ta đều vô ích.
//
// Nạp cho ESP32 thường, Partition Scheme: Huge APP.

#include "BluetoothA2DPSource.h"
#include <math.h>

#define TARGET_SPEAKER_NAME "HAVIT TW967"

#define c3_frequency 130.81
const float pi_2 = PI * 2.0;
const float angular_frequency = pi_2 * c3_frequency;
const float deltaAngle = angular_frequency / 44100.0;

BluetoothA2DPSource a2dp_source;

volatile uint32_t cb_calls = 0;      // số lần Bluedroid xin dữ liệu
volatile uint32_t cb_frames = 0;     // tổng số frame đã cấp
static uint32_t prev_calls = 0;
static unsigned long last_report = 0;

// Nguyên văn callback của ví dụ: kiểu Frame, và có delay(1) chống watchdog.
int32_t get_data_frames(Frame *frame, int32_t frame_count) {
  cb_calls++;
  cb_frames += frame_count;

  static float m_angle = 0.0;
  float m_amplitude = 10000.0;
  float m_phase = 0.0;
  for (int sample = 0; sample < frame_count; ++sample) {
    frame[sample].channel1 = m_amplitude * sin(m_angle + m_phase);
    frame[sample].channel2 = frame[sample].channel1;
    m_angle += deltaAngle;
    if (m_angle > pi_2) m_angle -= pi_2;
  }
  delay(1);        // có trong ví dụ gốc, giữ nguyên
  return frame_count;
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\n=====================================================");
  Serial.println("=== VI DU CHINH CHU CUA THU VIEN ESP32-A2DP       ===");
  Serial.printf("=== Loa muc tieu: %-33s ===\n", TARGET_SPEAKER_NAME);
  Serial.println("=== Se phat mot not C3 (130.81 Hz) lien tuc       ===");
  Serial.println("=====================================================");
  Serial.printf("[INFO] Heap truoc khi bat Bluetooth: %u byte\n", (unsigned)ESP.getFreeHeap());

  a2dp_source.set_data_callback_in_frames(get_data_frames);
  a2dp_source.set_volume(30);

  // Khac biet duy nhat so voi vi du goc, va la thu bat buoc: thu vien mac dinh
  // tat Secure Simple Pairing, ma loa hien dai deu dung SSP.
  a2dp_source.set_ssp_enabled(true);

  a2dp_source.start(TARGET_SPEAKER_NAME);
  Serial.printf("[INFO] Heap sau khi bat Bluetooth: %u byte\n", (unsigned)ESP.getFreeHeap());
}

void loop() {
  unsigned long now = millis();
  if (now - last_report < 5000) {
    delay(100);
    return;
  }
  last_report = now;

  uint32_t calls = cb_calls;
  uint32_t delta = calls - prev_calls;
  prev_calls = calls;

  Serial.printf("[TRANG THAI] Ket noi:%s | CB:%u/5s | Tong frame:%u | Heap:%u\n",
                a2dp_source.is_connected() ? "OK" : "chua",
                (unsigned)delta, (unsigned)cb_frames, (unsigned)ESP.getFreeHeap());

  if (a2dp_source.is_connected() && delta == 0) {
    Serial.println("[KET LUAN] Da ghep loa nhung Bluedroid KHONG xin du lieu.");
    Serial.println("[KET LUAN] ==> Day la vi du goc cua tac gia, khong co code cua ta trong do.");
    Serial.println("[KET LUAN] ==> Thu vien 1.8.11 khong chay duoc voi core ESP32 3.3.12.");
  } else if (delta > 0) {
    Serial.println("[KET LUAN] Callback DANG CHAY. Ban phai nghe thay mot not tram lien tuc.");
    Serial.println("[KET LUAN] ==> Neu co tieng: loi nam trong sketch esp32_connect_loa cua ta.");
  }
}
