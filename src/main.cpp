#include <Arduino.h>
#include <TFT_eSPI.h>
#include <AnimatedGIF.h>
#include <LittleFS.h>
#include "idle_bg.h"

#define GIF_W 128
#define GIF_H 160

// ── Serial draw command protocol ──────────────────────────────────────────
#define MAGIC_0             0xAB
#define MAGIC_1             0xCD
#define CMD_LINE            0x01   // x0, y0, x1, y1, r, g, b
#define CMD_ERASE           0x02   // clear screen to black
#define CMD_SYNC            0x03   // keepalive / sync
#define CMD_ROTATE          0x04   // rotation: 0=portrait, 1=landscape, 2=inv portrait, 3=inv landscape

// How long with no serial data before returning to idle/video loop (ms)
#define AIRDRAW_TIMEOUT_MS  3000

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

// ── Draw Brush Helper ─────────────────────────────────────────────────────────
void drawBrush(int x0, int y0, int x1, int y1, uint16_t color, int r = 0) {
  tft.drawLine(x0, y0, x1, y1, color);
  for (int d = 1; d <= r; d++) {
    tft.drawLine(x0 - d, y0, x1 - d, y1, color);
    tft.drawLine(x0 + d, y0, x1 + d, y1, color);
    tft.drawLine(x0, y0 - d, x1, y1 - d, color);
    tft.drawLine(x0, y0 + d, x1, y1 + d, color);
  }
  if (r > 0) {
    tft.fillCircle(x0, y0, r, color);
    tft.fillCircle(x1, y1, r, color);
  }
}

// ── Air-Draw Mode ─────────────────────────────────────────────────────────────
// Receives lightweight draw commands (CMD_LINE, CMD_ERASE, CMD_SYNC)
// from air_draw.py over 115200 baud Serial.
enum ParserState {
  STATE_MAGIC_0,
  STATE_MAGIC_1,
  STATE_CMD,
  STATE_ARGS
};

void runAirDrawMode() {
  Serial.println("[AirDraw] Mode active — horizontal landscape drawing.");
  // Default to right-side landscape rotation (3)
  tft.setRotation(3);
  tft.fillScreen(TFT_BLACK);

  ParserState state = STATE_MAGIC_0;
  uint8_t currentCmd = 0;
  uint8_t argBuf[8];
  uint8_t argIdx = 0;
  uint8_t argExpected = 0;

  uint32_t lastDataTime = millis();

  while (millis() - lastDataTime < AIRDRAW_TIMEOUT_MS) {
    while (Serial.available()) {
      int b = Serial.read();
      if (b < 0) break;
      uint8_t byte = (uint8_t)b;
      lastDataTime = millis();

      switch (state) {
        case STATE_MAGIC_0:
          if (byte == MAGIC_0) state = STATE_MAGIC_1;
          break;

        case STATE_MAGIC_1:
          if (byte == MAGIC_1) {
            state = STATE_CMD;
          } else if (byte == MAGIC_0) {
            state = STATE_MAGIC_1;
          } else {
            state = STATE_MAGIC_0;
          }
          break;

        case STATE_CMD:
          currentCmd = byte;
          if (currentCmd == CMD_LINE) {
            state = STATE_ARGS;
            argExpected = 7;
            argIdx = 0;
          } else if (currentCmd == CMD_ROTATE) {
            state = STATE_ARGS;
            argExpected = 1;
            argIdx = 0;
          } else if (currentCmd == CMD_ERASE) {
            tft.fillScreen(TFT_BLACK);
            state = STATE_MAGIC_0;
          } else if (currentCmd == CMD_SYNC) {
            state = STATE_MAGIC_0;
          } else {
            state = STATE_MAGIC_0;
          }
          break;

        case STATE_ARGS:
          argBuf[argIdx++] = byte;
          if (argIdx >= argExpected) {
            if (currentCmd == CMD_LINE) {
              uint8_t x0    = argBuf[0];
              uint8_t y0    = argBuf[1];
              uint8_t x1    = argBuf[2];
              uint8_t y1    = argBuf[3];
              uint8_t r     = argBuf[4];
              uint8_t g     = argBuf[5];
              uint8_t b_col = argBuf[6];
              uint16_t col  = tft.color565(r, g, b_col);
              drawBrush(x0, y0, x1, y1, col, 0);
            } else if (currentCmd == CMD_ROTATE) {
              uint8_t rot = argBuf[0] % 4;
              tft.setRotation(rot);
              tft.fillScreen(TFT_BLACK);
            }
            state = STATE_MAGIC_0;
          }
          break;
      }
    }
    yield();
    delay(2);
  }

  // Restore portrait rotation for idle background and video
  tft.setRotation(0);
  Serial.println("[AirDraw] Session ended — restoring idle.");
}

// ── Setup ─────────────────────────────────────────────────────────────────────
void setup() {
  tft.init();
  tft.setRotation(0);
  tft.fillScreen(TFT_BLACK);

  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== ESP32 DESK BUDDY BOOT ===");
  Serial.println("Air-Draw ready @ 115200 baud.");

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
  // Check for incoming air-draw start
  if (Serial.available()) {
    if (Serial.peek() == MAGIC_0) {
      runAirDrawMode();
      showIdleScreen();
      return;
    } else {
      Serial.read();
    }
  }

  // Normal idle + periodic video loop
  showIdleScreen();
  cycleCount++;
  Serial.printf("\n[Cycle %d] Idle active. Next video in %lu min...\n",
                cycleCount, IDLE_INTERVAL_MS / 60000UL);

  unsigned long startWait = millis();
  unsigned long lastPrint = startWait;

  while (millis() - startWait < IDLE_INTERVAL_MS) {
    if (Serial.available()) {
      if (Serial.peek() == MAGIC_0) {
        runAirDrawMode();
        showIdleScreen();
        return;
      } else {
        Serial.read();
      }
    }

    delay(20);
    yield();

    // Serial heartbeat every minute
    if (millis() - lastPrint >= 60000UL) {
      lastPrint = millis();
      unsigned long rem = (IDLE_INTERVAL_MS - (millis() - startWait)) / 1000UL;
      Serial.printf("Next walk in %lu min %lu sec...\n", rem / 60, rem % 60);
    }
  }

  // Play video
  Serial.println("\n>>> Desk Buddy is walking! <<<");
  playVideoOnce();
}