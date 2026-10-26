#!/usr/bin/env python3
"""
air_draw.py — Air-Writing for ESP32 Desk Buddy
================================================
Draw on your laptop display using your index finger in front of the webcam.
Your drawing streams to the ESP32 ST7735 display in real-time over USB serial.

Gestures:
  ✌  Index finger up, middle finger DOWN  →  Draw mode (leaves a trail)
  🖐  All 5 fingers up (open palm)         →  Erase / Clear canvas
  ✊  Fist (all fingers down)              →  Pause (lift pen)

Usage:
  pip install opencv-python mediapipe pyserial numpy
  python3 air_draw.py                         # auto-detect port
  python3 air_draw.py --port /dev/tty.usbserial-0001
  python3 air_draw.py --port /dev/tty.usbserial-0001 --baud 921600
  python3 air_draw.py --color 0 255 0         # draw in green
"""

import cv2
import mediapipe as mp
import numpy as np
import serial
import serial.tools.list_ports
import struct
import argparse
import sys
import time

# ── Display dimensions (must match ESP32 firmware) ───────────────────────────
DISP_W, DISP_H = 128, 160

# ── Serial protocol magic ─────────────────────────────────────────────────────
# Header: 0xDE 0xAD + 2-byte little-endian payload size
MAGIC       = b'\xDE\xAD'
PAYLOAD_LEN = DISP_W * DISP_H * 2          # 40 960 bytes of RGB565
HEADER      = MAGIC + struct.pack('<H', PAYLOAD_LEN)

# ── Finger tip / pip landmark indices (MediaPipe Hand) ───────────────────────
TIPS = [4, 8, 12, 16, 20]   # thumb, index, middle, ring, pinky
PIPS = [3, 6, 10, 14, 18]   # knuckles just below each tip

DRAW_COLOR_DEFAULT = (0, 255, 255)   # Cyan in BGR
BRUSH_RADIUS       = 3               # radius in display pixels
BRUSH_RADIUS_CAM   = 8               # radius for preview circle on camera


# ─────────────────────────────────────────────────────────────────────────────
def find_esp32_port():
    """Return the first serial port that looks like an ESP32."""
    candidates = list(serial.tools.list_ports.comports())
    for p in candidates:
        desc = (p.description or '').lower()
        hwid = (p.hwid or '').lower()
        if any(k in desc or k in hwid for k in ('cp210', 'ch340', 'ftdi', 'uart', 'usb serial', 'esp')):
            return p.device
    for p in candidates:
        if 'usb' in (p.hwid or '').lower():
            return p.device
    return None


def canvas_to_rgb565_bytes(canvas_bgr):
    """Convert a BGR uint8 canvas (H x W x 3) to raw RGB565 bytes (big-endian, row-major)."""
    r = canvas_bgr[:, :, 2].astype(np.uint16)
    g = canvas_bgr[:, :, 1].astype(np.uint16)
    b = canvas_bgr[:, :, 0].astype(np.uint16)
    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    # byteswap to big-endian — TFT_eSPI pushColors expects big-endian
    rgb565_be = rgb565.byteswap()
    return rgb565_be.astype(np.uint16).tobytes()


def fingers_up(hand_landmarks, handedness_label):
    """Return list of 5 booleans: [thumb, index, middle, ring, pinky] up."""
    lm = hand_landmarks.landmark
    up = []
    # Thumb: compare x position (hand-aware)
    if handedness_label == 'Right':
        up.append(lm[TIPS[0]].x < lm[PIPS[0]].x)
    else:
        up.append(lm[TIPS[0]].x > lm[PIPS[0]].x)
    # Other fingers: tip above pip in y means up
    for i in range(1, 5):
        up.append(lm[TIPS[i]].y < lm[PIPS[i]].y)
    return up


def map_to_display(norm_x, norm_y):
    """Map MediaPipe normalized coords (0..1) to display pixel coords."""
    dx = int(norm_x * DISP_W)
    dy = int(norm_y * DISP_H)
    dx = max(0, min(DISP_W - 1, dx))
    dy = max(0, min(DISP_H - 1, dy))
    return dx, dy


# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Air-draw on ESP32 display via webcam.')
    parser.add_argument('--port',    default=None,  help='Serial port (auto-detect if omitted)')
    parser.add_argument('--baud',    type=int, default=115200, help='Baud rate (default 115200)')
    parser.add_argument('--cam',     type=int, default=0,      help='Webcam index (default 0)')
    parser.add_argument('--color',   type=int, nargs=3,
                        default=[0, 255, 255],   # RGB order for CLI
                        metavar=('R', 'G', 'B'),
                        help='Draw color in RGB (default: 0 255 255 = cyan)')
    parser.add_argument('--no-send', action='store_true',
                        help='Preview-only mode — no serial output')
    args = parser.parse_args()

    # CLI gives RGB, OpenCV needs BGR
    draw_color_bgr = (args.color[2], args.color[1], args.color[0])

    # ── Serial setup ──────────────────────────────────────────────────────────
    ser = None
    if not args.no_send:
        port = args.port or find_esp32_port()
        if port is None:
            print('No ESP32 serial port found. Use --port or --no-send for preview only.')
            sys.exit(1)
        try:
            ser = serial.Serial(port, args.baud, timeout=0.1)
            time.sleep(1.5)
            print(f'Connected to ESP32 on {port} @ {args.baud} baud')
        except serial.SerialException as e:
            print(f'Could not open {port}: {e}')
            sys.exit(1)
    else:
        print('Running in preview-only mode (--no-send). No serial output.')

    # ── MediaPipe Hands ───────────────────────────────────────────────────────
    mp_hands    = mp.solutions.hands
    mp_drawing  = mp.solutions.drawing_utils
    hands_model = mp_hands.Hands(
        static_image_mode=False,
        max_num_hands=1,
        min_detection_confidence=0.7,
        min_tracking_confidence=0.6,
    )

    # ── Webcam ────────────────────────────────────────────────────────────────
    cap = cv2.VideoCapture(args.cam)
    if not cap.isOpened():
        print(f'Cannot open webcam {args.cam}')
        sys.exit(1)
    cam_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    cam_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f'Webcam: {cam_w}x{cam_h}')

    # ── Drawing state ─────────────────────────────────────────────────────────
    canvas       = np.zeros((DISP_H, DISP_W, 3), dtype=np.uint8)
    prev_pt      = None     # last draw point for continuous lines
    dirty        = True     # send on first frame
    last_send_t  = 0.0
    SEND_INTERVAL = 1.0 / 30   # max 30 fps to ESP32

    print('\nControls:')
    print('  Index up, middle DOWN  -> Draw')
    print('  Open palm (all up)     -> Erase / Clear')
    print('  Fist                   -> Pause / Lift pen')
    print('  C key                  -> Clear canvas')
    print('  Q / ESC                -> Quit\n')

    SCALE = 4   # preview scale factor

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Mirror frame so movement feels natural
        frame = cv2.flip(frame, 1)

        # ── Hand detection ────────────────────────────────────────────────────
        rgb     = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = hands_model.process(rgb)

        mode           = 'idle'
        finger_tip_cam = None

        if results.multi_hand_landmarks:
            hand_lm    = results.multi_hand_landmarks[0]
            handedness = results.multi_handedness[0].classification[0].label

            up         = fingers_up(hand_lm, handedness)
            index_up   = up[1]
            middle_up  = up[2]
            all_up     = all(up[1:])    # four fingers (ignore thumb)

            if all_up:
                mode = 'erase'
            elif index_up and not middle_up:
                mode = 'draw'

            # Skeleton overlay
            mp_drawing.draw_landmarks(
                frame, hand_lm, mp_hands.HAND_CONNECTIONS,
                mp_drawing.DrawingSpec(color=(80, 80, 255), thickness=2, circle_radius=3),
                mp_drawing.DrawingSpec(color=(200, 200, 200), thickness=1),
            )

            # Index finger tip coords
            tip = hand_lm.landmark[8]
            finger_tip_cam = (int(tip.x * cam_w), int(tip.y * cam_h))
            dx, dy = map_to_display(tip.x, tip.y)

            if mode == 'erase':
                canvas[:] = 0
                prev_pt   = None
                dirty     = True
            elif mode == 'draw':
                if prev_pt is not None:
                    cv2.line(canvas, prev_pt, (dx, dy), draw_color_bgr, BRUSH_RADIUS * 2)
                cv2.circle(canvas, (dx, dy), BRUSH_RADIUS, draw_color_bgr, -1)
                prev_pt = (dx, dy)
                dirty   = True
            else:
                prev_pt = None
        else:
            prev_pt = None

        # ── Send to ESP32 ─────────────────────────────────────────────────────
        now = time.time()
        if dirty and ser and (now - last_send_t) >= SEND_INTERVAL:
            try:
                ser.write(HEADER + canvas_to_rgb565_bytes(canvas))
                ser.flush()
            except serial.SerialException as e:
                print(f'Serial write error: {e}')
            last_send_t = now
            dirty       = False

        # ── Build preview ─────────────────────────────────────────────────────
        canvas_big = cv2.resize(canvas, (DISP_W * SCALE, DISP_H * SCALE),
                                interpolation=cv2.INTER_NEAREST)

        # Scale camera to match canvas preview height
        ph = DISP_H * SCALE
        pw = int(cam_w * ph / cam_h)
        cam_s = cv2.resize(frame, (pw, ph))
        # Crop to same width as canvas preview
        cam_s = cam_s[:, :DISP_W * SCALE] if pw >= DISP_W * SCALE else cv2.copyMakeBorder(
            cam_s, 0, 0, 0, DISP_W * SCALE - pw, cv2.BORDER_CONSTANT)

        # Blend drawing over camera
        mask = cv2.threshold(cv2.cvtColor(canvas_big, cv2.COLOR_BGR2GRAY),
                             1, 255, cv2.THRESH_BINARY)[1]
        blended = np.where(cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR) > 0, canvas_big, cam_s)

        # Side-by-side: blended (left) | clean canvas (right)
        full = np.hstack([blended, canvas_big])

        # HUD
        MODE_COLORS = {'draw': (0, 220, 80), 'erase': (30, 30, 220), 'idle': (140, 140, 140)}
        MODE_LABELS = {'draw': 'DRAW', 'erase': 'ERASE', 'idle': 'IDLE'}
        cv2.rectangle(full, (0, 0), (180, 34), (15, 15, 15), -1)
        cv2.putText(full, MODE_LABELS.get(mode, '?'), (8, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, MODE_COLORS.get(mode, (255, 255, 255)), 2)
        if ser:
            cv2.putText(full, 'ESP32 OK', (full.shape[1] - 120, 24),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (60, 220, 60), 1)

        # Fingertip dot on camera side
        if finger_tip_cam:
            tc = MODE_COLORS.get(mode, (200, 200, 200))
            cv2.circle(full, finger_tip_cam, BRUSH_RADIUS_CAM, tc, 2)
            cv2.circle(full, finger_tip_cam, 3, (255, 255, 255), -1)

        # Panel labels
        cv2.putText(full, 'Camera + Drawing', (8, ph - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (90, 90, 90), 1)
        cv2.putText(full, 'Display Preview (128x160)', (DISP_W * SCALE + 8, ph - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (90, 90, 90), 1)
        cv2.line(full, (DISP_W * SCALE, 0), (DISP_W * SCALE, ph), (55, 55, 55), 2)

        cv2.imshow('Air Draw -> ESP32  |  Q to quit', full)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord('q'), ord('Q'), 27):
            break
        elif key in (ord('c'), ord('C')):
            canvas[:] = 0
            dirty = True

    # ── Cleanup ───────────────────────────────────────────────────────────────
    cap.release()
    cv2.destroyAllWindows()
    hands_model.close()
    if ser and ser.is_open:
        # Clear display on exit
        blank = np.zeros((DISP_H, DISP_W, 3), dtype=np.uint8)
        ser.write(HEADER + canvas_to_rgb565_bytes(blank))
        ser.flush()
        time.sleep(0.2)
        ser.close()
    print('Bye!')


if __name__ == '__main__':
    main()
