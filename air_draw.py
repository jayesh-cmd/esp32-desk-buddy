#!/usr/bin/env python3
"""
air_draw.py — Air-Writing for ESP32 Desk Buddy (Horizontal / Hold-to-Draw)
=========================================================================
Track your finger in the air like a stylus. Hold the Fn key or
Command key (⌘) to draw on the ESP32 TFT display. Release to lift the pen.

Protocol (rock-solid @ 115200 baud):
  Each packet: MAGIC(2) + CMD(1) + args
    CMD_LINE    0x01  x0 y0 x1 y1 r g b  (10 bytes total)
    CMD_ERASE   0x02                      (3 bytes total)
    CMD_SYNC    0x03                      (3 bytes total)
    CMD_ROTATE  0x04  rot                 (4 bytes total: 1=landscape, 3=inv landscape)

Controls:
  🖱️ / ⌨️  Hold Fn or Command (⌘)       : DRAW on screen
  🖐️      Release button              : HOVER / LIFT PEN (aim without drawing)
  Key C                               : Clear Canvas
  Key R                               : Switch Landscape Side (Right 3 <-> Left 1)
  Keys 1-6                            : Switch Colors (Cyan, Green, Yellow, Orange, Pink, White)
  Key Q / ESC                         : Quit
"""

import cv2
import mediapipe as mp
import numpy as np
import serial
import serial.tools.list_ports
import argparse
import sys
import time
import threading
import queue

# ── macOS Hardware Key Detection (Zero-lag, no extra permissions needed) ───────
try:
    import Quartz
    def is_draw_button_held():
        flags = Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateCombinedSessionState)
        # Check Fn key (Function / Globe key on Mac: modifier mask & keycode 63)
        if flags & Quartz.kCGEventFlagMaskSecondaryFn:
            return True
        if Quartz.CGEventSourceKeyState(Quartz.kCGEventSourceStateCombinedSessionState, 63):
            return True
        # Check macOS Command modifier key (⌘)
        if flags & Quartz.kCGEventFlagMaskCommand:
            return True
        if Quartz.CGEventSourceKeyState(Quartz.kCGEventSourceStateCombinedSessionState, 55) or \
           Quartz.CGEventSourceKeyState(Quartz.kCGEventSourceStateCombinedSessionState, 54):
            return True
        return False
except Exception:
    is_draw_button_held = lambda: False

# ── Display dimensions (Horizontal / Landscape: 160 x 128) ────────────────────
DISP_W, DISP_H = 160, 128

# ── Draw command protocol ─────────────────────────────────────────────────────
MAGIC      = b'\xAB\xCD'
CMD_LINE   = 0x01   # x0 y0 x1 y1 r g b
CMD_ERASE  = 0x02
CMD_SYNC   = 0x03
CMD_ROTATE = 0x04   # rot (0..3)

# ── Hand landmark indices ─────────────────────────────────────────────────────
TIPS = [4, 8, 12, 16, 20]   # Thumb, Index, Middle, Ring, Pinky
PIPS = [3, 6, 10, 14, 18]

# Brush settings (Thin, crisp line)
BRUSH_RADIUS = 1

# Color presets (BGR for OpenCV, RGB sent to ESP32)
COLOR_PALETTE = {
    ord('1'): ("Cyan",    (255, 255, 0),   (0, 255, 255)),
    ord('2'): ("Green",   (0, 255, 0),     (0, 255, 0)),
    ord('3'): ("Yellow",  (0, 255, 255),   (255, 255, 0)),
    ord('4'): ("Orange",  (0, 140, 255),   (255, 140, 0)),
    ord('5'): ("Pink",    (255, 0, 255),   (255, 0, 255)),
    ord('6'): ("White",   (255, 255, 255), (255, 255, 255)),
}


def find_esp32_port():
    for p in serial.tools.list_ports.comports():
        desc = (p.description or '').lower()
        hwid = (p.hwid or '').lower()
        if any(k in desc or k in hwid for k in ('cp210', 'ch340', 'ftdi', 'uart', 'usb serial', 'esp')):
            return p.device
    for p in serial.tools.list_ports.comports():
        if 'usb' in (p.hwid or '').lower():
            return p.device
    return None


def pkt_line(x0, y0, x1, y1, rgb):
    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
    x0 = max(0, min(DISP_W - 1, int(x0)))
    y0 = max(0, min(DISP_H - 1, int(y0)))
    x1 = max(0, min(DISP_W - 1, int(x1)))
    y1 = max(0, min(DISP_H - 1, int(y1)))
    return MAGIC + bytes([CMD_LINE, x0, y0, x1, y1, r, g, b])


