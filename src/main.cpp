#include <Arduino.h>
#include <TFT_eSPI.h>
#include <AnimatedGIF.h>
#include <LittleFS.h>
#include "idle_bg.h"

#define GIF_W 128
#define GIF_H 160

// ── Serial frame protocol ─────────────────────────────────────────────────
// Header: 0xDE 0xAD + 2-byte little-endian payload size (40960)
#define FRAME_MAGIC_0   0xDE
#define FRAME_MAGIC_1   0xAD
#define FRAME_PAYLOAD   (GIF_W * GIF_H * 2)   // 40 960 bytes

// How long with no serial data before returning to idle/video loop (ms)
#define AIRDRAW_TIMEOUT_MS  2000

// Time to stay on the idle screen between video plays (default: 20 minutes)
const unsigned long IDLE_INTERVAL_MS = 20UL * 60UL * 1000UL;

TFT_eSPI tft = TFT_eSPI();
AnimatedGIF gif;

// Framebuffer in RAM: 128×160 × 2 bytes = 40 KB
uint16_t frameBuf[GIF_W * GIF_H];

// ── LittleFS file handle & callbacks ────────────────────────────────────────
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
  if (h) ((File*)h)->close();
}

int32_t GIFReadFile(GIFFILE *p, uint8_t *b, int32_t n) {
  if (!p || !p->fHandle) return 0;
  int32_t bytesRead = ((File*)p->fHandle)->read(b, n);
  if (bytesRead > 0) p->iPos += bytesRead;
  return bytesRead;
}

int32_t GIFSeekFile(GIFFILE *p, int32_t pos) {
  if (!p || !p->fHandle) return 0;
  ((File*)p->fHandle)->seek(pos);
  p->iPos = pos;
  return pos;
}

// ── GIFDraw callback ─────────────────────────────────────────────────────────
void GIFDraw(GIFDRAW *pDraw) {
  int y = pDraw->iY + pDraw->y;
  if (y >= GIF_H) return;

  uint16_t *row = &frameBuf[y * GIF_W + pDraw->iX];
  uint8_t  *s   = pDraw->pPixels;
  int iCount    = min((int)pDraw->iWidth, GIF_W - pDraw->iX);

  if (pDraw->ucHasTransparency) {
    for (int i = 0; i < iCount; i++) {
      uint8_t idx = s[i];
      if (idx != pDraw->ucTransparent) row[i] = pDraw->pPalette[idx];
    }
  } else {
    for (int i = 0; i < iCount; i++) {
      row[i] = pDraw->pPalette[s[i]];
    }
  }
}

// ── Push frameBuf to display ─────────────────────────────────────────────────
void flushFrameBuf() {
  tft.startWrite();
  tft.setAddrWindow(0, 0, GIF_W, GIF_H);
  tft.pushColors(frameBuf, GIF_W * GIF_H, true);
  tft.endWrite();
}

// ── Display Idle Screen ───────────────────────────────────────────────────────
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
  flushFrameBuf();
}

// ── Play Video Animation Once ─────────────────────────────────────────────────
void playVideoOnce() {
  if (!LittleFS.exists("/anim.gif")) {
    Serial.println("ERROR: /anim.gif not found in LittleFS!");
    return;
  }
  if (!gif.open("/anim.gif", GIFOpenFile, GIFCloseFile, GIFReadFile, GIFSeekFile, GIFDraw)) {
    Serial.printf("ERROR: gif.open failed, code %d\n", gif.getLastError());
    return;
  }

  int frameDelay = 0, frameCount = 0, rc = 1;
  while (rc > 0) {
    long tStart = millis();
    rc = gif.playFrame(false, &frameDelay);
    if (rc >= 0) {
      flushFrameBuf();
      frameCount++;
      long elapsed     = millis() - tStart;
      int  targetDelay = (frameDelay > 0) ? frameDelay : 65;
      if (elapsed < targetDelay) delay(targetDelay - elapsed);
      else yield();
    }
  }
  gif.close();
  Serial.printf("Video playback complete (%d frames).\n", frameCount);
}

// ── Air-Draw Mode ─────────────────────────────────────────────────────────────
// Reads raw RGB565 frames sent by air_draw.py over USB Serial.
// Protocol: 0xDE 0xAD + 2-byte little-endian size (40960) + payload bytes.
// Returns when no data is received for AIRDRAW_TIMEOUT_MS milliseconds.

