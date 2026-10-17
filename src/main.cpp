#include <Arduino.h>
#include <TFT_eSPI.h>
#include <AnimatedGIF.h>
#include <LittleFS.h>

#define GIF_W 128
#define GIF_H 160

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

// ── Setup ──────────────────────────────────────────────────────────────────
void setup() {
  tft.init();
  tft.setRotation(0);
  tft.fillScreen(TFT_BLACK);

  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== ESP32 GIF PLAYER BOOT ===");

  if (!LittleFS.begin()) {
    Serial.println("ERROR: LittleFS mount failed!");
    tft.setTextColor(TFT_RED);
    tft.drawString("FS MOUNT FAILED", 5, 50);
    return;
  }
  Serial.println("LittleFS mounted OK.");

  if (!LittleFS.exists("/anim.gif")) {
    Serial.println("ERROR: /anim.gif not found in LittleFS!");
    tft.setTextColor(TFT_YELLOW);
    tft.drawString("NO /anim.gif", 5, 50);
    return;
  }

  // Clear framebuffer to black
  memset(frameBuf, 0, sizeof(frameBuf));

  gif.begin(LITTLE_ENDIAN_PIXELS);

  if (!gif.open("/anim.gif", GIFOpenFile, GIFCloseFile, GIFReadFile, GIFSeekFile, GIFDraw)) {
    Serial.printf("ERROR: gif.open failed, code %d\n", gif.getLastError());
    tft.setTextColor(TFT_RED);
    tft.drawString("GIF OPEN FAILED", 5, 50);
    return;
  }

  Serial.printf("GIF opened! Canvas: %dx%d\n", gif.getCanvasWidth(), gif.getCanvasHeight());
  Serial.println("Starting animation loop...");
}

// ── Loop ───────────────────────────────────────────────────────────────────
int cycleCount = 0;

void loop() {
  int frameDelay = 0;
  int frameCount = 0;
  int rc = 1;

  while (rc > 0) {
    long tStart = millis();

    rc = gif.playFrame(false, &frameDelay);
    if (rc >= 0) {
      // Push updated framebuffer to TFT in one shot
      tft.startWrite();
      tft.setAddrWindow(0, 0, GIF_W, GIF_H);
      tft.pushColors(frameBuf, GIF_W * GIF_H, true);
      tft.endWrite();

      frameCount++;

      if (frameCount % 20 == 0) {
        Serial.printf("Cycle %d: frame %d (delay %d ms)\n", cycleCount + 1, frameCount, frameDelay);
      }

      // Frame rate control
      long elapsed = millis() - tStart;
      int targetDelay = (frameDelay > 0) ? frameDelay : 65;
      if (elapsed < targetDelay) {
        delay(targetDelay - elapsed);
      } else {
        yield();
      }
    } else {
      Serial.printf("playFrame error: %d\n", gif.getLastError());
      break;
    }
  }

  cycleCount++;
  Serial.printf("Cycle %d completed (%d frames). Looping...\n", cycleCount, frameCount);

  if (rc < 0) {
    Serial.printf("Recovering from error, re-opening GIF...\n");
    gif.close();
    delay(100);
    gif.open("/anim.gif", GIFOpenFile, GIFCloseFile, GIFReadFile, GIFSeekFile, GIFDraw);
  } else {
    // Rewind GIF for next cycle
    gif.reset();
  }
}