// ============================================================================
//  ESP32 classic — mic INMP441 + loa I2S, nói chuyện với server căn hộ
// ============================================================================
//
// Board này là TAI và MIỆNG của căn hộ. Nó không bật đèn: nghe câu nói, gửi lên
// server, server nhận dạng rồi đẩy lệnh xuống MQTT cho board ESP32-S3 làm việc đó.
// Việc của board này chỉ là nghe cho rõ và nói cho ra tiếng.
//
//   mic ──► POST /upload-audio ──► server (STT + trợ lý + TTS)
//                                      │
//   loa ◄── GET /api/audio/stream ◄────┘
//
// ---------------------------------------------------------------------------
//  LOA BLUETOOTH ĐÃ BỊ BỎ. ĐÂY LÀ LÝ DO.
// ---------------------------------------------------------------------------
// Bản trước dùng loa Bluetooth A2DP và nó KHÔNG BAO GIỜ chạy: loa ghép nối thành
// công, thư viện in "a2dp media start successfully", rồi Bluedroid không bao giờ
// gọi callback xin dữ liệu -- bộ đếm CB đứng yên ở 0 và loa im hoàn toàn. Đã loại
// trừ thiếu RAM (chạy riêng Bluetooth với heap dư dả, vẫn CB:0) và nhiễu sóng
// (tắt Wi-Fi vẫn vậy). Nghi phạm còn lại là ESP32-A2DP 1.8.11 với core 3.3.12.
//
// Loa I2S thay thế đi thẳng vào vấn đề: không ghép nối, không Bluedroid, không
// bộ mã hoá SBC. Dữ liệu đi từ socket ra chân DIN, và trả lại khoảng 110 KB heap
// mà Bluetooth từng chiếm.
//
// ---------------------------------------------------------------------------
//  ĐẤU DÂY
// ---------------------------------------------------------------------------
//   Mic INMP441          ESP32           Loa I2S (MAX98357A / PCM5102)
//   SCK   ───────────────  D14  ──────── BCLK
//   WS    ───────────────  D15  ──────── LRC
//   SD    ───────────────  D32
//                          D27  ──────── DIN
//   L/R   ─── GND                        VIN ─── 5V, GND ─── GND
//
// Mic và loa DÙNG CHUNG hai dây clock, nên cả hai chạy trên một bộ I2S ở chế độ
// song công -- xem AudioBus.h để biết vì sao đó là cách duy nhất đúng.
//
// Riêng MAX98357A: chân SD trên module là ĐIỀU KHIỂN BẬT/TẮT, không phải dữ liệu.
// Để trống thì module tự bật. Nối SD xuống GND là tắt tiệt, và triệu chứng giống
// hệt "sai code" -- có clock, có dữ liệu, không ra tiếng.
//
// ---------------------------------------------------------------------------
//  BIÊN DỊCH
// ---------------------------------------------------------------------------
//   arduino-cli compile --fqbn "esp32:esp32:esp32:PartitionScheme=huge_app" .
//
// PartitionScheme=huge_app là BẮT BUỘC: bản mặc định chỉ cho app 1,2 MB.
//
// ---------------------------------------------------------------------------
//  LỆNH GÕ QUA SERIAL (115200)
// ---------------------------------------------------------------------------
//   t  phát nốt 440 Hz tại chỗ      -> thử dây và cấu hình I2S, không cần mạng
//   y  nhờ server đọc một câu       -> thử trọn đường mạng + TTS + loa
//   s  in trạng thái ngay lập tức
//   +  tăng âm lượng phần mềm       -  giảm
//   r  mở lại luồng audio

#include <WiFi.h>

#include "AudioBus.h"
#include "MicUplink.h"
#include "SpeakerStream.h"

// ==================== CẤU HÌNH ====================
// Sketch này cố tình KHÔNG include Config.h (file đó của board ESP32-S3). Hằng số
// khai ngay tại chỗ, như bản trước.
#define WIFI_SSID     "FSB-Student 1"
#define WIFI_PASSWORD "fsb@student"
#define WIFI_CONNECT_TIMEOUT_MS 20000

// true  -> server đã deploy trên Fly.io (cổng 80, không TLS: board không đủ RAM cho TLS)
// false -> máy tính trong mạng LAN
const bool USE_CLOUD = true;

const char* CLOUD_HOST = "smart-apartment-server.fly.dev";
const int   CLOUD_PORT = 80;
const char* LOCAL_HOST = "192.168.1.12";
const int   LOCAL_PORT = 8000;

// Câu mà lệnh 'y' nhờ server đọc. Có tên trợ lý trong đó để nghe là biết ngay
// tiếng đi đúng đường, không phải một đoạn nhiễu nào đó.
const char* TEST_SENTENCE =
    "Xin chao, minh la Anh Thac Si. Loa da hoat dong binh thuong roi nhe.";

