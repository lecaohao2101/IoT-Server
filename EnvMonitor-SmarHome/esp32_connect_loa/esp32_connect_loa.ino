#include <WiFi.h>
#include <WebServer.h>
#include <WiFiManager.h>      // Thư viện tạo Popup Wifi
#include <ESPmDNS.h>          // Để truy cập http://esp32-audio.local
#include "BluetoothA2DPSource.h"
#include <math.h>
#include <HTTPClient.h>
#include "esp_bt.h"
#include "MicUplink.h"     // Mic INMP441 nam chung board nay, khong phai o ESP32-S3

// ============================================================================
// 1. CẤU HÌNH & KHAI BÁO BIẾN TOÀN CỤC
// ============================================================================
#define SAMPLE_RATE 44100
#define FREQUENCY 440

// --- CẤU HÌNH KẾT NỐI SERVER FASTAPI / CLOUD ---
// Đặt USE_CLOUD = true để kết nối thẳng tới server đã deploy trên Fly.io
// Đặt USE_CLOUD = false nếu muốn kết nối tới IP máy tính cục bộ trong mạng LAN
const bool USE_CLOUD = true;

// Server Cloud (Fly.io) - Khuyến nghị Port 80 (HTTP) để tiết kiệm RAM tối đa khi chạy Bluetooth A2DP
const char* CLOUD_HOST = "smart-apartment-server.fly.dev";
const int CLOUD_PORT = 80; 

// Wi-Fi: thu ket noi thang toi mang nay truoc, khong vao duoc thi quay ve
// Captive Portal cua WiFiManager. De WIFI_SSID rong ("") de chi dung portal.
// Sketch nay khong include Config.h nen phai tu khai bao -- giong CLOUD_HOST ben duoi.
#define WIFI_SSID     "51_Nguyen_Thien_Ke"
#define WIFI_PASSWORD "0986172846"
#define WIFI_CONNECT_TIMEOUT_MS 15000

// Server Local (Máy tính cá nhân)
const char* LOCAL_HOST = "192.168.1.12"; 
const int LOCAL_PORT = 8000;

// Ten loa Bluetooth can ghep. Bo trong ("") de chap nhan thiet bi dau tien tim
// duoc -- chi nen dung khi do song, vi lan quet bat duoc ca dien thoai, tai nghe
// va TV hang xom, ma nhung thu do khong phai loa A2DP nen ghep noi se that bai
// lap vo tan. So khop khong phan biet hoa thuong, chap nhan ten chua chuoi nay.
#define TARGET_SPEAKER_NAME "HAVIT TW967"

// Ket noi thang toi dia chi MAC, bo qua buoc quet.
//
// Quet dua tren ten thiet bi, ma ten lai nam trong goi EIR -- loa phai dang phat
// quang ba va phai tra ve ten khop thi moi toi duoc buoc bat tay. Dia chi MAC thi
// co dinh, nen duong nay ngan hon va it diem gay hon. Lay MAC tu dong log
// "[BT SCAN] Bat duoc: ... | MAC: ..." roi dien vao day.
//
// Dat USE_FIXED_SPEAKER_MAC = false de quay ve quet tu dong nhu cu.
#define USE_FIXED_SPEAKER_MAC true
// HAVIT TW967 -- con loa thuc su ghep noi duoc.
//
// Truoc day cho nay la MAC cua 'G-10' (28:5B:51:C4:D4:33). Thiet bi do quang ba
// binh thuong va qua duoc bo loc Class of Device, nhung khong bao gio tra loi
// buoc bat tay A2DP. De nguyen MAC cu thi moi lan khoi dong mat 60 giay thu 5
// lan vo vong truoc khi chiu quay ve quet.
static esp_bd_addr_t FIXED_SPEAKER_MAC = {0x2F, 0x33, 0x45, 0xA6, 0xD6, 0x20};

BluetoothA2DPSource a2dp_source;
WebServer server(80);

// Cổng 80: không dựng TLS. Giữ một WiFiClientSecure ở đây chỉ tốn RAM, mà RAM
// là thứ board này không có. Muốn chạy 443 thì phải tính lại ngân sách bộ nhớ.
WiFiClient localAudioClient;

static float m_time = 0.0;
// volatile vì ba task khác nhau cùng đụng vào: connection_state_changed() chạy
// trong task callback của Bluetooth, get_sound_data() trong task media của
// Bluedroid, còn loop() và web server trong task Arduino. Cùng lý do đã ghi cho
// head/tail bên dưới.
volatile bool is_bt_connected = false;
volatile bool is_stream_connected = false;
volatile bool is_playing_test_sound = false;
String current_test_speech = "";
String current_youtube_url = "";
String connected_speaker_name = "Đang tìm kiếm...";
String connected_speaker_mac = "";

unsigned long lastStreamReconnect = 0;

