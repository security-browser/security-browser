"""
Remove Gemini's VISIBLE corner sparkle (✦) from generated images/videos.

Strips only the cosmetic visible logo baked into the pixels. It deliberately
does NOT touch SynthID — the imperceptible provenance watermark is spread across
the whole frame and survives this small local edit, so AI-provenance is kept.

Images use OpenCV inpainting (reconstructs structure cleanly); video uses
ffmpeg's `delogo` filter (motion hides the small interpolated box). Positions
are measured from real Gemini outputs and kept here as constants so they can be
adjusted if Gemini moves the logo (like the DOM selectors).

Toggle with env GEMINI_STRIP_WATERMARK (default on).
"""

import json
import os
import subprocess

import cv2
import numpy as np

STRIP = os.environ.get("GEMINI_STRIP_WATERMARK", "1").lower() not in ("0", "false", "no", "")

# Sparkle position as fractions of (W, H). Measured 2026-06 on 1024×559 images
# and 1280×720 Veo videos. Image radius is kept tight to the sparkle core so the
# inpaint patch stays small (invisible on smooth areas, a faint soft smudge on
# busy texture — content-agnostic, no hard seams).
IMAGE_WM = (0.955, 0.927, 0.020)              # cx_rel, cy_rel, radius_rel(·W)
VIDEO_WM = (0.922, 0.866, 0.055, 0.100)       # cx_rel, cy_rel, w_rel, h_rel


def _probe_wh(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", path],
        timeout=30)
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"])


def _delogo_box(W, H, wm):
    cxr, cyr, wr, hr = wm
    w = max(16, int(wr * W))
    h = max(16, int(hr * H))
    x = min(W - w - 1, max(1, int(cxr * W - w / 2)))
    y = min(H - h - 1, max(1, int(cyr * H - h / 2)))
    return x, y, w, h


def strip_image_watermark(img_bytes: bytes, ext: str = ".png") -> bytes:
    """Inpaint the sparkle out of an image byte string via OpenCV (Telea).
    Returns PNG bytes; on any failure returns the input unchanged."""
    try:
        img = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return img_bytes
        H, W = img.shape[:2]
        cxr, cyr, rr = IMAGE_WM
        cx, cy = int(cxr * W), int(cyr * H)
        r = max(18, int(rr * W))
        mask = np.zeros((H, W), np.uint8)
        cv2.circle(mask, (cx, cy), r, 255, -1)
        out = cv2.inpaint(img, mask, 2, cv2.INPAINT_TELEA)
        ok, buf = cv2.imencode(".png", out)
        return buf.tobytes() if ok else img_bytes
    except Exception:
        return img_bytes


def strip_video_watermark(path: str) -> bool:
    """Remove the sparkle from a video file IN PLACE via ffmpeg `delogo`.
    Returns True on success, False (file unchanged) otherwise."""
    tmp = path + ".dewm.mp4"
    try:
        W, H = _probe_wh(path)
        x, y, w, h = _delogo_box(W, H, VIDEO_WM)
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", path,
             "-vf", f"delogo=x={x}:y={y}:w={w}:h={h}",
             "-c:a", "copy", "-movflags", "+faststart", tmp],
            check=True, timeout=300)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False
