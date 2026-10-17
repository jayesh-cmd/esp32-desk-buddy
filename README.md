# ESP32 Video & GIF Player 🎬

A high-performance video and animated GIF player for **ESP32** and **ST7735 (128×160) SPI TFT** displays.

Convert any video (`.mp4`, `.mov`, `.mkv`) or GIF directly into an optimized filesystem image and flash it to the ESP32 in a single command. The firmware decodes frames from flash memory into an internal 40 KB RAM framebuffer and pushes frames over 27 MHz SPI with smooth, infinite looping.

---

## ✨ Features

* **High-Speed Playback**: Smooth 15–20 fps video playback powered by [BitBank's AnimatedGIF](https://github.com/bitbank2/AnimatedGIF) and [Bodmer's TFT_eSPI](https://github.com/Bodmer/TFT_eSPI).
* **RAM Framebuffer Compositing**: 40 KB RAM buffer composites delta frames and transparency without flickering, tearing, or black cutouts.
* **LittleFS Flash Storage**: Videos are stored in the ESP32's onboard flash partition (`~1.4 MB`), avoiding firmware size limits and memory exhaustion.
* **All-in-One CLI Tool (`flash_video.py`)**:
  * Converts any video format to 128×160 using FFmpeg.
  * Adaptive palette optimization (128 colors).
  * Auto-detects connected ESP32 serial port.
  * Supports rotation (`--rotate left/right/180`) and fitting (`--fit stretch/crop/pad`).
  * Generates the LittleFS filesystem image and flashes it via `esptool`.

---

## 🛠️ Hardware Requirements & Wiring

* **MCU**: ESP32 Dev Board (ESP-WROOM-32 / ESP32-D0WD)
* **Display**: 1.8" ST7735 SPI TFT Display (128×160, 1.8V/3.3V logic)

### Pinout Connection Table

| Display Pin (ST7735) | ESP32 GPIO | Description |
| :--- | :--- | :--- |
| **VCC** | `3.3V` / `5V` | Display Power |
| **GND** | `GND` | Ground |
| **CS** | `GPIO 5` | Chip Select (VSPI CS) |
| **RESET / RST** | `GPIO 4` | Display Hardware Reset |
| **A0 / DC** | `GPIO 2` | Data / Command Control |
| **SDA / MOSI** | `GPIO 23` | SPI MOSI (Master Out) |
| **SCK / SCLK** | `GPIO 18` | SPI Clock |
| **LED / BLK** | `3.3V` | Backlight Anode |

*(SPI Frequency is clocked at 27 MHz in `platformio.ini` for smooth transfers).*

---

## 📦 Prerequisites

1. **PlatformIO**: Install via VSCode extension or command line:
   ```bash
   brew install platformio    # macOS
   # or
   pip install platformio
   ```
2. **FFmpeg**: Required for video transcoding:
   ```bash
   brew install ffmpeg        # macOS
   sudo apt install ffmpeg    # Ubuntu/Debian
   ```
3. **Python Libraries**:
   ```bash
   pip install littlefs-python esptool pyserial
   ```

---

## 🚀 Quick Start

### 1. Flash the Firmware
Connect your ESP32 via USB and compile/upload the player firmware:
```bash
pio run -t upload
```

### 2. Flash a Video
Flash the included sample video (`1015.mp4`):
```bash
python3 flash_video.py 1015.mp4 --rotate left --fit stretch
```
* **`--rotate left`**: Rotates 90° counter-clockwise so widescreen videos fill the display when held horizontally.
* **`--fit stretch`**: Fills the exact 128×160 display area with no black bars.

Your display will reboot and begin playing the animation immediately in a continuous loop!

---

## 🕹️ CLI Usage (`flash_video.py`)

```text
usage: flash_video.py [-h] [--fps FPS] [--rotate {none,left,right,180}]
                      [--fit {stretch,crop,pad}] [--port PORT] input
```

### Options:
| Flag | Default | Description |
| :--- | :--- | :--- |
| `input` | *(required)* | Path to video (`.mp4`, `.mov`) or `.gif` file |
| `--fps` | `15` | Playback frame rate (10–20 fps recommended) |
| `--rotate` | `none` | Rotation: `left` (90° CCW), `right` (90° CW), `180`, `none` |
| `--fit` | `stretch` | Fit mode: `stretch` (full-screen, no bars), `crop`, or `pad` (letterbox) |
| `--port` | *auto* | Serial port (e.g. `/dev/cu.usbserial-110`); auto-detected if omitted |

### Examples:
* **Flash another video in widescreen**:
  ```bash
  python3 flash_video.py my_clip.mp4 --rotate left --fps 15
  ```
* **Flash an existing animated GIF directly**:
  ```bash
  python3 flash_video.py animation.gif
  ```
* **Longer video with reduced framerate to save space**:
  ```bash
  python3 flash_video.py long_clip.mp4 --fps 10
  ```

---

## 🔍 Serial Monitor & Debugging

To observe real-time frame telemetry, frame delay, and loop cycle counters:
```bash
pio device monitor --baud 115200
```
Expected output:
```text
=== ESP32 GIF PLAYER BOOT ===
LittleFS mounted OK.
GIF opened! Canvas: 128x160
Starting animation loop...
Cycle 1: frame 20 (delay 66 ms)
Cycle 1: frame 40 (delay 66 ms)
...
Cycle 1 completed (150 frames). Looping...
```

---

## 📂 Project Structure

```text
├── 1015.mp4           # Included sample video
├── data/              # LittleFS filesystem source (contains anim.gif)
├── flash_video.py     # All-in-one transcoding and flashing CLI
├── platformio.ini     # Hardware pins, build flags & libraries configuration
├── src/
│   └── main.cpp       # ESP32 firmware (LittleFS reader + AnimatedGIF player)
└── README.md
```

---

## 📄 License
MIT License. Free to use and modify for personal and commercial projects.