// Ring buffer cho luồng audio. Đây là mảng toàn cục, tức nó bị trừ thẳng vào
// vùng nhớ mà heap dùng chung với Bluedroid -- 16 KB ở đây là 16 KB mà bộ mã hoá
// SBC và socket TCP không bao giờ có.
//
// 8 KB ở 44,1 kHz stereo là 46 ms tiếng đệm sẵn. Đủ để loop() lỡ nhịp một vài
// vòng mà tiếng không đứt, và trả lại 8 KB cho hai thứ đang chết đói.
// Cấp phát lúc chạy thay vì khai báo tĩnh, để lấy đúng thứ phần cứng thật sự có:
// board WROVER thì nằm hẳn trong PSRAM và rộng gấp tám lần, board WROOM thì
// vẫn 8 KB trong RAM chip như cũ. Một mảng tĩnh thì luôn bị trừ vào RAM chip,
// kể cả trên board có sẵn 4 MB PSRAM nằm không.
//
// Kích thước luôn là luỹ thừa của 2 để vòng quanh bộ đệm dùng phép AND thay cho
// phép chia dư -- vòng lặp trong callback A2DP chạy trên từng byte một.
uint8_t* audioBuffer = nullptr;
uint32_t AUDIO_BUFFER_SIZE = 0;
uint32_t audioBufferMask = 0;
// head do callback A2DP sua (task Bluetooth), tail do loop() sua (task Arduino).
// Thieu volatile, trinh bien dich duoc phep giu chung trong thanh ghi va khong
// doc lai -- tieng se dung im du du lieu van chay vao buffer.
volatile int head = 0, tail = 0;

// ---------------------------------------------------------------- do dem chan doan
// get_sound_data() chay trong task Bluetooth. Serial.print o do se lam vo tieng,
// nen o day chi cong don, con viec in ra de cho loop().
volatile uint32_t stat_bytes_to_speaker = 0;  // byte THUC su phat ra loa
volatile uint32_t stat_underruns = 0;         // so lan buffer rong, phai chen im lang
// So lan Bluedroid goi callback xin du lieu, dem o NGAY DAU ham truoc moi nhanh
// re. Tach bach hai chuyen rat de nham voi nhau:
//   goi = 0  -> Bluedroid khong he xin du lieu. Loi o tang A2DP.
//   goi > 0 ma Loa<- = 0 -> co xin, nhung ham thoat som. Loi o logic cua ta.
volatile uint32_t stat_cb_calls = 0;
uint32_t stat_bytes_from_server = 0;          // byte doc duoc tu /api/audio/stream
bool     speaker_was_playing = false;         // de chi in luc CHUYEN trang thai
unsigned long lastStatusLog = 0;
unsigned long lastAudioSeenAt = 0;
// Thoi diem loa ghep xong, de hoan viec mo socket lai vai giay.
unsigned long btReadySince = 0;

// Ket qua lan test mic gan nhat, de hien tren trang web quan tri.
MicUploadResult last_mic_result = {false, 0, 0, 0, 0, String(), String(), String()};
bool mic_ready = false;

int availableBuffer() {
  if (!audioBuffer) return 0;
  return (tail >= head) ? (tail - head) : ((int)AUDIO_BUFFER_SIZE - head + tail);
}

// Cấp bộ đệm vòng. Gọi TRƯỚC khi bật Bluetooth: lúc đó RAM còn nguyên vẹn nhất.
static void allocAudioBuffer() {
  const uint32_t SIZE_PSRAM = 65536;   // 372 ms tiếng ở 44,1 kHz stereo
  const uint32_t SIZE_DRAM  = 8192;    // 46 ms -- vừa đủ, và là tất cả những gì
                                       // RAM chip có thể cho mà không bóp nghẹt
                                       // bộ mã hoá SBC lẫn socket TCP.

  if (ESP.getPsramSize() > 0) {
    audioBuffer = (uint8_t*)ps_malloc(SIZE_PSRAM);
    if (audioBuffer) {
      AUDIO_BUFFER_SIZE = SIZE_PSRAM;
      Serial.printf("[MEM] Ring buffer %u byte nam trong PSRAM -- RAM chip khong mat gi.\n",
                    (unsigned)SIZE_PSRAM);
    }
  }

  if (!audioBuffer) {
    audioBuffer = (uint8_t*)malloc(SIZE_DRAM);
    if (!audioBuffer) {
      // Không có bộ đệm thì không phát được tiếng nào. Nói thẳng ra thay vì để
      // callback A2DP đọc vào con trỏ rỗng.
      Serial.println("[MEM] ===> KHONG CAP DUOC RING BUFFER. Se khong co tieng ra loa.");
      AUDIO_BUFFER_SIZE = 0;
      audioBufferMask = 0;
      return;
    }
    AUDIO_BUFFER_SIZE = SIZE_DRAM;
    Serial.printf("[MEM] Ring buffer %u byte nam trong RAM chip (board khong co PSRAM).\n",
                  (unsigned)SIZE_DRAM);
  }

  audioBufferMask = AUDIO_BUFFER_SIZE - 1;
  head = tail = 0;
}