void runAirDrawMode() {
  Serial.println("[AirDraw] Entering air-draw mode...");

  uint8_t  rxBuf[FRAME_PAYLOAD];   // receive buffer for one frame
  uint32_t lastDataTime = millis();
  bool     inHeader     = false;
  uint8_t  headerIdx    = 0;       // 0..3 : step through magic + size bytes
  uint32_t expectedSize = 0;
  uint32_t rxCount      = 0;

  // State machine states
  enum State { WAIT_MAGIC0, WAIT_MAGIC1, READ_SIZE_LO, READ_SIZE_HI, READ_PAYLOAD };
  State state = WAIT_MAGIC0;

  while (true) {
    // Check timeout — return to idle loop if laptop goes silent
    if (millis() - lastDataTime > AIRDRAW_TIMEOUT_MS) {
      Serial.println("[AirDraw] Timeout — returning to idle loop.");
      return;
    }

    if (!Serial.available()) {
      yield();
      continue;
    }

    uint8_t b = (uint8_t)Serial.read();
    lastDataTime = millis();

    switch (state) {
      case WAIT_MAGIC0:
        if (b == FRAME_MAGIC_0) state = WAIT_MAGIC1;
        break;

      case WAIT_MAGIC1:
        if (b == FRAME_MAGIC_1) state = READ_SIZE_LO;
        else                    state = WAIT_MAGIC0;   // re-sync
        break;

      case READ_SIZE_LO:
        expectedSize = b;          // low byte of payload size
        state = READ_SIZE_HI;
        break;

      case READ_SIZE_HI:
        expectedSize |= ((uint32_t)b << 8);   // high byte
        if (expectedSize == FRAME_PAYLOAD) {
          rxCount = 0;
          state   = READ_PAYLOAD;
        } else {
          // Unexpected size — re-sync
          Serial.printf("[AirDraw] Bad size %u, re-syncing.\n", expectedSize);
          state = WAIT_MAGIC0;
        }
        break;

      case READ_PAYLOAD:
        rxBuf[rxCount++] = b;
        if (rxCount >= FRAME_PAYLOAD) {
          // Full frame received — copy to frameBuf and push to display
          memcpy(frameBuf, rxBuf, FRAME_PAYLOAD);
          // RGB565 bytes arrive big-endian from Python (byteswap already done)
          // TFT_eSPI pushColors with swapBytes=true handles big-endian correctly
          flushFrameBuf();
          state = WAIT_MAGIC0;   // ready for next frame
        }
        break;
    }
  }
}

// ── Setup ─────────────────────────────────────────────────────────────────────
void setup() {
  tft.init();
  tft.setRotation(0);
  tft.fillScreen(TFT_BLACK);

  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== ESP32 DESK BUDDY BOOT ===");
  Serial.println("Send air-draw frames within 2s to enter drawing mode.");

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

// ── Loop ──────────────────────────────────────────────────────────────────────
int cycleCount = 0;

void loop() {
  // ── Check for incoming air-draw frames ──────────────────────────────────
  // If the laptop sends the magic header within the first 200 ms of the loop
  // iteration, switch to air-draw mode until the laptop goes silent.
  if (Serial.available()) {
    // Peek at the first byte — if it looks like our magic, enter draw mode
    int peeked = Serial.peek();
    if (peeked == FRAME_MAGIC_0) {
      runAirDrawMode();
      // After drawing session ends, restore idle background
      showIdleScreen();
      return;
    }
  }

  // ── Normal idle + periodic video loop ────────────────────────────────────
  showIdleScreen();
  cycleCount++;
  Serial.printf("\n[Cycle %d] Idle active. Next video in %lu min...\n",
                cycleCount, IDLE_INTERVAL_MS / 60000UL);

  unsigned long startWait = millis();
  unsigned long lastPrint = startWait;

  while (millis() - startWait < IDLE_INTERVAL_MS) {
    delay(200);
    yield();

    // Check for incoming air-draw frames during the wait
    if (Serial.available() && Serial.peek() == FRAME_MAGIC_0) {
      runAirDrawMode();
      showIdleScreen();
      return;   // restart loop() to reset timer
    }

    // Serial heartbeat every minute
    if (millis() - lastPrint >= 60000UL) {
      lastPrint = millis();
      unsigned long rem = (IDLE_INTERVAL_MS - (millis() - startWait)) / 1000UL;
      Serial.printf("Next walk in %lu min %lu sec...\n", rem / 60, rem % 60);
    }
  }

  // ── Play video ────────────────────────────────────────────────────────────
  Serial.println("\n>>> Desk Buddy is walking! <<<");
  playVideoOnce();
}