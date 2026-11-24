from pathlib import Path
from PIL import Image
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import models, transforms
import os
import cv2
from collections import Counter
import time

# --------------------------------------------------
# Configuration
# --------------------------------------------------
BUNDLE_PATH = "efficientnetv2s_bundle.pth"
VIDEO_PATH = "vids/De Jong PURE CLASS vs Villarreal 🪄🎩.mp4"

EVERY_SEC = 1.0      # Lower values sample more frames (slower, potentially more accurate)
FLAG_CONF = 0.90     # Minimum confidence required to flag a frame
BATCH_SIZE = 16

# --------------------------------------------------
# Load model
# --------------------------------------------------
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

cpu = torch.device("cpu")
torch.set_num_threads(os.cpu_count() or 1)

bundle = torch.load(
    BUNDLE_PATH,
    map_location=cpu,
    weights_only=True
)

cpu_model = models.efficientnet_v2_s(weights=None)  # No pretrained-weight download
cpu_model.classifier[1] = nn.Linear(
    cpu_model.classifier[1].in_features,
    bundle["num_classes"]
)
cpu_model.load_state_dict(bundle["state_dict"])
cpu_model = (
    cpu_model.to(cpu)
    .to(memory_format=torch.channels_last)
    .eval()
)

pred_classes = bundle["classes"]

pred_tfm = transforms.Compose([
    transforms.Resize(bundle["resize"]),
    transforms.CenterCrop(bundle["img_size"]),
    transforms.ToTensor(),
    transforms.Normalize(bundle["mean"], bundle["std"]),
])

print("CPU model ready | classes:", pred_classes)


# --------------------------------------------------
# Image prediction
# --------------------------------------------------
@torch.inference_mode()
def predict(image_path):
    img = Image.open(image_path).convert("RGB")
    x = pred_tfm(img).unsqueeze(0).contiguous(
        memory_format=torch.channels_last
    )

    probs = F.softmax(cpu_model(x), dim=1)[0]
    conf, idx = probs.max(dim=0)

    return (
        pred_classes[idx.item()],
        conf.item(),
        {
            c: probs[i].item()
            for i, c in enumerate(pred_classes)
        }
    )


# --------------------------------------------------
# Video prediction
# --------------------------------------------------
@torch.inference_mode()
def predict_video(video_path, every_sec=1.0, batch_size=16):
    cap = cv2.VideoCapture(str(video_path))

    if not cap.isOpened():
        raise FileNotFoundError(video_path)

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    step = max(1, int(round(fps * every_sec)))

    results = []
    batch = []
    times = []

    def flush():
        if not batch:
            return

        x = torch.stack(batch).contiguous(
            memory_format=torch.channels_last
        )
        probs = F.softmax(cpu_model(x), dim=1)

        for t, p in zip(times, probs):
            conf, idx = p.max(0)

            results.append({
                "time": t,
                "label": pred_classes[idx.item()],
                "conf": conf.item(),
                **{
                    c: p[i].item()
                    for i, c in enumerate(pred_classes)
                },
            })

        batch.clear()
        times.clear()

    try:
        i = 0

        while cap.grab():  # Grab skips decoding unused frames
            if i % step == 0:
                ok, frame = cap.retrieve()

                if ok:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    batch.append(
                        pred_tfm(Image.fromarray(rgb))
                    )
                    times.append(i / fps)

                    if len(batch) == batch_size:
                        flush()

            i += 1

        flush()

    finally:
        cap.release()

    return results


# --------------------------------------------------
# Timestamp formatting
# --------------------------------------------------
def mmss(t):
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


# --------------------------------------------------
# Run video detection
# --------------------------------------------------
if __name__ == "__main__":
    if not Path(VIDEO_PATH).is_file():
        raise FileNotFoundError(f"Video not found: {VIDEO_PATH}")

    t0 = time.time()
    results = predict_video(
        VIDEO_PATH,
        every_sec=EVERY_SEC,
        batch_size=BATCH_SIZE
    )

    print(f"Done in {time.time() - t0:.0f}s")

    # Summary
    counts = Counter(r["label"] for r in results)
    total = len(results)

    print(f"Frames checked: {total}")

    if total == 0:
        raise RuntimeError("No video frames were successfully processed.")

    for c in pred_classes:
        percentage = counts[c] / total * 100
        print(f"  {c}: {counts[c]} ({percentage:.1f}%)")

    # Flagged moments
    for label in ["nsfw", "suggestive"]:
        hits = [
            r for r in results
            if r["label"] == label and r["conf"] >= FLAG_CONF
        ]

        if hits:
            moments = ", ".join(
                f"{mmss(r['time'])} ({r['conf'] * 100:.0f}%)"
                for r in hits
            )
            print(f"\n{label.upper()} at: {moments}")

    # Overall verdict: most severe class found above threshold
    if any(
        r["label"] == "nsfw" and r["conf"] >= FLAG_CONF
        for r in results
    ):
        print("\nVERDICT: nsfw")

    elif any(
        r["label"] == "suggestive" and r["conf"] >= FLAG_CONF
        for r in results
    ):
        print("\nVERDICT: suggestive")

    else:
        print(
            "VERDICT (no frame passed threshold):",
            counts.most_common(1)[0][0]
        )