Client& getAudioClient() {
  return localAudioClient;
}

// ============================================================================
// 2. CALLBACK CẤP DỮ LIỆU ÂM THANH CHO LOA BLUETOOTH A2DP
// ============================================================================
int32_t get_sound_data(uint8_t *data, int32_t len) {
  stat_cb_calls++;      // đếm trước mọi nhánh rẽ -- xem ghi chú ở phần khai báo

  if (!is_bt_connected) {
    memset(data, 0, len);
    return len;
  }

  // Nếu đang bật âm thử nghiệm (440Hz sin wave)
  if (is_playing_test_sound) {
    int16_t *pcm = (int16_t*)data;
    int sample_count = len / 2;
    float time_step = 1.0 / SAMPLE_RATE;
    for (int i = 0; i < sample_count; i += 2) {
      int16_t sample = (int16_t)(sin(2.0 * M_PI * FREQUENCY * m_time) * 8000.0);
      pcm[i] = sample;
      pcm[i + 1] = sample;
      m_time += time_step;
    }
    return len;
  }

  // Đọc dữ liệu stream từ Backend (Google TTS 44.1kHz Stereo) đưa ra loa
  int bytesRead = 0;
  while (bytesRead < len && availableBuffer() > 0) {
    data[bytesRead++] = audioBuffer[head];
    head = (head + 1) & audioBufferMask;
  }

  stat_bytes_to_speaker += bytesRead;

  // Nếu buffer tạm trống, chèn yên lặng để tránh nổ bụp / giật tiếng
  if (bytesRead < len) {
    // Buffer rong gan nhu luc nao cung dung: phan lon thoi gian server khong gui
    // gi ca. Chi dem la underrun khi dang co tieng ma bi hut giua chung -- do moi
    // la trieu chung that (mang khong kip, hoac loop() bi chan qua lau).
    if (bytesRead > 0) stat_underruns++;
    memset(data + bytesRead, 0, len - bytesRead);
  }

  return len;
}

// ============================================================================
// 3. QUÉT VÀ TỰ ĐỘNG CHỌN LOA BLUETOOTH (DISCOVERY)
// ============================================================================
bool ssid_callback(const char *ssid, esp_bd_addr_t address, int rssi) {
  char macStr[18];
  snprintf(macStr, sizeof(macStr), "%02X:%02X:%02X:%02X:%02X:%02X",
           address[0], address[1], address[2], address[3], address[4], address[5]);

  Serial.printf("[BT SCAN] Bắt được: '%s' | MAC: %s | RSSI: %d dBm\n", 
                (ssid && strlen(ssid) > 0) ? ssid : "Không rõ tên", macStr, rssi);

  if (ssid == NULL || strlen(ssid) == 0) return false;

  // Loc theo ten. Khong co buoc nay, mach se ghep voi BAT KY thiet bi nao co ten
  // hien ra truoc -- dien thoai, laptop, tai nghe -- roi that bai va quet lai mai.
  if (strlen(TARGET_SPEAKER_NAME) > 0) {
    String found = String(ssid);
    String want = String(TARGET_SPEAKER_NAME);
    found.toLowerCase();
    want.toLowerCase();
    if (found.indexOf(want) < 0) {
      Serial.printf("[BT SCAN] Bo qua '%s' -- khong khop TARGET_SPEAKER_NAME=\"%s\"\n",
                    ssid, TARGET_SPEAKER_NAME);
      return false;
    }
  }

  connected_speaker_name = String(ssid);
  connected_speaker_mac = String(macStr);

  Serial.println("----------------------------------------------");
  Serial.printf("===> CHỌN LOA BLUETOOTH: '%s' (%s)\n", ssid, macStr);
  Serial.println("===> Đang tiến hành ghép nối A2DP...");
  Serial.println("----------------------------------------------");

  return true; 
}

void connection_state_changed(esp_a2d_connection_state_t state, void *ptr) {
  if (state == ESP_A2D_CONNECTION_STATE_CONNECTED) {
    is_bt_connected = true;
    Serial.println("\n[A2DP] ===> KẾT NỐI LOA BLUETOOTH THÀNH CÔNG!");
  } else if (state == ESP_A2D_CONNECTION_STATE_DISCONNECTED) {
    is_bt_connected = false;
    connected_speaker_name = "Đang tìm kiếm...";
    Serial.println("\n[A2DP] Ngắt kết nối với Loa. Đang chờ quét lại...");
  }
}