def pkt_erase():
    return MAGIC + bytes([CMD_ERASE])


def pkt_sync():
    return MAGIC + bytes([CMD_SYNC])


def pkt_rotate(rot: int):
    return MAGIC + bytes([CMD_ROTATE, int(rot) % 4])


# ── Thread-safe Non-blocking Serial Worker ─────────────────────────────────────
_cmd_queue = queue.Queue(maxsize=150)


def _serial_worker(ser):
    while True:
        try:
            pkt = _cmd_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        if pkt is None:
            break
        try:
            ser.write(pkt)
        except Exception as e:
            print(f"[Serial] Error writing to device: {e}")
            break


def send_cmd(pkt: bytes):
    try:
        _cmd_queue.put_nowait(pkt)
    except queue.Full:
        pass


def fingers_up(lm, hand_label):
    up = []
    if hand_label == 'Right':
        up.append(lm[TIPS[0]].x < lm[PIPS[0]].x)
    else:
        up.append(lm[TIPS[0]].x > lm[PIPS[0]].x)
    for i in range(1, 5):
        up.append(lm[TIPS[i]].y < lm[PIPS[i]].y)
    return up


def draw_styled_rect(img, pt1, pt2, color, thickness=1, corner_len=18):
    """Draws an elegant sci-fi bounding box with corner accents."""
    x1, y1 = pt1
    x2, y2 = pt2
    cv2.rectangle(img, (x1, y1), (x2, y2), (40, 40, 40), 1)
    c_thick = thickness + 1
    # Top-left
    cv2.line(img, (x1, y1), (x1 + corner_len, y1), color, c_thick)
    cv2.line(img, (x1, y1), (x1, y1 + corner_len), color, c_thick)
    # Top-right
    cv2.line(img, (x2, y1), (x2 - corner_len, y1), color, c_thick)
    cv2.line(img, (x2, y1), (x2, y1 + corner_len), color, c_thick)
    # Bottom-left
    cv2.line(img, (x1, y2), (x1 + corner_len, y2), color, c_thick)
    cv2.line(img, (x1, y2), (x1, y2 - corner_len), color, c_thick)
    # Bottom-right
    cv2.line(img, (x2, y2), (x2 - corner_len, y2), color, c_thick)
    cv2.line(img, (x2, y2), (x2, y2 - corner_len), color, c_thick)


# Global mouse state for optional mouse-click drawing
_mouse_pressed = False

def _mouse_cb(event, x, y, flags, param):
    global _mouse_pressed
    if event == cv2.EVENT_LBUTTONDOWN:
        _mouse_pressed = True
    elif event == cv2.EVENT_LBUTTONUP:
        _mouse_pressed = False


