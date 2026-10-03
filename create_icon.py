"""
create_icon.py - Generates a professional Windows .ico icon for WinVoice.
"""

from PIL import Image, ImageDraw
import os

def generate_icon(path="app_icon.ico"):
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # 1. Outer glowing rounded background
    pad = 12
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=54,
        fill=(15, 17, 26, 255),
        outline=(56, 189, 248, 220),
        width=4
    )
    
    # 2. Concentric inner glow
    draw.ellipse([50, 50, size - 50, size - 50], fill=(26, 115, 242, 60))
    draw.ellipse([70, 70, size - 70, size - 70], fill=(37, 99, 235, 90))

    # 3. Stylized Microphone
    # Mic body capsule
    mx1, my1, mx2, my2 = 108, 65, 148, 145
    draw.rounded_rectangle([mx1, my1, mx2, my2], radius=20, fill=(248, 250, 252, 255))
    
    # Mic U-arc cradle
    draw.arc([92, 95, 164, 165], start=0, end=180, fill=(56, 189, 248, 255), width=6)
    
    # Mic stem
    draw.line([128, 165, 128, 195], fill=(56, 189, 248, 255), width=6)
    # Mic base stand
    draw.line([106, 195, 150, 195], fill=(56, 189, 248, 255), width=6)

    # 4. Audio waves on sides
    # Left wave
    draw.arc([68, 90, 88, 140], start=110, end=250, fill=(96, 165, 250, 200), width=4)
    # Right wave
    draw.arc([168, 90, 188, 140], start=290, end=70, fill=(96, 165, 250, 200), width=4)

    # Save as multi-resolution ICO
    img.save(
        path,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    )
    print(f"Generated icon at {path}")

if __name__ == "__main__":
    generate_icon()
