#!/usr/bin/env python3
"""imgtext — kepen levo felirat cserelese/forditasa (PIL + numpy, se cv2, se scipy).

Ket mod:

  # 1) merés: hol vannak a szovegsorok, mekkora a betu, mekkora a regi felirat?
  python3 imgtext.py probe kep.jpg --region 157,6,1049,292

  # 2) csere: a job.json leirja, mit hova kell irni
  python3 imgtext.py run job.json

A job.json schemaja: lasd README.md / example-job.json.
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


# ------------------------------------------------------------------ alapok
def components(dark: np.ndarray):
    """Osszefuggo sotet komponensek: [(pontok, erinti-e a regio szelet)]."""
    h, w = dark.shape
    seen = np.zeros_like(dark, bool)
    out = []
    for sy in range(h):
        for sx in np.where(dark[sy] & ~seen[sy])[0]:
            if seen[sy, sx]:
                continue
            stack = [(sy, sx)]
            seen[sy, sx] = True
            pts, touch = [], False
            while stack:
                y, x = stack.pop()
                pts.append((y, x))
                if y in (0, h - 1) or x in (0, w - 1):
                    touch = True
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < h and 0 <= nx < w and dark[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            stack.append((ny, nx))
            out.append((pts, touch))
    return out


def dilate(mask: np.ndarray, r: int, diag: bool = True) -> np.ndarray:
    for _ in range(r):
        g = mask.copy()
        g[1:, :] |= mask[:-1, :]
        g[:-1, :] |= mask[1:, :]
        g[:, 1:] |= mask[:, :-1]
        g[:, :-1] |= mask[:, 1:]
        if diag:
            g[1:, 1:] |= mask[:-1, :-1]
            g[:-1, :-1] |= mask[1:, 1:]
            g[1:, :-1] |= mask[:-1, 1:]
            g[:-1, 1:] |= mask[1:, :-1]
        mask = g
    return mask


def box_blur(img: np.ndarray, r: int) -> np.ndarray:
    """Gyors separable box-blur float tombokon (Gaussian kozelites)."""
    k = 2 * r + 1
    pad = np.pad(img, [(r, r), (r, r)] + [(0, 0)] * (img.ndim - 2), mode="edge")
    cs = np.cumsum(pad, axis=0)
    cs = np.concatenate([np.zeros_like(cs[:1]), cs], axis=0)
    img = (cs[k:] - cs[:-k]) / k
    cs = np.cumsum(img, axis=1)
    cs = np.concatenate([np.zeros_like(cs[:, :1]), cs], axis=1)
    return (cs[:, k:] - cs[:, :-k]) / k


# ------------------------------------------------------------------ probe
def probe(path: str, region=None) -> None:
    """Szovegsavok merese: sorok, x-kiterjedes, ascender-magassag, becsult em."""
    im = Image.open(path).convert("RGB")
    a = np.array(im).astype(int)
    lum = a.mean(axis=2)
    x0, y0, x1, y1 = region or (0, 0, im.width, im.height)
    sub = lum[y0:y1, x0:x1]
    # csak a "maganyos" sotet foltok (a regio szelet erinto = hatter/poszter)
    dark = np.zeros(sub.shape, bool)
    for pts, touch in components(sub < 110):
        if touch:
            continue
        for y, x in pts:
            dark[y, x] = True

    rows = dark.sum(axis=1)
    bands, inb, start = [], False, 0
    for y, v in enumerate(rows):
        if v and not inb:
            start, inb = y, True
        elif not v and inb:
            bands.append((start, y - 1))
            inb = False
    if inb:
        bands.append((start, len(rows) - 1))

    print(f"# {path}  region={x0},{y0},{x1},{y1}")
    for i, (b0, b1) in enumerate(bands, 1):
        cols = np.where(dark[b0:b1 + 1].any(axis=0))[0]
        print(f"line {i}: y {b0 + y0}-{b1 + y0}  h={b1 - b0 + 1}  "
              f"x {cols.min() + x0}-{cols.max() + x0}  w={cols.max() - cols.min() + 1}")
    if bands:
        ink_h = max(b1 - b0 + 1 for b0, b1 in bands)
        print(f"\nlegnagyobb tintamagassag: {ink_h}px  (em ~ {ink_h / 0.73:.0f}px, "
              f"ha nincs leszallo; kulonben /0.94)")
        print(f"kozepvonal x: {(x0 + x1) / 2:.1f}")


# -------------------------------------------------------------- caption
def erase_line_text(a: np.ndarray, region, core=170, radius=4, halo=254) -> np.ndarray:
    """A regioban maganyosan allo sotet foltok (betuk) eltuntetese feherre."""
    x0, y0, x1, y1 = region
    lum = a.mean(axis=2)
    sub = lum[y0:y1, x0:x1] < core
    mask = np.zeros(sub.shape, bool)
    for pts, touch in components(sub):
        if touch:                       # a regio szelet erinti -> nem betu
            continue
        for y, x in pts:
            mask[y, x] = True
    mask = dilate(mask, radius)
    erase = mask & (lum[y0:y1, x0:x1] < halo)
    arr = np.array(a, dtype=np.uint8)
    arr[y0:y1, x0:x1][erase] = (255, 255, 255)
    return arr


def draw_squeezed(base: Image.Image, text: str, font, baseline: float, cx: float,
                  fill, sx: float = 1.0) -> None:
    """Vizszintesen osszenyomott, kozepre igazitott szoveg adott baseline-nal."""
    tmp = Image.new("RGBA", (4 * base.width, 400), (0, 0, 0, 0))
    dd = ImageDraw.Draw(tmp)
    dd.text((tmp.width / 2, 200), text, font=font, anchor="ms", fill=fill + (255,))
    bb = tmp.getbbox()
    if not bb:
        return
    ink = tmp.crop(bb)
    up = 200 - bb[1]
    neww = max(1, round(ink.width * sx))
    ink = ink.resize((neww, ink.height), Image.LANCZOS)
    base.alpha_composite(ink, (round(cx - neww / 2), round(baseline - up)))


# ------------------------------------------------------------------ sign
def rebuild_placard(im: Image.Image, box, quad, dark_thresh=190, blur_r=10,
                    border_width=7, noise=2.6, seed=11) -> Image.Image:
    """A tabla belsejének ujraepitese sima papirral; a kezirasos keret marad."""
    x0, y0, x1, y1 = box
    a = np.array(im.crop(box)).astype(float)
    lum = a.mean(axis=2)

    dark = dilate(lum < dark_thresh, 2)
    w = (~dark).astype(float)
    num = np.stack([box_blur(a[:, :, c] * w, blur_r) for c in range(3)], axis=2)
    den = box_blur(w, blur_r)
    bg = num / np.maximum(den, 1e-3)[:, :, None]
    rng = np.random.default_rng(seed)
    bg = np.clip(bg + rng.normal(0, noise, (a.shape[0], a.shape[1], 1)), 0, 255)

    q = [(x - x0, y - y0) for x, y in quad]
    poly = Image.new("L", im.crop(box).size, 0)
    pd = ImageDraw.Draw(poly)
    pd.polygon(q, fill=255)
    pd.line(q + [q[0]], fill=0, width=border_width, joint="curve")
    alpha = np.array(poly.filter(ImageFilter.GaussianBlur(1.4))).astype(float) / 255.0

    out = a * (1 - alpha[:, :, None]) + bg * alpha[:, :, None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def draw_sign_text(im: Image.Image, box, center, text_lines, font_path, size,
                   track=4.0, angle=0.0, color=(78, 66, 46)) -> Image.Image:
    f = ImageFont.truetype(font_path, size)
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx0, cy0 = im.width / 2, im.height / 2
    for text, dy in text_lines:
        widths = [d.textlength(ch, font=f) for ch in text]
        total = sum(widths) + track * (len(text) - 1)
        x = cx0 - total / 2
        for ch, wd in zip(text, widths):
            d.text((x, cy0 + dy), ch, font=f, fill=color + (255,), anchor="ls")
            x += wd + track
    layer = layer.rotate(angle, resample=Image.BICUBIC, center=(cx0, cy0))
    out = im.convert("RGBA")
    out.alpha_composite(layer, (round(center[0] - cx0), round(center[1] - cy0)))
    return out.convert("RGB")


# ------------------------------------------------------------------- run
def run(job_path: str) -> None:
    job = json.load(open(job_path))
    im = Image.open(job["src"]).convert("RGB")

    cap = job.get("caption")
    if cap:
        a = np.array(im).astype(int)
        arr = erase_line_text(
            a, cap["region"],
            core=cap.get("core", 170),
            radius=cap.get("radius", 4),
            halo=cap.get("halo", 254),
        )
        im = Image.fromarray(arr).convert("RGBA")
        font = ImageFont.truetype(cap["font"], cap["size"])
        if cap.get("weight"):
            font.set_variation_by_axes([cap["weight"]])
        color = tuple(cap.get("color", [12, 12, 12]))
        for line, base in zip(cap["lines"], cap["baselines"]):
            draw_squeezed(im, line, font, base, cap["center_x"], color,
                          sx=cap.get("squeeze", 1.0))
        im = im.convert("RGB")

    sign = job.get("sign")
    if sign:
        paper = rebuild_placard(
            im, sign["box"], sign["quad"],
            border_width=sign.get("border_width", 7),
            noise=sign.get("noise", 2.6),
        )
        im.paste(paper, sign["box"][:2])
        im = draw_sign_text(
            im, sign["box"], sign["center"], sign["lines"], sign["font"],
            sign["size"], track=sign.get("tracking", 4.0),
            angle=sign.get("angle", 0.0), color=tuple(sign.get("color", [78, 66, 46])),
        )

    im.save(job["out"], quality=job.get("quality", 96), subsampling=0)
    print("saved", job["out"])


def main() -> None:
    ap = argparse.ArgumentParser(description="kepen levo felirat cserelese/forditasa")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="szovegsavok merese")
    p.add_argument("image")
    p.add_argument("--region", help="x0,y0,x1,y1")
    r = sub.add_parser("run", help="job.json vegrehajtasa")
    r.add_argument("job")
    args = ap.parse_args()

    if args.cmd == "probe":
        region = None
        if args.region:
            region = tuple(int(v) for v in args.region.split(","))
        probe(args.image, region)
    else:
        run(args.job)


if __name__ == "__main__":
    main()
