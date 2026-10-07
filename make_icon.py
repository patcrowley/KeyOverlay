"""Generate KeyOverlay.ico — a fun keycap + sparkle icon."""
from PIL import Image, ImageDraw, ImageFont
import math, os

def draw_icon(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size

    # ── Background pill ──────────────────────────────────────────────────
    r = s * 0.18  # corner radius
    bg_col = (18, 18, 38, 255)          # deep navy
    d.rounded_rectangle([0, 0, s-1, s-1], radius=r, fill=bg_col)

    # ── Keycap body ──────────────────────────────────────────────────────
    pad = s * 0.12
    cap_r = s * 0.10
    cap_col  = (230, 230, 240, 255)     # light silver
    cap_shad = (120, 120, 140, 255)     # shadow edge
    # shadow (bottom-right offset)
    off = max(1, int(s * 0.04))
    d.rounded_rectangle(
        [pad + off, pad + off, s - pad + off, s * 0.72 + off],
        radius=cap_r, fill=cap_shad
    )
    # main cap face
    d.rounded_rectangle(
        [pad, pad, s - pad, s * 0.72],
        radius=cap_r, fill=cap_col
    )
    # inner bevel highlight
    bv = max(1, int(s * 0.03))
    bevel_col = (255, 255, 255, 200)
    d.rounded_rectangle(
        [pad + bv, pad + bv, s - pad - bv, s * 0.72 - bv],
        radius=max(2, cap_r - bv), fill=None, outline=bevel_col,
        width=max(1, int(s * 0.025))
    )

    # ── "KO" label on the key ───────────────────────────────────────────
    label_col = (50, 50, 80, 255)
    cx, cy = s // 2, int(s * 0.38)
    label_size = max(8, int(s * 0.28))
    try:
        font = ImageFont.truetype("arialbd.ttf", label_size)
    except:
        try:
            font = ImageFont.truetype("arial.ttf", label_size)
        except:
            font = ImageFont.load_default()

    text = "KO"
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((cx - tw // 2, cy - th // 2), text, font=font, fill=label_col)

    # ── Sparkle / overlay burst (top-right corner of cap) ───────────────
    spx = int(s * 0.75)
    spy = int(s * 0.18)
    spark_r = s * 0.16
    # glow halo
    for glow_r, alpha in [(spark_r * 2.2, 40), (spark_r * 1.7, 70), (spark_r * 1.3, 110)]:
        glow_col = (160, 80, 255, alpha)
        d.ellipse(
            [spx - glow_r, spy - glow_r, spx + glow_r, spy + glow_r],
            fill=glow_col
        )
    # star rays
    ray_col   = (220, 160, 255, 255)
    ray_outer = spark_r * 0.95
    ray_inner = spark_r * 0.38
    pts = []
    for i in range(8):
        angle = math.radians(i * 45 - 90)
        r_use = ray_outer if i % 2 == 0 else ray_inner
        pts.append((spx + r_use * math.cos(angle),
                     spy + r_use * math.sin(angle)))
    d.polygon(pts, fill=(255, 200, 255, 255))
    # bright center dot
    cd = spark_r * 0.28
    d.ellipse([spx - cd, spy - cd, spx + cd, spy + cd],
              fill=(255, 255, 255, 255))

    # ── Bottom stem of key ───────────────────────────────────────────────
    stem_x0 = s * 0.30
    stem_x1 = s * 0.70
    stem_y0 = s * 0.72
    stem_y1 = s * 0.82
    stem_col = (180, 180, 195, 255)
    d.rectangle([stem_x0, stem_y0, stem_x1, stem_y1], fill=stem_col)

    # base bar
    base_y0 = s * 0.82
    base_y1 = s * 0.88
    d.rectangle([pad, base_y0, s - pad, base_y1], fill=stem_col)

    return img


if __name__ == "__main__":
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [draw_icon(sz) for sz in sizes]

    # Save from the largest frame; Pillow drops sizes bigger than the base image.
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "KeyOverlay.ico")
    frames[-1].save(out, format="ICO", sizes=[(sz, sz) for sz in sizes],
                    append_images=frames[:-1])
    print(f"Saved: {out}")