// ============================================================================
// 4. KẾT NỐI LUỒNG ÂM THANH PERSISTENT VỚI SERVER FASTAPI
// ============================================================================
void connectAudioStream() {
  Client& client = getAudioClient();
  if (client.connected()) return;

  const char* host = USE_CLOUD ? CLOUD_HOST : LOCAL_HOST;
  int port = USE_CLOUD ? CLOUD_PORT : LOCAL_PORT;

  Serial.printf("[STREAM] Free Heap: %d bytes | Dang ket noi luong Audio TTS toi %s:%d...\n",
                ESP.getFreeHeap(), host, port);

  // 12 giây cho bước bắt tay TCP, thay vì 3 giây mặc định của core.
  //
  // Khi A2DP đang phát, Bluetooth chiếm sóng gần như liên tục trên cùng một
  // ăng-ten 2.4 GHz, nên gói SYN đi và về chậm hơn hẳn. Server trả lời trong
  // khoảng 50 ms khi đo từ máy tính -- chỗ mất thời gian là đường truyền của
  // board, không phải server.
  // Đặt qua đối tượng cụ thể chứ không qua tham chiếu `Client&`: lớp cơ sở
  // trừu tượng không có hàm này.
  localAudioClient.setConnectionTimeout(12000);

  if (!client.connect(host, port)) {
    is_stream_connected = false;
    size_t heap = ESP.getFreeHeap();
    if (heap < 8000) {
      // Mở một TCP socket cần vài KB liền kề. Dưới ngưỡng này thì mới thật sự
      // là hết RAM -- hai lỗi cần sửa theo hai hướng khác hẳn nhau.
      Serial.printf("[STREAM] Ket noi that bai vi HET RAM (%u byte).\n", (unsigned)heap);
    } else {
      Serial.printf("[STREAM] Bat tay TCP het gio (heap con %u byte, du dung). ", (unsigned)heap);
      Serial.println("Nhieu kha nang Bluetooth dang chiem song. Thu lai sau 5s...");
    }
    return;
  }

  // HTTP/1.0 la co y, khong phai so suat. Voi HTTP/1.1 server tra ve
  // "transfer-encoding: chunked", tuc moi khoi am thanh bi boc trong
  // "<hex do dai>\r\n <du lieu> \r\n". Vong doc o loop() do thang byte vao ring
  // buffer PCM, nen may byte khung do se bi phat ra nhu mau am thanh va lam lech
  // can le 16-bit -- tieng noi bien thanh rac. HTTP/1.0 khong co chunked.
  String request = String("GET /api/audio/stream?rate=44100&channels=2 HTTP/1.0\r\n") +
                   "Host: " + String(host) + "\r\n" +
                   "User-Agent: ESP32-A2DP-Speaker\r\n" +
                   "Accept: application/octet-stream\r\n\r\n";
  client.print(request);

  bool chunked = false;
  unsigned long timeout = millis();
  // 12 giây chứ không phải 4. Bluetooth và Wi-Fi dùng chung một ăng-ten, nên khi
  // A2DP đang phát thì gói HTTP về chậm hơn hẳn lúc mạng rảnh. 4 giây là đủ cho
  // một board chỉ chạy Wi-Fi, không đủ cho board này.
  while (client.connected() && millis() - timeout < 12000) {
    if (!client.available()) {
      // Quay vòng rỗng không nghỉ sẽ chiếm CPU của đúng tác vụ Bluetooth đang
      // cần nó để giải mã tiếng. Nhường một nhịp.
      delay(2);
      continue;
    }

    String line = client.readStringUntil('\n');
    line.trim();

    if (line.length() == 0) {           // dong trong = het header
      if (chunked) {
        // Tha ngat con hon phat tieng rac roi di do nguyen nhan o phia loa.
        Serial.println("[STREAM] LOI: server tra chunked encoding, byte khung se lan vao PCM.");
        Serial.println("[STREAM] Yeu cau phai la HTTP/1.0. Ngat ket noi.");
        client.stop();
        is_stream_connected = false;
        return;
      }
      is_stream_connected = true;
      Serial.println("[STREAM] ===> KET NOI LUONG AUDIO THANH CONG! San sang phat tieng Tro ly AI.");
      return;
    }

    String lower = line;
    lower.toLowerCase();
    if (lower.startsWith("transfer-encoding:") && lower.indexOf("chunked") >= 0) {
      chunked = true;
    }
  }

  // Đóng hẳn socket chứ không bỏ lửng.
  //
  // Bỏ lửng thì phần header còn chưa đọc hết sẽ bị vòng lặp ở loop() nuốt vào
  // ring buffer và phát ra loa như âm thanh -- một tiếng rẹt khó hiểu ngay đầu
  // câu nói. Đóng rồi mở lại sạch sẽ hơn nhiều.
  Serial.println("[STREAM] Het thoi gian cho header phan hoi. Dong socket de thu lai sach.");
  client.stop();
  is_stream_connected = false;
}


