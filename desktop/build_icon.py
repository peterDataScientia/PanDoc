"""Build the Windows icon from the exact approved PanDockLab logo artwork.

The icon is the protein/flask emblem cropped from the same artwork used
in the Streamlit web logo. Do not draw a substitute or add subtitle text.
"""
from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "pandocklab_selected_logo.webp.b64"
TARGET = ROOT / "desktop" / "vendor" / "pandocklab.ico"


def build() -> None:
    encoded = SOURCE.read_text(encoding="ascii").strip()
    artwork = Image.open(BytesIO(base64.b64decode(encoded, validate=True))).convert("RGBA")
    if artwork.width < 100 or artwork.height < 70:
        raise RuntimeError("Approved PanDockLab logo is missing or invalid.")

    # Preserve the exact approved drawing; exclude only the bottom wordmark
    # because text is unreadable at 16px/32px Windows taskbar icon sizes.
    emblem = artwork.crop((int(artwork.width * 0.13), int(artwork.height * 0.05),
                           int(artwork.width * 0.90), int(artwork.height * 0.74)))
    emblem.thumbnail((228, 228), Image.Resampling.LANCZOS)
    # Scale uniformly; never stretch molecular structures or typography.
    factor = min(228 / emblem.width, 228 / emblem.height)
    target_size = (round(emblem.width * factor), round(emblem.height * factor))
    emblem = emblem.resize(target_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    canvas.alpha_composite(emblem, ((256 - emblem.width) // 2, (256 - emblem.height) // 2))
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(TARGET, format="ICO",
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                       (64, 64), (128, 128), (256, 256)])
    print(f"PanDockLab icon derived from approved artwork: {TARGET}")


if __name__ == "__main__":
    build()
