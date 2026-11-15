"""Tests for shot_motion, shot_light, shot_decon. Run: python3 -I -B tests/test_motion_light_decon.py (or pytest)."""
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import shot_decon  # noqa: E402
import shot_light  # noqa: E402
import shot_motion  # noqa: E402

TD = HERE.parents[4] / "skills" / "261105-scan-and-index-hard-drives" / "test_drive" / "TEST_DRIVE_v1"


def texture(seed=1, w=1280, h=720):
    rng = np.random.default_rng(seed)
    b = (rng.random((h // 4, w // 4, 3)) * 255).astype(np.uint8)
    return cv2.GaussianBlur(cv2.resize(b, (w, h), interpolation=cv2.INTER_LINEAR), (0, 0), 2)


def shift(img, dx, dy=0):
    return cv2.warpAffine(img, np.float32([[1, 0, dx], [0, 1, dy]]), img.shape[1::-1], borderMode=cv2.BORDER_REFLECT)


def scale(img, s):
    h, w = img.shape[:2]
    return cv2.warpAffine(img, cv2.getRotationMatrix2D((w / 2, h / 2), 0, s), (w, h), borderMode=cv2.BORDER_REFLECT)


def all_trusted(d):
    for k, v in d.items():
        assert set(v) >= {"value", "trust", "rule"}, k
        assert v["trust"] in ("measured", "probe"), (k, v["trust"])


def test_pan():
    b = texture()
    out = shot_motion.measure([shift(b, -10 * k) for k in range(4)], dt=1 / 24)
    all_trusted(out)
    assert out["camera_move"]["value"] == "pan right", out["camera_move"]
    out = shot_motion.measure([shift(b, 10 * k) for k in range(4)], dt=1 / 24)
    assert out["camera_move"]["value"] == "pan left"


def layered(fn_bg, fn_fg, k):
    """background plane + a near layer over the bottom-left quarter (moves more: parallax)."""
    bg, fg = fn_bg(texture(2), k), fn_fg(texture(9), k)
    h, w = bg.shape[:2]
    out = bg.copy()
    out[h // 2:, : w // 2] = fg[h // 2:, : w // 2]
    return out


def test_zoom_without_parallax():
    b = texture(2)
    out = shot_motion.measure([scale(b, 1 + 0.01 * k) for k in range(4)], dt=1 / 24)
    assert out["camera_move"]["value"] == "zoom in", out["camera_move"]
    out = shot_motion.measure([scale(b, 1 - 0.01 * k) for k in range(4)], dt=1 / 24)
    assert out["camera_move"]["value"] == "zoom out"


def test_push_with_parallax():
    fr = [layered(lambda i, k: scale(i, 1 + 0.01 * k), lambda i, k: scale(i, 1 + 0.03 * k), k) for k in range(4)]
    out = shot_motion.measure(fr, dt=1 / 24)
    assert out["camera_move"]["value"] == "push in", (out["camera_move"], out["motion_numbers"]["value"])


def test_truck_with_parallax():
    fr = [layered(lambda i, k: shift(i, -6 * k), lambda i, k: shift(i, -12 * k), k) for k in range(4)]
    out = shot_motion.measure(fr, dt=1 / 24)
    assert out["camera_move"]["value"] == "truck right", (out["camera_move"], out["motion_numbers"]["value"])


def test_static_camera_moving_subject():
    b, f = texture(3), texture(11)
    fr = []
    for k in range(4):
        img = b.copy()
        x = 400 + 10 * k
        img[300:420, x:x + 160] = f[300:420, 400:560]
        fr.append(img)
    out = shot_motion.measure(fr, dt=1 / 24)
    assert out["camera_move"]["value"] == "static", out["camera_move"]
    assert out["subject_manner"]["value"] != "still" and out["subject_direction"]["value"] == "R", out["subject_manner"]
    assert out["motion_numbers"]["value"]["difference_matte_share"] > 0     # the matte sees movement; it does not label


def test_static_locked():
    b = texture(3)
    out = shot_motion.measure([b, b, b], dt=1 / 24)
    assert out["camera_move"]["value"] == "static" and out["steadiness"]["value"] == "locked"


def warm_sky_frame():
    h, w = 720, 1280
    img = np.zeros((h, w, 3), np.uint8)
    for yy in range(h // 3):                                # smooth warm sky, brightest at the horizon
        t = yy / (h / 3)
        img[yy] = (40 + 30 * t, 110 + 50 * t, 200 + 40 * t)  # BGR: orange
    img[h // 3:] = (35, 40, 50)                             # dark ground
    rng = np.random.default_rng(0)
    img[h // 3:] = np.clip(img[h // 3:].astype(int) + rng.integers(-15, 15, img[h // 3:].shape), 0, 255).astype(np.uint8)
    return img


def test_warm_top_sunset():
    out = shot_light.measure([warm_sky_frame()])
    all_trusted(out)
    assert out["time_of_day"]["value"] in ("sunset", "golden hour"), out["time_of_day"]
    assert "CCT" in out["time_of_day"]["note"] and "R/B" in out["time_of_day"]["note"]
    assert out["time_of_day_probe"]["value"] in ("not run", "not installed")


def test_dark_night_lowkey():
    rng = np.random.default_rng(4)
    dark = (rng.random((360, 640, 3)) * 20).astype(np.uint8)
    assert shot_light.measure([dark])["time_of_day"]["value"] == "night"
    assert shot_decon.measure([dark])["bw"]["value"]["shape"] in ("low-key", "flat")
    bright_patch = dark.copy()
    bright_patch[100:200, 100:300] = 60
    assert shot_decon.measure([bright_patch])["bw"]["value"]["p50"] < 60


def test_probe_missing(tmp="/nonexistent_cinemanet"):
    out = shot_light.measure([warm_sky_frame()], cinemanet=tmp)
    assert out["time_of_day_probe"]["value"] == "not installed"
    assert "cinemanet.md" in out["time_of_day_probe"]["note"]
    assert out["time_of_day"]["value"] in ("sunset", "golden hour")    # probe never overwrites


def test_difference_matte():
    b = texture(5)
    same = shot_decon.measure([b, b.copy()])["difference"]["value"]
    assert same["changed_share"] == 0 and same["attribution"] == "none"
    moved = shot_decon.measure([b, shift(b, -24)])["difference"]["value"]
    assert moved["changed_share"] > 0 and moved["attribution"] == "camera", moved
    sub = b.copy()
    sub[300:420, 600:720] = 255 - sub[300:420, 600:720]
    local = shot_decon.measure([b, sub])["difference"]["value"]
    assert local["attribution"] == "subject" and "centre" in local["where"], local


def test_posterize_deterministic():
    b = texture(6)
    a1 = shot_decon.measure([b], levels=5)
    a2 = shot_decon.measure([b.copy()], levels=5)
    assert a1 == a2
    all_trusted(a1)


def test_test_drive_end_to_end():
    vids = sorted(TD.glob("*/*/DCIM/*/*.MP4")) + sorted(TD.glob("*/*/DCIM/*/*.MOV"))
    assert vids, "TEST_DRIVE_v1 clips not found under %s" % TD
    for v in vids[:3]:
        for mod in (shot_motion, shot_light, shot_decon):
            out = mod.measure(str(v))
            all_trusted(out)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
        try:
            fn()
            print("PASS", name)
        except Exception as e:  # noqa: BLE001
            fails += 1
            print("FAIL", name, repr(e)[:300])
    print("%d failed" % fails)
    sys.exit(1 if fails else 0)