// ============================================================================
// 5. GIAO DIỆN WEB QUẢN TRỊ & THỬ NGHIỆM
// ============================================================================
void handleRoot() {
  // Một lần send(), trang gọn, chuỗi cấp phát sẵn.
  //
  // Hai lần trước đều sai theo hai hướng ngược nhau, và log đã dạy cả hai:
  //
  //   Bản đầu: nối chuỗi `html +=` bốn chục lần cho một trang 4 KB. Mỗi lần nối
  //   là một lần cấp phát lại; ở mức heap hơn chục KB đã phân mảnh thì gãy giữa
  //   chừng -> trình duyệt nhận mỗi phần đầu, hiện ra trang trống.
  //
  //   Bản hai: chia thành ~20 lần sendContent(). Nhưng core ESP32 cho mỗi lần
  //   write() tới 10 lần thử, mỗi lần chờ select() 1 giây
  //   (WIFI_CLIENT_MAX_WRITE_RETRY = 10, WIFI_CLIENT_SELECT_TIMEOUT_US = 1e6).
  //   Khi Bluetooth chiếm sóng và TCP không đẩy được byte nào, 20 lần gọi hoá
  //   thành 200 giây loop() đứng hình -- board biến mất khỏi mạng.
  //
  // Nên: ÍT lần gọi write nhất có thể (một), và trang đủ nhỏ để lọt một lần gửi.
  // reserve() trước để không có lần cấp phát lại nào.
  if (!server.client().connected()) return;

  String h;
  h.reserve(1400);

  h += F("<!DOCTYPE html><html><head><meta charset='UTF-8'>"
         "<meta name='viewport' content='width=device-width,initial-scale=1'>"
         "<title>ESP32 Audio Hub</title><style>"
         "body{font-family:sans-serif;background:#0f172a;color:#fff;padding:16px;text-align:center}"
         ".c{background:#1e293b;padding:16px;border-radius:10px;max-width:420px;margin:auto}"
         ".b{background:#0f172a;border:1px solid #334155;border-radius:6px;padding:10px;"
         "margin:10px 0;text-align:left;font-size:14px}"
         "a,button{display:inline-block;margin:4px;padding:10px 14px;border:0;border-radius:6px;"
         "font-weight:bold;text-decoration:none;color:#0f172a;background:#38bdf8}"
         "input{width:90%;padding:10px;margin:6px 0;border-radius:6px;border:1px solid #334155;"
         "background:#0f172a;color:#fff}"
         "</style></head><body><div class='c'><h3>ESP32 Audio Hub</h3><div class='b'>");

  h += F("Loa: ");
  h += is_bt_connected ? F("OK") : F("chua ghep");
  h += F("<br>Stream: ");
  h += is_stream_connected ? F("OK") : F("chua noi");
  h += F("<br>Mic: ");
  h += mic_ready ? F("OK") : F("khong co tin hieu");
  h += F("<br>Heap: ");
  h += ESP.getFreeHeap();
  h += F(" byte</div>");

  if (last_mic_result.bytes_sent > 0) {
    h += F("<div class='b'>Test mic gan nhat: ");
    h += last_mic_result.bytes_sent;
    h += F(" byte, HTTP ");
    h += last_mic_result.http_status;
    h += F("<br>Nghe duoc: ");
    h += last_mic_result.transcript.length() ? last_mic_result.transcript : String(F("(khong ro)"));
    if (last_mic_result.reply.length()) {
      h += F("<br>Tra loi: ");
      h += last_mic_result.reply;
    }
    h += F("</div>");
  }

  h += F("<form action='/say' method='POST'>"
         "<input name='text' value='Xin chao, toi la tro ly nha thong minh.' required>"
         "<button type='submit' style='background:#22c55e;color:#fff'>Noi thu (TTS)</button></form>"
         "<a href='/mic-test' style='background:#f97316;color:#fff'>Test Mic 3s</a>"
         "<a href='/bt-connect' style='background:#a855f7;color:#fff'>Ket noi loa</a>"
         "<a href='/test_sound'>Am thu 440Hz</a>"
         "</div></body></html>");

  server.send(200, "text/html", h);
}

void handleSay() {
  if (server.hasArg("text")) {
    String textToSay = server.arg("text");
    Serial.printf("\n[WEB UI] Yêu cầu phát TTS: '%s'\n", textToSay.c_str());

    // Cùng cổng 80 như luồng audio. Dựng TLS ở đây là chỗ dễ hết heap nhất trên
    // board này: handshake cần vài chục KB trong khi A2DP đã ăn gần hết.
    HTTPClient http;
    WiFiClient webClient;
    String serverUrl = String("http://") + (USE_CLOUD ? String(CLOUD_HOST) + ":" + String(CLOUD_PORT)
                                                      : String(LOCAL_HOST) + ":" + String(LOCAL_PORT)) +
                       "/api/audio/play";
    http.begin(webClient, serverUrl);

    http.addHeader("Content-Type", "application/json");
    String jsonPayload = "{\"text\":\"" + textToSay + "\"}";
    int code = http.POST(jsonPayload);
    Serial.printf("[HTTP /api/audio/play] Mã phản hồi: %d\n", code);
    http.end();
  }

  server.sendHeader("Location", "/");
  server.send(303);
}