def main():
    parser = argparse.ArgumentParser(description='Air-Draw with Command-Hold to ESP32 Display.')
    parser.add_argument('--port',    default=None, help='Serial port (auto-detect if omitted)')
    parser.add_argument('--baud',    type=int, default=115200, help='Baud rate (default 115200)')
    parser.add_argument('--cam',     type=int, default=1,      help='Webcam index (default 1)')
    parser.add_argument('--rot',     type=int, default=3,      help='Display rotation (3=right-side landscape, 1=left-side landscape, default 3)')
    parser.add_argument('--no-send', action='store_true',      help='Preview only — no ESP32 connection')
    args = parser.parse_args()

    # Active color state (default: Cyan)
    color_name, current_bgr, current_rgb = COLOR_PALETTE[ord('1')]
    current_rot = args.rot

    # ── Serial Connection ─────────────────────────────────────────────────────
    ser = None
    if not args.no_send:
        port = args.port or find_esp32_port()
        if not port:
            print('[Error] No ESP32 serial port detected! Please plug in ESP32 or use --port.')
            sys.exit(1)
        try:
            ser = serial.Serial(port, args.baud, timeout=0.2)
            time.sleep(1.2)   # Wait for ESP32 reboot after DTR toggle
            print(f'✅ Connected to ESP32 on {port} @ {args.baud} baud')
            worker_t = threading.Thread(target=_serial_worker, args=(ser,), daemon=True)
            worker_t.start()
            # Send initial rotation & clear screen to activate right-side landscape drawing
            send_cmd(pkt_rotate(current_rot) + pkt_erase() + pkt_sync())
        except serial.SerialException as e:
            print(f'[Error] Could not open {port}: {e}')
            sys.exit(1)
    else:
        print('ℹ️  Running in preview-only mode (--no-send).')

    # ── MediaPipe Hands ───────────────────────────────────────────────────────
    mp_hands   = mp.solutions.hands
    mp_drawing = mp.solutions.drawing_utils
    hands      = mp_hands.Hands(max_num_hands=1,
                                min_detection_confidence=0.7,
                                min_tracking_confidence=0.6)

    # ── Webcam ────────────────────────────────────────────────────────────────
    cap = cv2.VideoCapture(args.cam)
    if not cap.isOpened() and args.cam != 0:
        print(f'Webcam {args.cam} not found, trying webcam 0...')
        cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print(f'[Error] Cannot open webcam index {args.cam}')
        sys.exit(1)

    cam_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    cam_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f'Webcam active: {cam_w}x{cam_h}')

    # ── Canvas & Mapping Setup (160x128 Landscape) ────────────────────────────
    canvas  = np.zeros((DISP_H, DISP_W, 3), dtype=np.uint8)
    prev_pt = None
    last_keepalive = time.time()

    # Target box on camera feed corresponding to 160:128 (1.25) horizontal ratio
    box_h = int(cam_h * 0.72)
    box_w = int(box_h * (DISP_W / DISP_H))   # 1.25 aspect ratio
    box_x0 = (cam_w - box_w) // 2
    box_y0 = (cam_h - box_h) // 2
    box_x1 = box_x0 + box_w
    box_y1 = box_y0 + box_h

    SCALE = 3   # Preview scale for display canvas (160*3 = 480, 128*3 = 384)

    window_name = "Air-Draw -> ESP32 Desk Buddy (Hold ⌘ to Draw)"
    cv2.namedWindow(window_name)
    cv2.setMouseCallback(window_name, _mouse_cb)

    print("\n-------------------------------------------------------------")
    print(" 🖥️  HORIZONTAL DISPLAY MODE (Right-Side Landscape 160x128)")
    print(" ⌨️  HOLD [Fn] or [Command ⌘]        : DRAW on screen")
    print(" 🖐️  RELEASE button                 : HOVER (move pen without drawing)")
    print(" Key C                             : Clear Canvas")
    print(" Key R                             : Switch Landscape Side (Right 3 <-> Left 1)")
    print(" Keys 1-6                          : Switch Colors")
    print(" Key Q                             : Quit")
    print("-------------------------------------------------------------\n")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Flip horizontally for natural mirror feel
        frame = cv2.flip(frame, 1)
        rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res   = hands.process(rgb)

        mode = 'idle'
        cam_tip_pt = None
        disp_pt = None

        # Check if button is physically held down right now
        button_held = is_draw_button_held() or _mouse_pressed

        if res.multi_hand_landmarks:
            hl  = res.multi_hand_landmarks[0]
            lbl = res.multi_handedness[0].classification[0].label
            lm  = hl.landmark

            # Fingertip coordinates (Stylus point)
            tip = lm[8]
            cx, cy = int(tip.x * cam_w), int(tip.y * cam_h)
            cam_tip_pt = (cx, cy)

            # Map coordinates relative to the horizontal drawing box
            rel_x = (cx - box_x0) / float(box_w)
            rel_y = (cy - box_y0) / float(box_h)
            dx = int(np.clip(rel_x * DISP_W, 0, DISP_W - 1))
            dy = int(np.clip(rel_y * DISP_H, 0, DISP_H - 1))
            disp_pt = (dx, dy)

            # Draw slim hand skeleton
            mp_drawing.draw_landmarks(
                frame, hl, mp_hands.HAND_CONNECTIONS,
                mp_drawing.DrawingSpec(color=(60, 60, 220), thickness=2, circle_radius=2),
                mp_drawing.DrawingSpec(color=(180, 180, 180), thickness=1)
            )

            if button_held:
                # User is holding Fn or Command (⌘) -> DRAW
                mode = 'draw'
                x0, y0 = prev_pt if prev_pt is not None else (dx, dy)
                # Thin crisp line on canvas (thickness = 1)
                cv2.line(canvas, (x0, y0), (dx, dy), current_bgr, 1)
                cv2.circle(canvas, (dx, dy), 0, current_bgr, -1)
                # Send thin line command to ESP32
                if ser:
                    send_cmd(pkt_line(x0, y0, dx, dy, current_rgb) + pkt_sync())
                    last_keepalive = time.time()
                prev_pt = (dx, dy)

            else:
                # Button not held -> HOVER (Aiming pen / moving cursor without drawing)
                mode = 'hover'
                prev_pt = None
        else:
            prev_pt = None

        # Periodic keepalive (every 1.0s) so ESP32 stays in drawing mode
        if ser and (time.time() - last_keepalive > 1.0):
            send_cmd(pkt_sync())
            last_keepalive = time.time()

        # ── Visual Overlay ────────────────────────────────────────────────────
        # Draw active horizontal canvas region on camera feed
        box_color = (0, 255, 120) if mode == 'draw' else (120, 120, 120)
        draw_styled_rect(frame, (box_x0, box_y0), (box_x1, box_y1), box_color, 2)
        cv2.putText(frame, f"ESP32 Canvas (160x128) [Rot {current_rot}] - Press R to flip",
                    (box_x0 + 8, box_y0 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 160, 160), 1)

        # Stylus cursor dot & crosshair
        if cam_tip_pt:
            if mode == 'draw':
                # Active drawing indicator
                cv2.circle(frame, cam_tip_pt, 5, current_bgr, -1)
                cv2.circle(frame, cam_tip_pt, 9, (255, 255, 255), 1)
            else:
                # Hover crosshair
                cv2.drawMarker(frame, cam_tip_pt, (200, 200, 200), cv2.MARKER_CROSS, 14, 1)

        # HUD Overlay on Camera
        status_colors = {
            'draw':  (0, 255, 100),
            'hover': (255, 200, 0),
            'idle':  (150, 150, 150)
        }
        status_labels = {
            'draw':  "● DRAWING (Button Held)",
            'hover': "○ HOVER (Hold Fn or ⌘ to Draw)",
            'idle':  "IDLE (Show Hand)"
        }
        cv2.rectangle(frame, (10, 10), (320, 52), (20, 20, 20), -1)
        cv2.putText(frame, status_labels[mode], (20, 38),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.58, status_colors[mode], 2)

        # Current Color badge
        cv2.rectangle(frame, (cam_w - 180, 10), (cam_w - 10, 52), (20, 20, 20), -1)
        cv2.circle(frame, (cam_w - 155, 31), 8, current_bgr, -1)
        cv2.putText(frame, color_name, (cam_w - 135, 37),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 1)

        # ESP32 Status indicator
        esp_str = f"ESP32: OK (Rot {current_rot})" if ser else "ESP32: OFF"
        esp_col = (0, 255, 100) if ser else (100, 100, 100)
        cv2.putText(frame, esp_str, (20, cam_h - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, esp_col, 1)

        # ── Assemble Side-by-Side Display ─────────────────────────────────────
        h_target = int(DISP_H * SCALE)   # 128 * 3 = 384
        w_cam_target = int(cam_w * (h_target / cam_h))
        frame_resized = cv2.resize(frame, (w_cam_target, h_target))

        # Right side: Exact ESP32 display representation (160x128 scaled to 480x384)
        disp_preview = cv2.resize(canvas, (DISP_W * SCALE, DISP_H * SCALE),
                                  interpolation=cv2.INTER_NEAREST)

        # Border between views
        cv2.rectangle(disp_preview, (0, 0), (disp_preview.shape[1] - 1, disp_preview.shape[0] - 1),
                      (60, 60, 60), 1)
        cv2.putText(disp_preview, "ESP32 TFT Landscape (160x128)", (12, disp_preview.shape[0] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (130, 130, 130), 1)

        combined = np.hstack([frame_resized, disp_preview])
        cv2.imshow(window_name, combined)

        # Keyboard controls
        key = cv2.waitKey(1) & 0xFF
        if key in (ord('q'), ord('Q'), 27):
            break
        elif key in (ord('c'), ord('C')):
            canvas[:] = 0
            prev_pt = None
            if ser:
                send_cmd(pkt_erase() + pkt_sync())
        elif key in (ord('r'), ord('R')):
            current_rot = 1 if current_rot == 3 else 3
            canvas[:] = 0
            prev_pt = None
            if ser:
                send_cmd(pkt_rotate(current_rot) + pkt_erase() + pkt_sync())
            print(f"Switched display rotation to: {current_rot}")
        elif key in COLOR_PALETTE:
            color_name, current_bgr, current_rgb = COLOR_PALETTE[key]

    # ── Cleanup ───────────────────────────────────────────────────────────────
    cap.release()
    cv2.destroyAllWindows()
    hands.close()
    if ser:
        try:
            _cmd_queue.put_nowait(None)
            time.sleep(0.15)
            ser.close()
        except Exception:
            pass
    print("Done. Goodbye!")


if __name__ == '__main__':
    main()
