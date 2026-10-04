#ifndef AUDIO_HANDLER_H
#define AUDIO_HANDLER_H

#include <Arduino.h>

void setupINMP441();
int readINMP441SoundLevel();
void recordAndSendAudio(int seconds);

#endif