// Thu mic rồi đẩy lên /upload-audio. Server chạy STT + trợ lý + TTS, và câu trả
// lời quay về chính board này qua luồng audio -- nên một lần bấm nút kiểm được
// trọn vòng: mic -> mạng -> nhận dạng -> loa.
void handleMicTest() {
  Serial.println("\n[WEB UI] ===> Yeu cau TEST MIC <===");

  const char* host = USE_CLOUD ? CLOUD_HOST : LOCAL_HOST;
  int port = USE_CLOUD ? CLOUD_PORT : LOCAL_PORT;

  last_mic_result = micRecordAndUpload(host, port, 3000);

  server.sendHeader("Location", "/");
  server.send(303);
}

// Ép thử lại kết nối loa ngay, không phải reset board.
//
// Loa Bluetooth thường chỉ mở cho ghép nối trong một khoảng ngắn sau khi bấm nút
// trên loa. Nếu khoảng đó trôi qua lúc board đang bận việc khác thì coi như lỡ,
// và chờ chu kỳ quét sau có thể mất hàng phút. Nút này cho bấm đúng lúc.
void handleBtConnect() {
  Serial.println("\n[WEB UI] ===> Yeu cau ket noi loa thu cong <===");

  if (is_bt_connected) {
    Serial.println("[A2DP] Loa dang ket noi san roi, khong lam gi.");
  } else {
    Serial.printf("[A2DP] Thu ket noi lai | Heap: %u byte\n", (unsigned)ESP.getFreeHeap());
    // Nạp lại địa chỉ đích và cấp thêm lượt thử. Thư viện giữ bộ đếm số lần thử
    // còn lại, hết lượt là nó thôi và chỉ còn quét -- gọi lại ở đây để nạp đầy.
    if (USE_FIXED_SPEAKER_MAC) {
      a2dp_source.set_auto_reconnect(FIXED_SPEAKER_MAC, 5);
    } else {
      a2dp_source.set_auto_reconnect(true, 5);
    }
    Serial.println("[A2DP] Da yeu cau. Bat loa va de no o che do ghep noi ngay bay gio.");
  }

  server.sendHeader("Location", "/");
  server.send(303);
}

void handlePlay() {
  if (server.hasArg("yt_url")) {
    current_youtube_url = server.arg("yt_url");
    is_playing_test_sound = false;
  }
  server.sendHeader("Location", "/");
  server.send(303);
}

void handleTestSound() {
  is_playing_test_sound = !is_playing_test_sound;
  server.sendHeader("Location", "/");
  server.send(303);
}

void handleResetWifi() {
  server.send(200, "text/html", "<h3>Đã xóa cấu hình Wi-Fi! ESP32 đang khởi động lại...</h3>");
  delay(1000);
  WiFiManager wm;
  wm.resetSettings();
  ESP.restart();
}

