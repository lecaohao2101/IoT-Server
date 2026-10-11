#ifndef AUDIO_BUS_H
#define AUDIO_BUS_H

#include <Arduino.h>

// ============================================================================
// MỘT BUS I2S DÙNG CHUNG CHO CẢ MIC VÀ LOA
// ============================================================================
//
// Mic INMP441 và loa I2S (MAX98357A / PCM5102) đang nối CHUNG hai dây clock:
//
//        INMP441            ESP32            Loa I2S
//        SCK  ──────────────  D14  ────────── BCLK
//        WS   ──────────────  D15  ────────── LRC
//        SD   ──────────────  D32
//                             D27  ────────── DIN
//
// Đây không phải nối nhầm, mà là cách nối ĐÚNG duy nhất khi dùng chung chân:
// BCLK và LRC là tín hiệu ESP32 PHÁT RA. Không thể để I2S0 phát clock ra D14/D15
// cho mic rồi lại bắt I2S1 phát clock ra chính hai chân đó cho loa -- hai bộ
// ngoại vi tranh nhau một chân thì cái cài sau đè lên cái trước, và một trong hai
// thiết bị sẽ chạy theo clock của thiết bị kia.
//
// Vì vậy cả hai cùng dùng I2S_NUM_0 ở chế độ SONG CÔNG (RX | TX): một bộ clock,
// một tần số lấy mẫu, hai đường dữ liệu ngược chiều nhau. Kéo theo ba hệ quả mà
// phần còn lại của firmware phải sống chung:
//
//   1. Mic và loa BẮT BUỘC cùng 16 kHz. May là server cũng chốt cứng 16 kHz
//      PCM16 mono ở cả hai đầu, nên không phải đổi tần số lúc đang chạy.
//   2. Khung I2S là 32 bit (INMP441 trả mẫu 24-bit căn trái trong khung 32-bit).
//      Mẫu 16-bit gửi ra loa phải dịch lên nửa trên của khung -- xem audioBusWrite().
//   3. Chỉ một nơi được gọi i2s_driver_install. Đó là file này; MicUplink và
//      SpeakerStream đều đi qua đây.
//
// Bluetooth A2DP đã bị bỏ (xem ghi chú trong esp32_connect_loa.ino). Loa I2S
// không tranh RAM với Bluedroid, nên bus này được giữ thường trực chứ không phải
// cài rồi nhả từng lúc như hồi mic phải nhường chỗ cho A2DP.

// ==================== CHÂN ====================
// GPIO1 là chân TX của Serial. Đấu BCLK vào đó là mất sạch log -- đúng thứ cần
// để chẩn đoán. Vì vậy BCLK nằm ở D14.
constexpr int AUDIO_PIN_BCLK    = 14;  // dùng chung: mic gọi là SCK, loa gọi là BCLK
constexpr int AUDIO_PIN_WS      = 15;  // dùng chung: mic gọi là WS,  loa gọi là LRC
constexpr int AUDIO_PIN_MIC_SD  = 32;  // mic  -> ESP32 (DOUT của INMP441)
constexpr int AUDIO_PIN_SPK_DIN = 27;  // ESP32 -> loa  (DIN của module khuếch đại)

constexpr int AUDIO_SAMPLE_RATE = 16000;  // server chốt cứng ở cả hai chiều

// Cài bus I2S song công. Gọi nhiều lần không sao, lần sau là no-op.
bool audioBusBegin();
void audioBusEnd();
bool audioBusReady();

// ==================== CHIỀU VÀO (mic) ====================
// Đọc tối đa `max_samples` mẫu THÔ 32-bit từ DMA. Trả về số mẫu đọc được.
// `wait_ms = 0` nghĩa là không chặn: hết dữ liệu thì trả 0 ngay để loop() còn đi
// bơm tiếng cho loa.
//
// Cố ý trả về mẫu thô chưa dịch bit: phép dịch 14 bit của INMP441 là chuyện của
// MicUplink và phải khớp hệ số mà board ESP32-S3 đang dùng.
int audioBusRead(int32_t* raw, int max_samples, uint32_t wait_ms);

// ==================== CHIỀU RA (loa) ====================
// Ghi `samples` mẫu PCM16 mono ra loa. Trả về số mẫu thực sự ghi được -- ít hơn
// yêu cầu nghĩa là DMA đầy và đã hết `wait_ms`, phần còn lại người gọi tự quyết
// (bỏ đi hay thử lại). Bỏ đi thường đúng hơn: tiếng đến muộn còn tệ hơn mất tiếng.
size_t audioBusWrite(const int16_t* pcm, size_t samples, uint32_t wait_ms);

// Xả sạch đệm DMA về 0. Gọi khi dừng phát, nếu không mẫu cuối cùng nằm lại trong
// đệm và loa giữ nguyên mức điện áp đó -- nghe thành một tiếng "ù" dai dẳng.
void audioBusMute();

// Khuếch đại bằng phần mềm, nhân trước khi đẩy ra I2S. 1.0 = nguyên bản.
// Module MAX98357A mặc định đã có sẵn 9 dB phần cứng, nên chỉ cần động vào đây
// khi tiếng quá nhỏ mà không muốn hàn lại điện trở GAIN.
void  audioBusSetGain(float gain);
float audioBusGain();

// Phát một nốt sin. Hàm này CHẶN cho tới khi phát xong -- chỉ dùng lúc tự kiểm
// tra, đừng gọi trong loop().
//
// Đây là phép thử rẻ nhất tách được "sai dây" khỏi "sai luồng dữ liệu": nốt sin
// sinh tại chỗ, không cần Wi-Fi, không cần server. Nghe thấy nó là phần cứng và
// cấu hình I2S đều đúng, mọi thứ im lặng sau đó là lỗi của đường truyền.
void audioBusTone(float hz, uint32_t ms, float amplitude = 0.25f);

// Tổng số mẫu đã đẩy ra loa kể từ lúc khởi động. Dòng [TRANG THAI] in con số này
// để phân biệt "server không gửi gì" với "có gửi nhưng loa không kêu".
uint32_t audioBusSamplesPlayed();

#endif
