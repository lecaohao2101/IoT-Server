#ifndef MIC_UPLINK_H
#define MIC_UPLINK_H

#include <Arduino.h>

// Mic INMP441 nằm chung board với loa A2DP, nên đường tiếng nói phải sống chung
// với Bluetooth Classic trong cùng một ngân sách RAM. Hai hệ quả xuyên suốt file
// này: không mở WebSocket (thư viện + TLS không còn chỗ), và không bao giờ giữ cả
// câu nói trong heap -- âm thanh đi thẳng từ DMA ra socket theo từng khung 1 KB.

// ==================== CHÂN I2S (ESP32 classic) ====================
// GPIO1 là chân TX của Serial trên ESP32 thường. Đấu SCK vào đó thì mất sạch log
// Serial -- đúng thứ cần để chẩn đoán. Vì vậy SCK nằm ở GPIO14.
constexpr int MIC_PIN_SD  = 32;  // DOUT của mic  -> D32
constexpr int MIC_PIN_SCK = 14;  // BCLK          -> D14
constexpr int MIC_PIN_WS  = 15;  // LRCL / WS     -> D15
// Chân L/R của mic phải nối GND: cấu hình I2S dưới đây chỉ đọc kênh trái.

constexpr int MIC_SAMPLE_RATE = 16000;  // server chốt cứng 16 kHz PCM16 mono

// Cài driver I2S, tự kiểm tra mic, in kết quả, rồi NHẢ LẠI driver.
//
// Nhả lại là điều bắt buộc chứ không phải dọn dẹp cho đẹp: đệm DMA của I2S nằm
// trong cùng vùng nhớ mà A2DP và TCP socket phải chia nhau, và trên board này
// phần còn lại sau khi Bluetooth khởi động chỉ tính bằng vài KB. Giữ driver suốt
// đời chương trình là lấy mất đúng khoản dự phòng dùng để mở socket.
//
// Gọi TRƯỚC a2dp_source.start(): lúc đó heap còn rộng, và chưa có radio nào bật
// nên kết quả đo là tình trạng thuần tuý của phần cứng mic.
bool micBegin();

int micLevel();  // 0..100, lần đo gần nhất (tự kiểm tra hoặc lần test gần nhất)

// Thu `duration_ms` rồi POST thẳng lên /upload-audio. Hàm này CHẶN loop() trong
// suốt thời gian thu cộng thời gian server chạy STT + LLM + TTS.
struct MicUploadResult {
  bool     ok;              // server trả 200 và success=true
  int      http_status;
  uint32_t bytes_sent;
  int16_t  local_peak;      // biên độ lớn nhất CHÍNH TRONG đoạn vừa gửi
  uint32_t elapsed_ms;
  String   transcript;      // server nghe được gì
  String   reply;           // trợ lý trả lời gì
  String   error;           // mã lỗi server trả về, nếu có
};

MicUploadResult micRecordAndUpload(const char* host, int port, uint32_t duration_ms);

#endif
