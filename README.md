# ESP32 Desk Buddy 🎬✍️

A project for **ESP32 + ST7735 (128×160) SPI TFT** with two modes:

1. **Video Player** — Flash any `.mp4` / `.gif` to LittleFS and play it in a loop. Displays an idle background image and plays the video once every 20 minutes.
2. **Air Draw** ✨ — Draw in the air with your finger in front of your laptop webcam. Whatever you draw appears on the ESP32 display in real-time over USB serial.

---

## ✨ Features

- **Smooth Video Playback** via [AnimatedGIF](https://github.com/bitbank2/AnimatedGIF) + [TFT_eSPI](https://github.com/Bodmer/TFT_eSPI) at 15–20 fps
- **40 KB RAM Framebuffer** — composites delta frames flicker-free
- **LittleFS Flash Storage** (~1.4 MB partition)
- **20-Minute Idle Timer** — shows background image, plays video periodically
- **Air Draw Mode** — real-time finger-drawing via MediaPipe + OpenCV → USB Serial → TFT display
- **All-in-One Flash CLI** (`flash_video.py`) — convert, rotate, fit, and flash in one command

---

## 🛠️ Hardware Requirements & Wiring

- **MCU**: ESP32 Dev Board (ESP-WROOM-32)
- **Display**: 1.8" ST7735 SPI TFT (128×160, 3.3V logic)

| Display Pin | ESP32 GPIO | Description |
| :--- | :--- | :--- |
| **VCC** | `3.3V` | Power |
| **GND** | `GND` | Ground |
| **CS** | `GPIO 5` | Chip Select |
| **RST** | `GPIO 4` | Reset |
| **DC / A0** | `GPIO 2` | Data/Command |
| **SDA / MOSI** | `GPIO 23` | SPI Data |
| **SCK** | `GPIO 18` | SPI Clock |
| **LED** | `3.3V` | Backlight |

---

## 📦 Prerequisites

### Firmware
```bash
brew install platformio     # macOS
pip install platformio      # or pip
```

### Video flashing
```bash
brew install ffmpeg
pip install littlefs-python esptool pyserial
```

### Air Draw
```bash
pip install opencv-python mediapipe pyserial numpy
```

---

## 🚀 Quick Start

### 1. Flash the firmware
```bash
pio run -t upload
```

### 2. Flash a video
```bash
python3 flash_video.py 1015.mp4 --rotate left --fit stretch
```
The display will reboot and begin the idle → video loop immediately.

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--rotate` | `none` | `left` / `right` / `180` |
| `--fit` | `stretch` | `stretch` / `crop` / `pad` |
| `--fps` | `15` | Playback frame rate |
| `--port` | *auto* | Serial port (auto-detected) |

---

## ✍️ Air Draw Mode

Draw on the ESP32 display in real-time using your index finger in front of your laptop webcam.

### How it works
```
Webcam → MediaPipe hand tracking → Draw canvas → USB Serial → ESP32 → ST7735 display
```

### Gestures

| Gesture | Action |
| :--- | :--- |
| ☝️ Index finger up, middle finger **down** | **Draw** — leaves a trail |
| 🖐️ Open palm (all fingers up) | **Erase** — clears the canvas |
| ✊ Fist | **Pause** — lifts the pen |
| `C` key | Clear canvas |
| `Q` / `ESC` | Quit |

### Usage
```bash
# Auto-detect ESP32 port
python3 air_draw.py

# Specify port manually
python3 air_draw.py --port /dev/cu.usbserial-0001

# Change draw color (RGB)
python3 air_draw.py --color 255 100 0    # orange

# Test without ESP32 connected (preview only)
python3 air_draw.py --no-send
```

### What you see
The script opens a split preview window:
- **Left panel**: Mirrored webcam feed with your drawing overlaid
- **Right panel**: Exact pixel preview of what's on the 128×160 display
- **HUD**: Shows current mode (DRAW / ERASE / IDLE) and connection status

### How the ESP32 switches modes
- **Idle/Video loop** runs by default after boot
- When `air_draw.py` starts sending frames, the ESP32 **automatically detects** the magic header and enters draw mode
- When you quit `air_draw.py`, the ESP32 **automatically returns** to the idle screen after 2 seconds of no data

No reflashing needed to switch between video and air-draw modes.

---

## 🔍 Serial Monitor

```bash
pio device monitor --baud 115200
```

---

## 📂 Project Structure

```
├── 1015.mp4            # Sample video
├── air_draw.py         # Air-drawing host script (OpenCV + MediaPipe)
├── flash_video.py      # Video flashing CLI
├── data/               # LittleFS source files (anim.gif, idle.bin)
├── platformio.ini      # Build config, pins, libraries
├── src/
│   └── main.cpp        # ESP32 firmware (video + air-draw serial receiver)
└── README.md
```

---

## 📄 License
MIT — free to use and modify.
