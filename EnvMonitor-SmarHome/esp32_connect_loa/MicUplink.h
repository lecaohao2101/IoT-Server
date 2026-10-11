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

int micLevel();  // 0..100, lần đo gần nhất

// ==================== CỔNG ÂM THANH (lắng nghe liên tục) ====================
// Ngưỡng mở câu nói. Thiếu cổng này thì mic đẩy 32 KB/s suốt ngày và mỗi phút im
// lặng vẫn bị Google STT tính tiền.
constexpr int MIC_GATE_LEVEL  = 12;   // thang 0..100 của micLevel()
constexpr int MIC_GATE_FRAMES = 2;    // số khung liên tiếp vượt ngưỡng mới mở câu
constexpr unsigned long MIC_SILENCE_MS   = 900;    // lặng bấy nhiêu thì chốt câu
constexpr unsigned long MIC_MAX_UTTER_MS = 15000;  // chặn trên

// Bật chế độ lắng nghe liên tục: cài I2S và GIỮ driver.
//
// Khác với micBegin() vốn nhả driver ngay sau khi đo. Ở chế độ này mic phải mở
// thường trực mới bắt được lúc người dùng bắt đầu nói. Chỉ dùng khi Bluetooth
// đã tắt -- lúc đó 4 KB đệm DMA không còn tranh với ai.
bool micListenBegin();

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

// Gọi mỗi vòng loop(). Tự mở câu nói khi nghe thấy tiếng, tự chốt khi im lặng,
// rồi gửi lên server. Trả về true đúng một lần, ngay sau khi có kết quả.
//
// Độ dài câu nói KHÔNG bị chặn trước, vì thân request đi theo kiểu chunked: mỗi
// khung là một khối, không phải khai tổng độ dài từ đầu như Content-Length.
// Người dùng nói bao lâu thì thu bấy nhiêu, server tự quyết khi nào đủ.
bool micPoll(const char* host, int port, MicUploadResult& out);

#endif
