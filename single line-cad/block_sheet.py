#!/usr/bin/env python3
"""
block_sheet.py -- 块库对照图：每个块长什么样 + 对应名字，方便起名/确认

用法:  python block_sheet.py [--out out/块库对照图.png] [--cols 3]
"""

import argparse
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from PIL import Image, ImageDraw, ImageFont      # noqa: E402

import wiring_raw as wr                          # noqa: E402
import blockui_server as ui                      # noqa: E402

CJK_FONTS = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
             r"C:\Windows\Fonts\simsun.ttc"]


def load_font(size):
    for p in CJK_FONTS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def cell_prims(path):
    """读一个块文件，返回 (展平图元, 尺寸, 接点数)。"""
    sec, _o = wr.parse_sections_text(wr.read_dxf_text(path))
    bmap = wr._blocks_map(sec)
    recs = wr.group_entities(sec.get("ENTITIES", []))
    prims = []
    wr._prim_list(recs, bmap, (1, 0, 0, 1, 0, 0), 0, prims)
    n = sum(1 for r in recs if r[0][1] == "POINT" and (wr._g1(r, "8") or "").upper().startswith("CONN"))
    for m in bmap.values():
        n += sum(1 for r in m if r[0][1] == "POINT" and (wr._g1(r, "8") or "").upper().startswith("CONN"))
    return prims, wr._prims_bbox(prims), n


def main(argv=None):
    ap = argparse.ArgumentParser(description="块库对照图")
    ap.add_argument("--out", default=os.path.join(HERE, "out", "块库对照图.png"))
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--cell", type=int, default=340)
    a = ap.parse_args(argv)

    d = ui.BLOCKS_DIR
    files = sorted(f for f in os.listdir(d) if f.lower().endswith(".dxf"))
    if not files:
        print("块库里没有 .dxf"); return 1

    items = []
    for fn in files:
        nm = os.path.splitext(fn)[0]
        try:
            prims, bb, nconn = cell_prims(os.path.join(d, fn))
        except Exception as ex:
            prims, bb, nconn = [], None, 0
            print("  %s 读不了: %s" % (nm, ex))
        items.append((nm, prims, bb, nconn))

    cols = max(1, a.cols)
    rows = (len(items) + cols - 1) // cols
    pad, top, bottom = 16, 26, 40
    cw = ch = a.cell
    W = cols * cw + pad
    H = rows * ch + pad
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    f_name = load_font(17)
    f_info = load_font(13)

    for i, (nm, prims, bb, nconn) in enumerate(items):
        cx = pad + (i % cols) * cw
        cy = pad + (i // cols) * ch
        box = (cx, cy, cx + cw - pad, cy + ch - pad)
        dr.rectangle(box, outline=(210, 214, 222))
        dr.line([cx, cy + top - 6, cx + cw - pad, cy + top - 6], fill=(210, 214, 222))

        if bb:
            iw = box[2] - box[0] - 16
            ih = box[3] - box[1] - top - bottom
            sw = max(bb[1] - bb[0], 1e-6)
            sh = max(bb[3] - bb[2], 1e-6)
            sc = min(iw / sw, ih / sh)
            ox = box[0] + 8 + (iw - sw * sc) / 2 - bb[0] * sc
            oy = box[1] + top + (ih - sh * sc) / 2 + bb[3] * sc

            def X(x):
                return ox + x * sc

            def Y(y):
                return oy - y * sc

            for p in prims:
                if p[0] == "poly":
                    pts = [(X(q[0]), Y(q[1])) for q in p[1]]
                    if len(pts) > 1:
                        dr.line(pts, fill=(120, 124, 132), width=1)
                elif p[0] == "circle":
                    dr.ellipse([X(p[1] - p[3]), Y(p[2] + p[3]),
                                X(p[1] + p[3]), Y(p[2] - p[3])], outline=(120, 124, 132), width=1)
                elif p[0] == "text":
                    dr.text((X(p[1]), Y(p[2])), str(p[3])[:16], fill=(150, 154, 162), font=f_info)
            info = "%.1f x %.1f   接点 %d" % (bb[1] - bb[0], bb[3] - bb[2], nconn)
        else:
            info = "（没有图形）"

        dr.text((box[0] + 8, box[1] + 8), nm, fill=(20, 24, 32), font=f_name)
        dr.text((box[0] + 8, box[3] - bottom + 14), info, fill=(120, 124, 132), font=f_info)

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    img.save(a.out)
    print("对照图: %s  (%d 个块, %dx%d)" % (a.out, len(items), W, H))
    for nm, _p, bb, nconn in items:
        print("   %-10s %s  接点 %d" % (nm,
              ("%.1f x %.1f" % (bb[1] - bb[0], bb[3] - bb[2])) if bb else "空", nconn))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
