"""Generate Claude Gateway app icon — 1024x1024 PNG"""
from pathlib import Path
from PIL import Image, ImageDraw
import math

SIZE = 1024
img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Solid Claude orange background
orange = (232, 113, 74)
draw.rounded_rectangle([(0, 0), (SIZE - 1, SIZE - 1)], radius=180, fill=orange)

cx, cy = SIZE // 2, SIZE // 2
white = (255, 255, 255)

# === Gateway arch — smaller, centered ===
pillar_w = 52
arch_r = 120
base_y = cy + 155
pillar_top = cy - 95

# Left pillar
lx = cx - arch_r
draw.rounded_rectangle(
    [(lx - pillar_w // 2, pillar_top), (lx + pillar_w // 2, base_y)],
    radius=8, fill=white
)

# Right pillar
rx = cx + arch_r
draw.rounded_rectangle(
    [(rx - pillar_w // 2, pillar_top), (rx + pillar_w // 2, base_y)],
    radius=8, fill=white
)

# Arch
arch_cy = pillar_top
bbox = [
    cx - arch_r - pillar_w // 2,
    arch_cy - arch_r - pillar_w // 2,
    cx + arch_r + pillar_w // 2,
    arch_cy + arch_r + pillar_w // 2,
]
draw.arc(bbox, start=180, end=360, fill=white, width=pillar_w)

# Base
draw.rounded_rectangle(
    [(cx - arch_r - pillar_w - 10, base_y),
     (cx + arch_r + pillar_w + 10, base_y + 22)],
    radius=11, fill=white
)

# === AI Sparkle centered in the archway ===
spark_cy = cy + 40
spark_h = 42
spark_w = 25

# Diamond
draw.polygon([
    (cx, spark_cy - spark_h),
    (cx + spark_w, spark_cy),
    (cx, spark_cy + spark_h),
    (cx - spark_w, spark_cy),
], fill=white)

# Four accent dots
dot_r = 7
for angle in [45, 135, 225, 315]:
    rad = math.radians(angle)
    dx = cx + 50 * math.cos(rad)
    dy = spark_cy + 50 * math.sin(rad)
    draw.ellipse([(dx - dot_r, dy - dot_r), (dx + dot_r, dy + dot_r)], fill=white)

# Save
for name in ['icon.png', 'adaptive-icon.png', 'splash-icon.png']:
    path = str(Path(__file__).parent / 'assets' / name)
    img.save(path, 'PNG')

print('Icons generated')
