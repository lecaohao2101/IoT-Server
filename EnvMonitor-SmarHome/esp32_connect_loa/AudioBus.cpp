#include "AudioBus.h"

#include <driver/i2s.h>
#include <math.h>

#define AUDIO_I2S_PORT I2S_NUM_0

// 256 mẫu = 16 ms @16 kHz. Giữ nguyên kích thước khung mà MicUplink vẫn dùng để
// độ trễ đường tiếng nói không đổi khi chuyển sang bus dùng chung.
static constexpr int DMA_BUF_LEN   = 256;
static constexpr int DMA_BUF_COUNT = 6;

// Đệm chuyển đổi 16-bit -> khung 32-bit. 128 mẫu = 512 byte stack, đủ nhỏ để gọi
// từ loop() mà không phải cấp phát động giữa lúc đang phát tiếng.
static constexpr int WRITE_BLOCK = 128;

static bool     bus_ready = false;
static float    out_gain = 1.0f;
static uint32_t samples_played = 0;

bool audioBusReady() { return bus_ready; }
float audioBusGain() { return out_gain; }
uint32_t audioBusSamplesPlayed() { return samples_played; }

void audioBusSetGain(float gain) {
  if (gain < 0.0f) gain = 0.0f;
  if (gain > 8.0f) gain = 8.0f;   // trên mức này thì chỉ còn tiếng vỡ
  out_gain = gain;
}

bool audioBusBegin() {
  if (bus_ready) return true;

  i2s_config_t cfg = {
    // RX | TX trong cùng một port: đây là toàn bộ lý do file này tồn tại.
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_TX),
    .sample_rate = AUDIO_SAMPLE_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = i2s_comm_format_t(I2S_COMM_FORMAT_STAND_I2S),
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = DMA_BUF_COUNT,
    .dma_buf_len = DMA_BUF_LEN,
    .use_apll = false,
    // PHẢI bật. Khi không kịp bơm dữ liệu, DMA sẽ phát lại nguyên đệm cũ -- tức
    // là lặp đi lặp lại 16 ms tiếng cuối cùng, nghe như tiếng rè chói tai. Bật cờ
    // này thì chỗ thiếu được tự lấp bằng số 0, nghĩa là im lặng.
    .tx_desc_auto_clear = true,
    .fixed_mclk = 0
  };

  i2s_pin_config_t pins = {
    .bck_io_num = AUDIO_PIN_BCLK,
    .ws_io_num = AUDIO_PIN_WS,
    .data_out_num = AUDIO_PIN_SPK_DIN,
    .data_in_num = AUDIO_PIN_MIC_SD
  };

  esp_err_t err = i2s_driver_install(AUDIO_I2S_PORT, &cfg, 0, NULL);
  if (err != ESP_OK) {
    Serial.printf("[BUS] Khong cai duoc driver I2S (ma loi %d). Heap con %u byte.\n",
                  (int)err, (unsigned)ESP.getFreeHeap());
    return false;
  }

  err = i2s_set_pin(AUDIO_I2S_PORT, &pins);
  if (err != ESP_OK) {
    Serial.printf("[BUS] Khong gan duoc chan I2S (ma loi %d).\n", (int)err);
    i2s_driver_uninstall(AUDIO_I2S_PORT);
    return false;
  }

  i2s_zero_dma_buffer(AUDIO_I2S_PORT);
  bus_ready = true;

  Serial.printf("[BUS] I2S song cong san sang @%d Hz | BCLK=D%d WS/LRC=D%d | mic SD=D%d | loa DIN=D%d\n",
                AUDIO_SAMPLE_RATE, AUDIO_PIN_BCLK, AUDIO_PIN_WS,
                AUDIO_PIN_MIC_SD, AUDIO_PIN_SPK_DIN);
  return true;
}

void audioBusEnd() {
  if (!bus_ready) return;
  i2s_zero_dma_buffer(AUDIO_I2S_PORT);
  i2s_driver_uninstall(AUDIO_I2S_PORT);
  bus_ready = false;
}

void audioBusMute() {
  if (!bus_ready) return;
  i2s_zero_dma_buffer(AUDIO_I2S_PORT);
}

int audioBusRead(int32_t* raw, int max_samples, uint32_t wait_ms) {
  if (!bus_ready || raw == nullptr || max_samples <= 0) return 0;

  size_t bytes_read = 0;
  esp_err_t err = i2s_read(AUDIO_I2S_PORT, raw, (size_t)max_samples * sizeof(int32_t),
                           &bytes_read, pdMS_TO_TICKS(wait_ms));
  if (err != ESP_OK) return 0;
  return (int)(bytes_read / sizeof(int32_t));
}

size_t audioBusWrite(const int16_t* pcm, size_t samples, uint32_t wait_ms) {
  if (!bus_ready || pcm == nullptr || samples == 0) return 0;

  int32_t block[WRITE_BLOCK];
  size_t written_total = 0;

  while (written_total < samples) {
    size_t n = samples - written_total;
    if (n > WRITE_BLOCK) n = WRITE_BLOCK;

    for (size_t i = 0; i < n; i++) {
      float v = (float)pcm[written_total + i] * out_gain;
      if (v > 32767.0f) v = 32767.0f;
      if (v < -32768.0f) v = -32768.0f;
      // Khung I2S là 32 bit còn mẫu chỉ có 16: dữ liệu đi MSB trước, nên mẫu phải
      // nằm ở NỬA TRÊN của khung. Quên phép dịch này thì tiếng nhỏ đi 65536 lần --
      // loa vẫn chạy, vẫn có clock, chỉ là không nghe thấy gì.
      block[i] = (int32_t)((int16_t)v) << 16;
    }

    size_t bytes_written = 0;
    esp_err_t err = i2s_write(AUDIO_I2S_PORT, block, n * sizeof(int32_t),
                              &bytes_written, pdMS_TO_TICKS(wait_ms));
    if (err != ESP_OK) break;

    size_t done = bytes_written / sizeof(int32_t);
    written_total += done;
    samples_played += (uint32_t)done;
    if (done < n) break;   // DMA đầy và đã hết hạn chờ
  }

  return written_total;
}

void audioBusTone(float hz, uint32_t ms, float amplitude) {
  if (!bus_ready) {
    Serial.println("[BUS] Chua cai I2S, khong phat duoc not thu.");
    return;
  }
  if (amplitude < 0.0f) amplitude = 0.0f;
  if (amplitude > 1.0f) amplitude = 1.0f;

  const uint32_t total = (uint32_t)((uint64_t)AUDIO_SAMPLE_RATE * ms / 1000ULL);
  const float step = 2.0f * (float)M_PI * hz / (float)AUDIO_SAMPLE_RATE;
  const int16_t peak = (int16_t)(amplitude * 32767.0f);

  int16_t block[WRITE_BLOCK];
  float phase = 0.0f;
  uint32_t done = 0;

  while (done < total) {
    uint32_t n = total - done;
    if (n > WRITE_BLOCK) n = WRITE_BLOCK;
    for (uint32_t i = 0; i < n; i++) {
      block[i] = (int16_t)(sinf(phase) * peak);
      phase += step;
      if (phase > 2.0f * (float)M_PI) phase -= 2.0f * (float)M_PI;
    }
    // Chờ lâu ở đây là đúng: hàm này cố ý chặn, và DMA phải nhận đủ thì nốt mới
    // liền mạch thay vì đứt quãng.
    if (audioBusWrite(block, n, 200) == 0) break;
    done += n;
  }
}
