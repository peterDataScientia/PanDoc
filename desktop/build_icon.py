"""Build a deterministic PanDockLab Windows application icon using Pillow."""
from pathlib import Path
from PIL import Image, ImageDraw

TARGET = Path(__file__).resolve().parent / "vendor" / "pandocklab.ico"

def build():
    size = 256
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((1, 1, 254, 254), radius=53, fill=(12, 35, 67, 255))
    # Stylized PanDockLab P with open binding cavity.
    d.rounded_rectangle((55, 39, 117, 215), radius=16, fill=(17, 111, 163, 255))
    d.rounded_rectangle((68, 39, 211, 176), radius=56, fill=(17, 111, 163, 255))
    d.ellipse((111, 71, 172, 131), fill=(12, 35, 67, 255))
    # Ligand network projected across the pocket.
    bonds = [((132, 100), (155, 88)), ((155, 88), (180, 100)), ((180, 100), (181, 125)),
             ((181, 125), (157, 139)), ((157, 139), (133, 126)), ((133, 126), (132, 100)),
             ((181, 125), (209, 139))]
    for a, b in bonds:
        d.line((a, b), fill=(113, 214, 77, 255), width=7)
    for x, y in [(132, 100), (155, 88), (180, 100), (181, 125), (157, 139),
                 (133, 126), (209, 139)]:
        d.ellipse((x-7, y-7, x+7, y+7), fill=(117, 231, 92, 255))
    # Scientific green-blue orbital orbit.
    d.arc((12, 118, 239, 245), 12, 158, fill=(57, 192, 171, 255), width=9)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    im.save(TARGET, format="ICO", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    print(f"Created PanDockLab icon: {TARGET}")

if __name__ == "__main__":
    build()