static const char* host() { return USE_CLOUD ? CLOUD_HOST : LOCAL_HOST; }
static int         port() { return USE_CLOUD ? CLOUD_PORT : LOCAL_PORT; }

static unsigned long last_status_ms = 0;
static constexpr unsigned long STATUS_EVERY_MS = 5000;

// ==================== TỰ KIỂM TRA LOA ====================
// Chạy TRƯỚC Wi-Fi, cố ý. Nốt sin sinh tại chỗ nên kết quả là tình trạng thuần
// tuý của dây và của cấu hình I2S: nghe thấy nó mà sau đó im lặng thì lỗi nằm ở
// đường mạng, không phải ở loa. Trộn hai nguyên nhân đó vào nhau là cách nhanh
// nhất để mất một buổi tối.
static void speakerSelfTest() {
  Serial.println("---------------- TU KIEM TRA LOA I2S ----------------");
  Serial.printf("[LOA] BCLK=D%d  LRC=D%d  DIN=D%d\n",
                AUDIO_PIN_BCLK, AUDIO_PIN_WS, AUDIO_PIN_SPK_DIN);
  Serial.println("[LOA] Phat 3 not thu, NGHE KY: 440 Hz - 660 Hz - 880 Hz");

  uint32_t before = audioBusSamplesPlayed();
  audioBusTone(440.0f, 400, 0.25f);
  audioBusTone(660.0f, 400, 0.25f);
  audioBusTone(880.0f, 400, 0.25f);
  audioBusMute();
  uint32_t pushed = audioBusSamplesPlayed() - before;

  Serial.printf("[LOA] Da day %u mau ra I2S (mong doi ~%u).\n",
                (unsigned)pushed, (unsigned)(AUDIO_SAMPLE_RATE * 12 / 10));

  // Mỗi nhánh là một lỗi khác hẳn nhau. Nói thẳng việc cần làm thay vì in một con
  // số rồi để người đọc tự đoán.
  if (pushed == 0) {
    Serial.println("[LOA] ==> KHONG DAY DUOC MAU NAO. Driver I2S chua san sang.");
  } else {
    Serial.println("[LOA] ==> Du lieu da ra khoi chip. Neu KHONG nghe thay gi:");
    Serial.printf("[LOA]     1. Doi chieu lai day: BCLK=D%d, LRC=D%d, DIN=D%d.\n",
                  AUDIO_PIN_BCLK, AUDIO_PIN_WS, AUDIO_PIN_SPK_DIN);
    Serial.println("[LOA]     2. MAX98357A: chan SD la BAT/TAT, noi xuong GND la cam tieng.");
    Serial.println("[LOA]     3. VIN phai la 5V, va GND cua loa phai chung GND voi ESP32.");
    Serial.println("[LOA]     4. Go 't' de phat lai, '+' de tang am luong phan mem.");
  }
  Serial.println("-----------------------------------------------------");
}

// ==================== WI-FI ====================
static bool wifiConnect() {
  if (strlen(WIFI_SSID) == 0) return false;

  Serial.printf("[WIFI] Dang ket noi \"%s\"...\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);   // ngủ tiết kiệm điện làm luồng audio giật từng nhịp
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long deadline = millis() + WIFI_CONNECT_TIMEOUT_MS;
  while (WiFi.status() != WL_CONNECTED && millis() < deadline) {
    delay(300);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WIFI] ==> KHONG VAO DUOC MANG. Loa va mic se chi chay offline.");
    return false;
  }
  Serial.printf("[WIFI] Da ket noi | IP %s | RSSI %d dBm\n",
                WiFi.localIP().toString().c_str(), WiFi.RSSI());
  return true;
}

// ==================== DÒNG TRẠNG THÁI ====================
// Tách bạch từng đoạn đường, để không bao giờ phải đoán khâu nào hỏng:
//   Mic      mức âm thanh đang nghe được (0..100)
//   Server-> byte TẢI VỀ từ server, sau khi đã bóc khung chunked
//   Loa<-    byte thực sự đẩy ra chân DIN
//   Bo       byte phải bỏ vì DMA đầy
//   Lan cuoi bao lâu rồi chưa nhận được mẫu khác 0
static void printStatus() {
  SpeakerStats s = speakerStreamStats();
  unsigned long quiet_s = s.last_audio_ms ? (millis() - s.last_audio_ms) / 1000 : 0;

  Serial.printf("[TRANG THAI] Mic:%d/100 | Luong:%s | Server->%u B | Loa<-%u B | Bo:%u B | ",
                micLevel(), s.connected ? "OK" : "DUT",
                (unsigned)s.bytes_from_server, (unsigned)s.bytes_to_speaker,
                (unsigned)s.dropped_bytes);
  if (s.last_audio_ms) {
    Serial.printf("tieng cuoi: %lus truoc | ", quiet_s);
  } else {
    Serial.print("chua co tieng nao | ");
  }
  Serial.printf("Gain:%.1f | Heap:%u B | RSSI:%d\n",
                audioBusGain(), (unsigned)ESP.getFreeHeap(),
                WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0);
}

