#include <Arduino.h>
#include <TFT_eSPI.h>
#include <AnimatedGIF.h>
#include <LittleFS.h>
#include "idle_bg.h"

#define GIF_W 128
#define GIF_H 160

// Time to stay on the idle screen between video plays (default: 20 minutes)
const unsigned long IDLE_INTERVAL_MS = 20UL * 60UL * 1000UL;

TFT_eSPI tft = TFT_eSPI();
AnimatedGIF gif;

// Framebuffer in RAM: 128x160 x 2 bytes = 40 KB
// Holds composited frames so delta frames with transparency composite seamlessly
uint16_t frameBuf[GIF_W * GIF_H];

// ── LittleFS file handle & callbacks ───────────────────────────────────────
static File s_gifFile;

void *GIFOpenFile(const char *fname, int32_t *pSize) {
  s_gifFile = LittleFS.open(fname, "r");
  if (s_gifFile) {
    *pSize = s_gifFile.size();
    return &s_gifFile;
  }
  return nullptr;
}

void GIFCloseFile(void *h) {
  if (h) {
    ((File*)h)->close();
  }
}

int32_t GIFReadFile(GIFFILE *p, uint8_t *b, int32_t n) {
  if (!p || !p->fHandle) return 0;
  int32_t bytesRead = ((File*)p->fHandle)->read(b, n);
  if (bytesRead > 0) {
    p->iPos += bytesRead;  // Crucial: AnimatedGIF relies on iPos tracking
  }
  return bytesRead;
}

int32_t GIFSeekFile(GIFFILE *p, int32_t pos) {
  if (!p || !p->fHandle) return 0;
  ((File*)p->fHandle)->seek(pos);
  p->iPos = pos;  // Crucial: AnimatedGIF relies on iPos tracking
  return pos;
}

// ── GIFDraw callback ───────────────────────────────────────────────────────
// Decodes each scanline into the 40 KB RAM framebuffer
void GIFDraw(GIFDRAW *pDraw) {
  int y = pDraw->iY + pDraw->y;
  if (y >= GIF_H) return;

  uint16_t *row = &frameBuf[y * GIF_W + pDraw->iX];
  uint8_t  *s   = pDraw->pPixels;
  int iCount    = min((int)pDraw->iWidth, GIF_W - pDraw->iX);

  if (pDraw->ucHasTransparency) {
    for (int i = 0; i < iCount; i++) {
      uint8_t idx = s[i];
      if (idx != pDraw->ucTransparent) {
        row[i] = pDraw->pPalette[idx];
      }
    }
  } else {
    for (int i = 0; i < iCount; i++) {
      row[i] = pDraw->pPalette[s[i]];
    }
  }
}

// ── Display Idle Screen ───────────────────────────────────────────────────
void showIdleScreen() {
  bool loaded = false;
  if (LittleFS.exists("/idle.bin")) {
    File f = LittleFS.open("/idle.bin", "r");
    if (f && f.size() == sizeof(frameBuf)) {
      f.read((uint8_t*)frameBuf, sizeof(frameBuf));
      f.close();
      loaded = true;
    }
  }
  if (!loaded) {
    memcpy_P(frameBuf, idle_bg, sizeof(frameBuf));
  }

  tft.startWrite();
  tft.setAddrWindow(0, 0, GIF_W, GIF_H);
  tft.pushColors(frameBuf, GIF_W * GIF_H, true);
  tft.endWrite();
}

// ── Play Video Animation Once ──────────────────────────────────────────────
void playVideoOnce() {
  if (!LittleFS.exists("/anim.gif")) {
    Serial.println("ERROR: /anim.gif not found in LittleFS!");
    return;
  }

  if (!gif.open("/anim.gif", GIFOpenFile, GIFCloseFile, GIFReadFile, GIFSeekFile, GIFDraw)) {
    Serial.printf("ERROR: gif.open failed, code %d\n", gif.getLastError());
    return;
  }

  int frameDelay = 0;
  int frameCount = 0;
  int rc = 1;

  while (rc > 0) {
    long tStart = millis();

    rc = gif.playFrame(false, &frameDelay);
    if (rc >= 0) {
      tft.startWrite();
      tft.setAddrWindow(0, 0, GIF_W, GIF_H);
      tft.pushColors(frameBuf, GIF_W * GIF_H, true);
      tft.endWrite();

      frameCount++;

      long elapsed = millis() - tStart;
      int targetDelay = (frameDelay > 0) ? frameDelay : 65;
      if (elapsed < targetDelay) {
        delay(targetDelay - elapsed);
      } else {
        yield();
      }
    }
  }

  gif.close();
  Serial.printf("Video playback complete (%d frames).\n", frameCount);
}

// ── Setup ──────────────────────────────────────────────────────────────────
void setup() {
  tft.init();
  tft.setRotation(0);
  tft.fillScreen(TFT_BLACK);

  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== ESP32 DESK BUDDY BOOT ===");

  if (!LittleFS.begin()) {
    Serial.println("WARNING: LittleFS mount failed (will use fallback idle background)");
  } else {
    Serial.println("LittleFS mounted OK.");
  }

  gif.begin(LITTLE_ENDIAN_PIXELS);

  // Show idle screen immediately on power-up
  showIdleScreen();
  Serial.println("Idle background active.");
}

// ── Loop ───────────────────────────────────────────────────────────────────
int cycleCount = 0;

void loop() {
  // 1. Display full-screen idle background
  showIdleScreen();
  cycleCount++;
  Serial.printf("\n[Cycle %d] Idle screen active. Waiting %lu minutes for next video...\n",
                cycleCount, IDLE_INTERVAL_MS / 60000UL);

  // 2. Wait for 20 minutes (with periodic serial heartbeat & watchdog feeding)
  unsigned long startWait = millis();
  unsigned long lastPrint = startWait;

  while (millis() - startWait < IDLE_INTERVAL_MS) {
    delay(500); // Yield & feed FreeRTOS idle task

    // Print remaining time to serial every minute
    if (millis() - lastPrint >= 60000UL) {
      lastPrint = millis();
      unsigned long remainingSec = (IDLE_INTERVAL_MS - (millis() - startWait)) / 1000UL;
      Serial.printf("Next walk in %lu min %lu sec...\n", remainingSec / 60, remainingSec % 60);
    }
  }

  // 3. Play video animation once
  Serial.println("\n>>> Desk Buddy is walking! <<<");
  playVideoOnce();
}