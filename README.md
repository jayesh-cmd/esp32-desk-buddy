# ESP32 Desk Buddy & Air-Draw 🎬✍️

An interactive desk companion powered by an **ESP32** and a **1.8" ST7735 SPI TFT display (160×128)** featuring two seamless operating modes:

1. **Air-Draw Mode (Wireless Stylus)** ✨ — Point your finger in front of your webcam and hold the **`Fn`** or **`Command (⌘)`** key to draw on the mini display in real-time over high-speed USB Serial.
2. **Desk Buddy Mode (Autonomous Animation)** 🐾 — Displays a full-screen idle image on your desk and periodically wakes up every 20 minutes to play an animated character video loop.

No re-flashing or button-pressing needed to switch modes! Launching `air_draw.py` immediately takes over the display, and closing it automatically returns the device to your desk buddy loop after 3 seconds.

---

## ✨ Key Features

- **Real-Time Air-Writing**: Powered by Google MediaPipe hand tracking + OpenCV with zero-lag hardware key detection (`Fn` or `⌘` to draw).
- **Lightweight Vector Protocol**: Tiny 10-byte draw packets (`CMD_LINE`, `CMD_ERASE`, `CMD_ROTATE`) streaming over standard 115200 baud serial — zero buffer overflow, rock-solid stability.
- **Horizontal Landscape Display**: Optimized for widescreen 160×128 orientation on your desk, with instant 180° rotation toggling (`R` key).
- **Non-Blocking Architecture**: Background daemon serial worker with thread-safe queues ensures the webcam feed runs at full 30+ FPS without stuttering.
- **Color Palette**: Switch between 6 vibrant drawing colors on the fly (`1` to `6`).
- **Autonomous Video Player**: Plays animated GIFs stored in LittleFS flash storage with flicker-free composite RAM framebuffering.
- **CLI Video Converter & Flasher** (`flash_video.py`): Convert any `.mp4` or `.gif`, crop/scale, generate LittleFS image, and flash to ESP32 in a single command.

---

## 🛠️ Hardware Requirements & Pinout

- **Microcontroller**: ESP32 Dev Board (ESP-WROOM-32)
- **Display**: 1.8" ST7735 SPI TFT (128×160 native, driven in 160×128 landscape)
- **Host**: Laptop / PC with webcam (macOS, Linux, or Windows)

| Display Pin | ESP32 GPIO | Description |
| :--- | :--- | :--- |
| **VCC** | `3.3V` / `5V` | Power (check display rating) |
| **GND** | `GND` | Ground |
| **CS** | `GPIO 5` | SPI Chip Select |
| **RST** | `GPIO 4` | Display Reset |
| **DC / A0** | `GPIO 2` | Data / Command Select |
| **SDA / MOSI** | `GPIO 23` | SPI MOSI Data |
| **SCK** | `GPIO 18` | SPI Clock |
| **LED** | `3.3V` | Backlight Anode |

---

## 📦 Installation & Setup

### 1. Install Firmware Toolchain
Install [PlatformIO](https://platformio.org/):
```bash
brew install platformio    # macOS (Homebrew)
# or: pip install platformio
```

### 2. Install Python Dependencies
```bash
pip install opencv-python mediapipe pyserial numpy
```

*(Optional for video flashing CLI)*:
```bash
brew install ffmpeg
pip install littlefs-python esptool
```

---

## 🚀 Quick Start

### 1. Flash Firmware to ESP32
Connect your ESP32 via USB and run:
```bash
pio run -t upload
```
The ESP32 will boot, mount LittleFS, and show the desk buddy idle screen.

### 2. Launch Air-Draw
Run the air-drawing script:
```bash
python3 air_draw.py
```
The script auto-detects your ESP32 serial port (`/dev/cu.usbserial-*` or `COM*`), clears the display, rotates to horizontal mode, and starts drawing!

```bash
# Optional flags:
python3 air_draw.py --cam 0              # Specify webcam index
python3 air_draw.py --port /dev/cu.usbserial-10   # Specify serial port
python3 air_draw.py --no-send            # Test camera & gestures without ESP32
```

---

## ✍️ Air-Draw Controls & Shortcuts

| Action | Control | Description |
| :--- | :--- | :--- |
| **Draw (Pen Down)** | **Hold `Fn`** or **`Command ⌘`** | Moves stylus and leaves a crisp vector line |
| **Hover (Pen Up)** | **Release button** | Reposition your finger across the canvas without drawing |
| **Clear Canvas** | **Key `C`** | Erases both the preview and the ESP32 screen |
| **Flip Screen 180°** | **Key `R`** | Switches between Right-Side Landscape (`3`) and Left-Side Landscape (`1`) |
| **Change Colors** | **Keys `1` – `6`** | `1` Cyan, `2` Green, `3` Yellow, `4` Orange, `5` Pink, `6` White |
| **Quit** | **Key `Q`** or **`ESC`** | Exits cleanly; ESP32 automatically returns to idle desk buddy mode |

---

## 🎬 Flashing Custom Desk Buddy Videos

To convert and flash a new video/animation onto the LittleFS partition:

```bash
python3 flash_video.py your_video.mp4 --rotate left --fit stretch
```

| Parameter | Default | Options / Description |
| :--- | :--- | :--- |
| `--rotate` | `none` | `none`, `left`, `right`, `180` (matches physical mounting) |
| `--fit` | `stretch` | `stretch`, `crop`, `pad` |
| `--fps` | `15` | Target animation frame rate |
| `--port` | *auto* | Serial port (auto-detected if omitted) |

---

## 📂 Project Structure

```
├── air_draw.py         # Air-drawing host app (OpenCV, MediaPipe, Quartz key capture)
├── flash_video.py      # Automated video converter & LittleFS flashing CLI
├── platformio.ini      # PlatformIO build configuration, SPI pins & libraries
├── data/               # LittleFS filesystem assets
│   ├── anim.gif        # Video animation played periodically
│   └── idle.bin        # Full-screen idle background image
├── src/
│   ├── main.cpp        # Firmware: dual-mode state machine & ST7735 driver
│   └── idle_bg.h       # Fallback idle background array
├── .gitignore          # Ignores build artifacts and raw media
└── README.md           # Documentation
```

---

## 📄 License

MIT License — free to use, modify, and build upon.