// ==================== LỆNH QUA SERIAL ====================
static void handleSerial() {
  if (!Serial.available()) return;
  char c = (char)Serial.read();

  switch (c) {
    case 't':
      Serial.println("[LOA] Phat not 440 Hz trong 1 giay...");
      audioBusTone(440.0f, 1000, 0.25f);
      audioBusMute();
      Serial.println("[LOA] Xong. Khong nghe thay gi thi loi nam o day hoac o nguon loa.");
      break;

    case 'y':
      Serial.println("[LOA] Nho server doc mot cau qua TTS...");
      speakerStreamSay(host(), port(), String(TEST_SENTENCE));
      break;

    case 's':
      printStatus();
      break;

    case '+':
      audioBusSetGain(audioBusGain() + 0.5f);
      Serial.printf("[LOA] Gain = %.1f\n", audioBusGain());
      break;

    case '-':
      audioBusSetGain(audioBusGain() - 0.5f);
      Serial.printf("[LOA] Gain = %.1f\n", audioBusGain());
      break;

    case 'r':
      Serial.println("[LOA] Mo lai luong audio...");
      speakerStreamBegin(host(), port());
      break;

    default:
      break;   // nuốt \r\n và mọi thứ khác
  }
}

// ============================================================================
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n\n======================================================");
  Serial.println("  ESP32 — MIC INMP441 + LOA I2S");
  Serial.println("======================================================");
  Serial.printf("[SYS] Heap luc khoi dong: %u byte\n", (unsigned)ESP.getFreeHeap());

  // Bus I2S lên trước mọi thứ khác: cả hai phép tự kiểm tra bên dưới đều cần nó,
  // và cả hai cùng chạy trước Wi-Fi để kết quả là tình trạng thuần tuý phần cứng.
  if (!audioBusBegin()) {
    Serial.println("[SYS] ==> KHONG CAI DUOC I2S. Mic va loa deu se im.");
  }

  speakerSelfTest();
  micBegin();          // tự kiểm tra mic, in kết quả, giữ nguyên bus

  if (wifiConnect()) {
    speakerStreamBegin(host(), port());
    Serial.printf("[SYS] Server: %s:%d\n", host(), port());
  }

  // Gửi xong câu nói, mic đứng chờ server trả lời trong vài giây -- mà server
  // đã bắt đầu đẩy tiếng trả lời xuống ngay trong quãng chờ đó. Móc này giữ cho
  // luồng audio vẫn được bơm trong lúc ấy, nếu không câu trả lời về tới loa thì
  // đã mất đầu.
  micSetIdleHook(speakerStreamPoll);

  if (!micListenBegin()) {
    Serial.println("[SYS] ==> Khong bat duoc che do lang nghe lien tuc.");
  }

  Serial.println("\n[SYS] San sang. Cu noi tu nhien, nho GOI TEN \"Anh Thac Si\" truoc.");
  Serial.println("[SYS] Lenh Serial: t=not thu | y=nho server doc | s=trang thai | +/-=am luong | r=mo lai luong");
  Serial.printf("[SYS] Heap con lai: %u byte\n\n", (unsigned)ESP.getFreeHeap());
  last_status_ms = millis();
}

void loop() {
  handleSerial();

  // Loa trước mic trong mỗi vòng. Luồng audio là thời gian thực và DMA chỉ đệm
  // được khoảng 96 ms; mic thì có đệm DMA riêng và chịu được trễ vài chục ms.
  // Để mic chạy trước là thỉnh thoảng lại hụt một nhịp tiếng.
  speakerStreamPoll();

  MicUploadResult result;
  if (micPoll(host(), port(), result)) {
    // micPoll đã in transcript và câu trả lời. Ở đây chỉ nói thêm phần mà một
    // mình nó không biết: câu trả lời sẽ về bằng đường nào.
    if (result.error == "wake_word_required") {
      Serial.println("[MIC-UP] ==> Chua goi ten. Thu lai: \"Anh Thac Si, bat den phong khach\".");
    } else if (result.reply.length() > 0) {
      Serial.println("[MIC-UP] ==> Dang cho tieng tra loi ve qua /api/audio/stream...");
    }
  }

  if (millis() - last_status_ms >= STATUS_EVERY_MS) {
    last_status_ms = millis();
    printStatus();
  }
}
