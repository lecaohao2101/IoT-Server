#ifndef SPEAKER_STREAM_H
#define SPEAKER_STREAM_H

#include <Arduino.h>

// ============================================================================
// KÉO TIẾNG TRẢ LỜI TỪ SERVER RA LOA I2S
// ============================================================================
//
// GET /api/audio/stream?rate=16000&channels=1 là một socket mở thường trực: server
// giữ nguyên đó, im lặng, và đẩy PCM16 xuống đúng lúc trợ lý nói. Xin thẳng 16 kHz
// mono để khớp bus I2S -- board không phải đổi tần số, và cũng không đủ sức làm
// việc đó theo thời gian thực.
//
// HAI CÁI BẪY ĐÃ TRẢ GIÁ Ở ĐÂY:
//
// 1. THÂN RESPONSE LÀ CHUNKED, KỂ CẢ KHI XIN HTTP/1.0.
//    Bản firmware trước xin HTTP/1.0 với lý do "HTTP/1.0 thì không có chunked" --
//    điều đó đúng với đặc tả nhưng SAI với uvicorn. StreamingResponse không biết
//    trước độ dài, và uvicorn trả về `transfer-encoding: chunked` bất kể phiên bản
//    client xin. Đo thật trên server đang chạy: byte đầu tiên của thân là
//    `32 30 30 0d 0a` = "200\r\n", tức kích thước khối 0x200 viết bằng ASCII.
//    Đổ thẳng dòng đó ra loa là đổ cả mấy ký tự ASCII kia vào giữa tiếng nói --
//    nghe thành tiếng "tách" đều đặn mỗi khối. Vì vậy phải bóc khung chunked.
//
// 2. SERVER CHỈ GỬI MỘT GÓI MỒI 512 BYTE RỒI IM.
//    Gói mồi (toàn số 0) chỉ để socket đăng ký ngay với audio hub. Sau đó im lặng
//    hoàn toàn cho tới khi có tiếng thật. Board KHÔNG có cách nào phân biệt "đang
//    chờ" với "socket đã chết", nên đừng bao giờ coi im lặng là lỗi. Muốn biết
//    server có thấy mình không thì đối chiếu `speaker_listeners` trong
//    GET /api/v1/system/info -- đó mới là con số thật.

struct SpeakerStats {
  bool     connected;
  uint32_t bytes_from_server;   // sau khi đã bóc khung chunked
  uint32_t bytes_to_speaker;    // thực sự đẩy được ra I2S
  uint32_t dropped_bytes;       // DMA đầy, phải bỏ -- tiếng đến muộn còn tệ hơn mất tiếng
  uint32_t reconnects;
  unsigned long last_audio_ms;  // millis() lúc nhận được mẫu khác 0 gần nhất
};

// Mở socket tới server. Thất bại cũng không sao: speakerStreamPoll() sẽ tự thử lại.
bool speakerStreamBegin(const char* host, int port);

// Gọi mỗi vòng loop(). Không chặn: đọc được bao nhiêu thì đẩy ra loa bấy nhiêu,
// hết dữ liệu thì trả về ngay. Tự mở lại socket khi đứt.
void speakerStreamPoll();

bool speakerStreamConnected();
SpeakerStats speakerStreamStats();

// Nhờ server đọc một câu: POST /api/audio/play {"text": "..."}.
//
// Đường ngắn nhất để thử loa qua mạng mà không cần mở miệng: server tổng hợp
// giọng nói rồi phát cho mọi loa đang nghe, trong đó có socket ở trên. Trả về
// true khi server nhận lệnh (HTTP 200), KHÔNG phải khi loa đã kêu.
bool speakerStreamSay(const char* host, int port, const String& text);

#endif