// ============================================================================
// 6. KHỞI TẠO HỆ THỐNG VÀ LOOP
// ============================================================================
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== KHỞI ĐỘNG HỆ THỐNG ESP32 SMART AUDIO HUB ===");

  // Mic trước tiên, trước cả Wi-Fi và Bluetooth. Hai lý do: heap đang rộng nhất
  // nên 8 KB đệm DMA chắc chắn cấp phát được, và chưa có radio nào bật nên kết
  // quả tự kiểm tra là tình trạng thuần tuý của phần cứng mic.
  // micBegin() tự cài I2S, đo mic, in kết quả rồi NHẢ LẠI driver. Bộ nhớ đó phải
  // để dành cho A2DP và cho socket -- giữ nó suốt đời chương trình là lấy mất
  // đúng khoản dự phòng dùng để mở luồng audio.
  mic_ready = micBegin();

  WiFiManager wm;
  wm.setConfigPortalTimeout(180);

  Serial.println("[WIFI] Dang kiem tra Wi-Fi...");

  // Giong board mic: uu tien mang cau hinh san, portal chi la duong lui.
  if (strlen(WIFI_SSID) > 0) {
    Serial.printf("[WIFI] Thu ket noi thang toi '%s'...\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    unsigned long t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < WIFI_CONNECT_TIMEOUT_MS) {
      delay(250);
      Serial.print(".");
    }
    Serial.println();
  }

  if (WiFi.status() != WL_CONNECTED && !wm.autoConnect("ESP32-Audio-Setup")) {
    Serial.println("[WIFI] Kết nối thất bại. Khởi động lại...");
    ESP.restart();
  }
  
  Serial.println("[WIFI] Đã kết nối Wi-Fi thành công!");
  Serial.print("[WIFI] IP ESP32: http://");
  Serial.println(WiFi.localIP());

  if (MDNS.begin("esp32-audio")) {
    Serial.println("[mDNS] Truy cập Web Server tại: http://esp32-audio.local");
  }

  server.on("/", handleRoot);
  server.on("/say", HTTP_POST, handleSay);
  server.on("/play", HTTP_POST, handlePlay);
  server.on("/test_sound", handleTestSound);
  server.on("/mic-test", handleMicTest);
  server.on("/bt-connect", handleBtConnect);
  server.on("/reset_wifi", handleResetWifi);
  server.begin();
  Serial.println("[WEB] Web Server quản trị đã sẵn sàng.");

  // Cấp bộ đệm và mở luồng audio NGAY BÂY GIỜ, trước khi bật Bluetooth.
  //
  // Đây là chỗ trước đây làm ngược và phải trả giá. Khi A2DP đã phát, nó chiếm
  // sóng 2.4 GHz gần như liên tục, và gói bắt tay TCP không chen nổi -- mọi lần
  // mở socket đều hết giờ ở 3 giây, rồi ở cả 12 giây. Nới hạn chờ chỉ là kéo dài
  // cơn đau; mở socket trước khi Bluetooth lên tiếng mới là chữa.
  //
  // Lúc này sóng hoàn toàn rảnh và RAM còn nguyên vẹn, nên bắt tay xong trong
  // chưa tới một giây. Socket mở rồi thì giữ luôn, không phải mở lại.
  allocAudioBuffer();
  Serial.println("[STREAM] Mo luong audio truoc khi bat Bluetooth (song con ranh)...");
  connectAudioStream();

  // Cấu hình Bluetooth A2DP Source tới Loa ngoài
  a2dp_source.set_ssid_callback(ssid_callback);
  a2dp_source.set_on_connection_state_changed(connection_state_changed); 
  a2dp_source.set_data_callback(get_sound_data);

  // Bat Secure Simple Pairing. Thu vien mac dinh TAT (ssp_enabled = false trong
  // BluetoothA2DPSource.cpp:65) va lui ve kieu ghep noi cu voi ma PIN "1234".
  //
  // SSP la cach ghep noi chuan tu Bluetooth 2.1, gan nhu moi loa hien dai deu
  // dung. Khi tat, thu vien bo qua ca su kien ESP_BT_GAP_CFM_REQ_EVT -- tuc loa
  // hoi xac nhan ghep noi ma ESP32 khong tra loi, nen loa cho roi bo cuoc. Nhin
  // tu phia mach chi thay "CONNECTING roi het gio", khong he co thong bao loi.
  a2dp_source.set_ssp_enabled(true);

  // Co MAC thi ket noi thang, khong co thi quay ve quet theo ten.
  if (USE_FIXED_SPEAKER_MAC) {
    a2dp_source.set_auto_reconnect(FIXED_SPEAKER_MAC, 5);
    Serial.printf("[A2DP] Ket noi thang toi %02X:%02X:%02X:%02X:%02X:%02X (bo qua buoc quet)\n",
                  FIXED_SPEAKER_MAC[0], FIXED_SPEAKER_MAC[1], FIXED_SPEAKER_MAC[2],
                  FIXED_SPEAKER_MAC[3], FIXED_SPEAKER_MAC[4], FIXED_SPEAKER_MAC[5]);
  } else {
    a2dp_source.set_auto_reconnect(false);
  }

  // A2DP chỉ cần Bluetooth Classic (BR/EDR). Controller mặc định giữ sẵn cả phần
  // RAM cho BLE -- ở đây không bao giờ dùng tới. Trả lại trước khi controller khởi
  // tạo, nếu không heap còn ~8 KB sau khi ghép loa và không mở nổi một TCP socket.
  size_t heap_before = ESP.getFreeHeap();
  esp_err_t released = esp_bt_controller_mem_release(ESP_BT_MODE_BLE);
  Serial.printf("[A2DP] Trả lại RAM của BLE: %s | Heap %u -> %u bytes\n",
                released == ESP_OK ? "OK" : "bỏ qua",
                (unsigned)heap_before, (unsigned)ESP.getFreeHeap());

  Serial.println("[A2DP] Bắt đầu quét các loa Bluetooth xung quanh...");
  a2dp_source.start(); 
}

