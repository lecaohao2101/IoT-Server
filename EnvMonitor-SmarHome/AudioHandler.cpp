#include "AudioHandler.h"
#include "Config.h"
#include "Controls.h"
#include <driver/i2s.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

void setupINMP441() {
  i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = 16000,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = i2s_comm_format_t(I2S_COMM_FORMAT_STAND_I2S),
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 4,
    .dma_buf_len = 512,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
  };

  i2s_pin_config_t pin_config = {
    .bck_io_num = PIN_I2S_SCK,
    .ws_io_num = PIN_I2S_WS,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = PIN_I2S_SD
  };

  i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
  i2s_set_pin(I2S_PORT, &pin_config);
}

int readINMP441SoundLevel() {
  int32_t samples[128];
  size_t bytes_read = 0;
  
  i2s_read(I2S_PORT, &samples, sizeof(samples), &bytes_read, portMAX_DELAY);
  int samples_read = bytes_read / sizeof(int32_t);
  if (samples_read <= 0) return 0;

  int64_t sum = 0;
  for (int i = 0; i < samples_read; i++) {
    int32_t val = samples[i] >> 14; 
    sum += abs(val);
  }
  
  int avg_amplitude = sum / samples_read;
  return map(constrain(avg_amplitude, 0, 2000), 0, 2000, 0, 100);
}

void recordAndSendAudio(int seconds) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[Audio] WiFi chưa kết nối, bỏ qua thu âm!");
    return;
  }

  int total_samples = 16000 * seconds;
  int buffer_size_bytes = total_samples * sizeof(int16_t);

  int16_t *pcm_buffer = (int16_t*) malloc(buffer_size_bytes);
  if (!pcm_buffer) {
    Serial.println("[Audio] Lỗi: Không đủ RAM!");
    return;
  }

  Serial.printf("[Audio] Đang thu âm %d giây từ mic INMP441...\n", seconds);
  
  int samples_read_total = 0;
  int32_t i2s_raw[256];

  while (samples_read_total < total_samples) {
    size_t bytes_read = 0;
    i2s_read(I2S_PORT, &i2s_raw, sizeof(i2s_raw), &bytes_read, portMAX_DELAY);
    int count = bytes_read / sizeof(int32_t);

    for (int i = 0; i < count && samples_read_total < total_samples; i++) {
      pcm_buffer[samples_read_total++] = (int16_t)(i2s_raw[i] >> 14);
    }
  }

  Serial.println("[Audio] Thu âm xong! Đang gửi lên Python API...");

  HTTPClient http;
  WiFiClientSecure secureClient;
  bool isHttps = String(SERVER_API_URL).startsWith("https");

  if (isHttps) {
    secureClient.setInsecure();
    http.begin(secureClient, String(SERVER_API_URL) + "/upload-audio");
  } else {
    http.begin(String(SERVER_API_URL) + "/upload-audio");
  }
  http.addHeader("Content-Type", "application/octet-stream");

  int httpResponseCode = http.POST((uint8_t*)pcm_buffer, buffer_size_bytes);

  if (httpResponseCode > 0) {
    Serial.printf("[Audio] Gửi thành công! Code: %d\n", httpResponseCode);
    if (httpResponseCode == HTTP_CODE_OK) {
      String response = http.getString();
      Serial.printf("[Audio Response]: %s\n", response.c_str());
      int statesPos = response.indexOf("\"states\":");
      if (statesPos != -1) {
        auto parseVal = [&](const char* key) -> int {
          int keyPos = response.indexOf(key, statesPos);
          if (keyPos == -1) return -1;
          int colPos = response.indexOf(':', keyPos);
          if (colPos == -1) return -1;
          return response.substring(colPos + 1).toInt();
        };
        int lr_m  = parseVal("\"lr_main\"");
        int lr_s  = parseVal("\"lr_sofa\"");
        int kit   = parseVal("\"kit_main\"");
        int bed_m = parseVal("\"bed_main\"");
        int bed_s = parseVal("\"bed_side\"");
        int stdy  = parseVal("\"study\"");
        int bal   = parseVal("\"balcony\"");
        int wc_l  = parseVal("\"wc\"");
        int lr_a  = parseVal("\"lr_angle\"");
        int bed_a = parseVal("\"bed_angle\"");
        syncAllFromStates(lr_m, lr_s, kit, bed_m, bed_s, stdy, bal, wc_l, lr_a, bed_a);
      }
    }
  } else {
    Serial.printf("[Audio] Lỗi gửi: %s\n", http.errorToString(httpResponseCode).c_str());
  }

  http.end();
  free(pcm_buffer);
}