import sys
from PIL import Image, ImageDraw, ImageFont
src = sys.argv[1] if len(sys.argv) > 1 else "escudo_real_madrid.txt"
out = sys.argv[2] if len(sys.argv) > 2 else "escudo_real_madrid.png"
with open(src, encoding="utf-8") as f:
    lines = f.read().split("\n")
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 20)
cw, ch = 12, 26
W = max((len(l) for l in lines), default=1) * cw + 40
H = len(lines) * ch + 40
img = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(img)
for i, line in enumerate(lines):
    d.text((20, 20 + i*ch), line, fill="black", font=font)
img.save(out)
print("OK", out, W, "x", H)
