import argparse
import math
import os
import time
from collections import Counter
from pathlib import Path

import cv2
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image
from torchvision import models, transforms

# ----------------------------- Config -----------------------------
BUNDLE_PATH = "efficientnetv2s_bundle.pth"
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

VIDEO_PATH = "vids/De Jong PURE CLASS vs Villarreal 🪄🎩.mp4"
EVERY_SEC = 1.0      # lower (e.g. 0.5) = more accurate, slower
FLAG_CONF = 0.90     # min confidence to flag a frame
BATCH_SIZE = 16
SEVERITY = ["nsfw", "suggestive"]   # most severe first

# ----------------------------- Model ------------------------------
cpu = torch.device("cpu")
torch.set_num_threads(os.cpu_count() or 1)

bundle = torch.load(BUNDLE_PATH, map_location=cpu, weights_only=True)

cpu_model = models.efficientnet_v2_s(weights=None)  # no download
cpu_model.classifier[1] = nn.Linear(
    cpu_model.classifier[1].in_features, bundle["num_classes"]
)
cpu_model.load_state_dict(bundle["state_dict"])
cpu_model = cpu_model.to(cpu).to(memory_format=torch.channels_last).eval()

pred_classes = bundle["classes"]

pred_tfm = transforms.Compose([
    transforms.Resize(bundle["resize"]),
    transforms.CenterCrop(bundle["img_size"]),
    transforms.ToTensor(),
    transforms.Normalize(bundle["mean"], bundle["std"]),
])

print("CPU model ready | classes:", pred_classes)


# --------------------------- Prediction ---------------------------
@torch.inference_mode()
def predict(image_path):
    """Classify a single image. Returns (label, confidence, {class: prob})."""
    img = Image.open(image_path).convert("RGB")
    x = pred_tfm(img).unsqueeze(0).contiguous(memory_format=torch.channels_last)
    probs = F.softmax(cpu_model(x), dim=1)[0]
    conf, idx = probs.max(dim=0)
    return (
        pred_classes[idx.item()],
        conf.item(),
        {c: probs[i].item() for i, c in enumerate(pred_classes)},
    )


@torch.inference_mode()
def predict_video(video_path, every_sec=1.0, batch_size=16):
    """Classify one frame every `every_sec` seconds. Returns a list of dicts."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise FileNotFoundError(f"Could not open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or math.isnan(fps) or fps <= 0:
        fps = 25.0
    step = max(1, int(round(fps * every_sec)))

    results, batch, times = [], [], []

    def flush():
        if not batch:
            return
        x = torch.stack(batch).contiguous(memory_format=torch.channels_last)
        probs = F.softmax(cpu_model(x), dim=1)
        for t, p in zip(times, probs):
            conf, idx = p.max(0)
            results.append({
                "time": t,
                "label": pred_classes[idx.item()],
                "conf": conf.item(),
                **{c: p[i].item() for i, c in enumerate(pred_classes)},
            })
        batch.clear()
        times.clear()

    try:
        i = 0
        while cap.grab():  # grab skips decoding unused frames
            if i % step == 0:
                ok, frame = cap.retrieve()
                if ok:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    batch.append(pred_tfm(Image.fromarray(rgb)))
                    times.append(i / fps)
                    if len(batch) == batch_size:
                        flush()
            i += 1
        flush()
    finally:
        cap.release()

    return results


def mmss(t):
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


# ------------------------------ Report ----------------------------
def report(results, flag_conf):
    total = len(results)
    if total == 0:
        print("No frames were processed.")
        return

    counts = Counter(r["label"] for r in results)
    print(f"Frames checked: {total}")
    for c in pred_classes:
        print(f"  {c}: {counts[c]} ({counts[c] / total * 100:.1f}%)")

    # Flagged moments
    for label in SEVERITY:
        hits = [r for r in results if r["label"] == label and r["conf"] >= flag_conf]
        if hits:
            print(
                f"\n{label.upper()} at: "
                + ", ".join(f"{mmss(r['time'])} ({r['conf'] * 100:.0f}%)" for r in hits)
            )

    # Overall verdict (most severe class found)
    for label in SEVERITY:
        if any(r["label"] == label and r["conf"] >= flag_conf for r in results):
            print(f"\nVERDICT: {label}")
            return
    print("\nVERDICT (no frame passed threshold):", counts.most_common(1)[0][0])


# ------------------------------ Main ------------------------------
def main():
    parser = argparse.ArgumentParser(description="Classify frames of a video or an image.")
    parser.add_argument("path", nargs="?", default=VIDEO_PATH, help="video or image path")
    parser.add_argument("--every-sec", type=float, default=EVERY_SEC)
    parser.add_argument("--flag-conf", type=float, default=FLAG_CONF)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        raise FileNotFoundError(path)

    if path.suffix.lower() in IMG_EXT:
        label, conf, probs = predict(path)
        print(f"{path.name}: {label} ({conf * 100:.1f}%)")
        for c, p in probs.items():
            print(f"  {c}: {p * 100:.1f}%")
        return

    t0 = time.time()
    results = predict_video(path, every_sec=args.every_sec, batch_size=args.batch_size)
    print(f"Done in {time.time() - t0:.0f}s")
    report(results, args.flag_conf)


if __name__ == "__main__":
    main()
