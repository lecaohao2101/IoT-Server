#ifndef AUDIO_HANDLER_H
#define AUDIO_HANDLER_H

#include <Arduino.h>

void setupINMP441();

// Mức âm thanh 0..100 của khung mic gần nhất. Giá trị được voiceLoop() cập nhật
// liên tục, nên hàm này không còn tự đọc I2S và không tranh DMA với luồng thoại.
int readINMP441SoundLevel();

// Trợ lý giọng nói thời gian thực. voiceBegin() mở WebSocket tới /ws/voice;
// voiceLoop() phải được gọi mỗi vòng loop() -- nó bơm socket, đọc mic và đẩy
// tiếng nói đi ngay khi người dùng còn đang nói.
void voiceBegin();
void voiceLoop();

// Ép mở một câu nói (nút bấm). Bình thường cổng âm thanh tự mở khi có tiếng.
void voiceRequestTalk();

bool voiceIsConnected();
bool voiceIsTalking();

#endif