void loop() {
  server.handleClient();

  // Tự động kết nối và duy trì luồng âm thanh từ Server
  Client& client = getAudioClient();
  unsigned long now = millis();

  // Luồng audio đã được mở từ setup(), trước khi Bluetooth bật. Ở đây chỉ còn
  // lo việc nối lại nếu nó rớt.
  //
  // Không còn đóng socket theo trạng thái loa như bản trước. Mở lại một socket
  // trong lúc A2DP đang phát là việc rất khó -- đó chính là chuỗi "het gio bat
  // tay TCP" trong log. Giữ được thì giữ.
  if (WiFi.status() == WL_CONNECTED && !client.connected()) {
    is_stream_connected = false;
    if (now - lastStreamReconnect > 5000) {
      lastStreamReconnect = now;
      connectAudioStream();
    }
  }

  // Đọc dữ liệu audio stream từ Server đưa vào Ring Buffer để phát ra Loa
  while (audioBuffer && client.connected() && client.available()) {
    // Vùng trống liền kề tới cuối mảng, để đọc được cả khối một lần. 44.1 kHz
    // stereo là 176 KB/s -- gọi read() từng byte là phí CPU mà board không dư.
    int room = (tail >= head) ? ((int)AUDIO_BUFFER_SIZE - tail - (head == 0 ? 1 : 0))
                              : (head - tail - 1);
    if (room <= 0) break;

    int got = client.read(audioBuffer + tail, room);
    if (got <= 0) break;
    tail = (tail + got) & audioBufferMask;
    stat_bytes_from_server += (uint32_t)got;
  }

  // ------------------------------------------------------------ chan doan loa
  // Ba con so tach bach ba doan duong: server -> board (stat_bytes_from_server),
  // board -> loa (stat_bytes_to_speaker), va cho noi giua (buffer + underrun).
  // Nhin mot dong la biet doan nao dung.
  static uint32_t prev_to_speaker = 0, prev_from_server = 0, prev_underruns = 0, prev_cb_calls = 0;

  uint32_t to_speaker = stat_bytes_to_speaker;
  if (to_speaker != prev_to_speaker) lastAudioSeenAt = now;

  bool playing_now = (to_speaker > 0) && (now - lastAudioSeenAt < 1000);
  if (playing_now != speaker_was_playing) {
    speaker_was_playing = playing_now;
    Serial.println(playing_now
      ? "[A2DP] >>> Bat dau co tieng tu server, dang phat ra loa."
      : "[A2DP] <<< Het tieng, loa tro ve trang thai cho.");
  }

  if (now - lastStatusLog >= 5000) {
    lastStatusLog = now;

    uint32_t d_speaker = to_speaker - prev_to_speaker;
    uint32_t d_server  = stat_bytes_from_server - prev_from_server;
    uint32_t d_under   = stat_underruns - prev_underruns;
    uint32_t d_calls   = stat_cb_calls - prev_cb_calls;
    prev_to_speaker  = to_speaker;
    prev_from_server = stat_bytes_from_server;
    prev_underruns   = stat_underruns;
    prev_cb_calls    = stat_cb_calls;

    Serial.printf("[TRANG THAI] BT:%s | Stream:%s | Server->%u B/5s | Loa<-%u B/5s | Buffer:%d/%d | Hut:%u | Mic:%d | Heap:%u\n",
                  is_bt_connected ? "OK" : "chua ghep",
                  is_stream_connected ? "OK" : "chua noi",
                  (unsigned)d_server, (unsigned)d_speaker,
                  availableBuffer(), (int)AUDIO_BUFFER_SIZE,
                  (unsigned)d_under, (unsigned)d_calls, micLevel(),
                  (unsigned)ESP.getFreeHeap());

    // Moi truong hop duoi day can sua mot thu khac han nhau.
    if (!is_bt_connected) {
      Serial.println("[CHAN DOAN] Chua ghep duoc loa Bluetooth. Bat loa, de che do ghep noi, kiem tra TARGET_SPEAKER_NAME.");
    } else if (!is_stream_connected) {
      Serial.println("[CHAN DOAN] Loa da ghep nhung chua mo duoc luong audio tu server. Xem log [STREAM] phia tren.");
    } else if (d_server > 0 && d_speaker == 0 && d_calls == 0) {
      Serial.println("[CHAN DOAN] Bluedroid KHONG HE goi callback xin du lieu (CB=0). Loi nam o tang A2DP, khong phai o bo dem.");
    } else if (d_server > 0 && d_speaker == 0 && d_calls > 0) {
      Serial.println("[CHAN DOAN] Bluedroid CO goi callback nhung ham thoat som truoc khi doc bo dem. Loi nam o logic cua ta.");
    } else if (d_under > 0) {
      Serial.println("[CHAN DOAN] Tieng bi hut giua chung: mang khong kip, hoac loop() bi chan qua lau (vi du dang test mic).");
    }
    // d_server == 0 la trang thai binh thuong khi khong ai hoi gi -- khong canh bao.
  }
}
