#!/usr/bin/env python3
"""
flash_video.py: Convert any video or GIF to 128x160 and flash it directly to your ESP32.

Usage:
    python3 flash_video.py 1015.mp4 --rotate left --fit stretch
"""

import sys
import os
import glob
import shutil
import subprocess
import argparse
from pathlib import Path

PARTITION_OFFSET = "0x290000"
PARTITION_SIZE = 0x160000  # 1,441,792 bytes (~1.4MB)

def get_esptool_cmd():
    """Finds the available esptool binary or python module."""
    pio_esptool = os.path.expanduser("~/.platformio/packages/tool-esptoolpy/esptool.py")
    if os.path.exists(pio_esptool):
        return [sys.executable, pio_esptool]
    if shutil.which("esptool.py"):
        return ["esptool.py"]
    if shutil.which("esptool"):
        return ["esptool"]
    return [sys.executable, "-m", "esptool"]

def auto_detect_port():
    """Finds connected ESP32 serial port on macOS and Linux."""
    candidates = (
        glob.glob("/dev/cu.usbserial*") +
        glob.glob("/dev/cu.SLAB_USBtoUART*") +
        glob.glob("/dev/cu.wchusbserial*") +
        glob.glob("/dev/cu.usbmodem*") +
        glob.glob("/dev/ttyUSB*") +
        glob.glob("/dev/ttyACM*")
    )
    if candidates:
        return sorted(candidates)[0]
    return None

def convert_video_to_gif(input_path, output_gif, fps=15, rotate="none", fit="stretch"):
    print(f"🎬 Converting '{input_path}' to 128x160 GIF ({fps} fps, rotate={rotate}, fit={fit})...")
    
    rotate_filter = ""
    if rotate == "left":
        rotate_filter = "transpose=2,"
    elif rotate == "right":
        rotate_filter = "transpose=1,"
    elif rotate == "180":
        rotate_filter = "transpose=2,transpose=2,"

    if fit == "stretch":
        # Scale directly to exact display resolution (no black bars!)
        scale_filter = "scale=128:160"
    elif fit == "crop":
        # Fill screen, crop edges
        scale_filter = "scale=128:160:force_original_aspect_ratio=increase,crop=128:160"
    else:  # pad
        # Letterbox with black bars
        scale_filter = "scale=128:160:force_original_aspect_ratio=decrease,pad=128:160:(ow-iw)/2:(oh-ih)/2:black"

    vf = (
        f"{rotate_filter}"
        f"fps={fps},"
        f"{scale_filter},"
        f"split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vf", vf,
        str(output_gif)
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        print("❌ FFmpeg conversion failed:")
        print(res.stderr.decode()[-500:])
        sys.exit(1)

def build_littlefs_image(gif_path, out_bin):
    import littlefs
    print(f"📦 Packaging GIF into LittleFS filesystem...")
    gif_size = os.path.getsize(gif_path)
    if gif_size > PARTITION_SIZE - 65536:
        print(f"⚠️  WARNING: GIF size ({gif_size} bytes) exceeds LittleFS partition ({PARTITION_SIZE} bytes)!")
        print("💡 Tip: Try lowering the FPS (--fps 12) or trimming the video length.")
        sys.exit(1)

    block_size = 4096
    block_count = PARTITION_SIZE // block_size

    fs = littlefs.LittleFS(
        block_size=block_size,
        block_count=block_count,
        read_size=256,
        prog_size=256,
        mount=False
    )
    fs.format()
    fs.mount()

    with open(gif_path, "rb") as f:
        data = f.read()

    with fs.open("/anim.gif", "wb") as f:
        f.write(data)

    idle_bin = Path(gif_path).parent / "idle.bin"
    if idle_bin.exists():
        with open(idle_bin, "rb") as f:
            with fs.open("/idle.bin", "wb") as f_out:
                f_out.write(f.read())
        print(f"🖼️  Added idle.bin ({idle_bin.stat().st_size} bytes) to LittleFS")

    with open(out_bin, "wb") as f:
        f.write(fs.context.buffer)

    print(f"✅ LittleFS image created: {out_bin} ({os.path.getsize(out_bin)} bytes)")

def flash_to_esp32(bin_path, port):
    print(f"⚡ Flashing to ESP32 at {port} (offset {PARTITION_OFFSET})...")
    esptool_base = get_esptool_cmd()
    cmd = esptool_base + [
        "--chip", "esp32",
        "--port", port,
        "--baud", "460800",
        "write_flash",
        PARTITION_OFFSET, str(bin_path)
    ]
    res = subprocess.run(cmd)
    if res.returncode == 0:
        print("\n🎉 Success! The ESP32 is now playing your video/GIF!")
    else:
        print("❌ Flashing failed. Check your USB connection and port.")

def main():
    parser = argparse.ArgumentParser(description="Convert and flash video/GIF to ESP32 screen")
    parser.add_argument("input", help="Path to video (.mp4, .mov, etc.) or GIF file")
    parser.add_argument("--fps", type=int, default=15, help="Playback frame rate (default: 15)")
    parser.add_argument("--rotate", choices=["none", "left", "right", "180"], default="none",
                        help="Rotation: left (90° CCW), right (90° CW), 180, none")
    parser.add_argument("--fit", choices=["stretch", "crop", "pad"], default="stretch",
                        help="Fit mode: stretch (full screen 128x160), crop, pad (with black bars)")
    parser.add_argument("--port", default=None, help="Serial port (auto-detected if omitted)")
    args = parser.parse_args()

    input_file = Path(args.input).resolve()
    if not input_file.exists():
        print(f"❌ File not found: {input_file}")
        sys.exit(1)

    port = args.port or auto_detect_port()
    if not port:
        print("❌ No ESP32 serial port detected! Please plug in your ESP32 or specify --port.")
        sys.exit(1)

    script_dir = Path(__file__).parent.resolve()
    data_dir = script_dir / "data"
    data_dir.mkdir(exist_ok=True)
    target_gif = data_dir / "anim.gif"
    target_bin = data_dir / "littlefs.bin"

    if input_file.suffix.lower() == ".gif" and args.rotate == "none":
        print(f"📄 Using existing GIF: {input_file}")
        shutil.copyfile(input_file, target_gif)
    else:
        convert_video_to_gif(input_file, target_gif, fps=args.fps, rotate=args.rotate, fit=args.fit)

    build_littlefs_image(target_gif, target_bin)
    flash_to_esp32(target_bin, port)

if __name__ == "__main__":
    main()
