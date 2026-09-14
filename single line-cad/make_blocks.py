#!/usr/bin/env python3
"""
make_blocks.py -- 根据 blocklib/_manifest.csv 批量生成 AutoCAD 块起步模板(.dxf)

每个块 = 一个独立 .dxf 绘图文件，内含：
  - 图形符号（LINE/CIRCLE/TEXT）
  - ATTDEF 属性（插入时自动提示/由 LISP 自动填充 TAG 等）

用法:  python make_blocks.py
落地:  生成到 blocklib/blocks/<BLOCK>.dxf
在 CAD 里打开任意 .dxf -> 另存为同名 .dwg 放回库中，即可成为可维护的块库。
改图形后重跑本脚本可重新生成模板（或直接在 CAD 里编辑后另存 .dwg，库以 .dwg 为准）。

零第三方依赖。
"""

import csv
import os


HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "blocklib", "_manifest.csv")
OUTDIR = os.path.join(HERE, "blocklib", "blocks")


# ------------------------- 极简 R12 DXF 绘制 -------------------------
class Dxf:
    def __init__(self):
        self.e = []
        self.cx = self.cy = 0.0

    def _n(self, v):
        s = f"{float(v):.4f}".rstrip("0").rstrip(".")
        return s if s not in ("", "-0") else "0"

    def line(self, x1, y1, x2, y2, layer="EQUIP", lw=None):
        e = ["0", "LINE", "8", layer]
        e += ["10", self._n(x1), "20", self._n(y1), "30", "0"]
        e += ["11", self._n(x2), "21", self._n(y2), "31", "0"]
        self.e += e

    def rect(self, x, y, w, h, layer="EQUIP"):
        self.line(x, y, x + w, y, layer)
        self.line(x + w, y, x + w, y - h, layer)
        self.line(x + w, y - h, x, y - h, layer)
        self.line(x, y - h, x, y, layer)

    def circle(self, cx, cy, r, layer="EQUIP"):
        self.e += ["0", "CIRCLE", "8", layer,
                   "10", self._n(cx), "20", self._n(cy), "30", "0",
                   "40", self._n(r)]

    def text(self, x, y, s, h=2.0, layer="TEXT"):
        self.e += ["0", "TEXT", "8", layer,
                   "10", self._n(x), "20", self._n(y), "30", "0",
                   "40", self._n(h), "1", s, "50", "0",
                   "72", "1", "73", "2", "11", self._n(x), "21", self._n(y),
                   "31", "0"]

    def attdef(self, x, y, tag, prompt, default="", h=1.8, layer="TEXT"):
        # ATTDEF：插入后成为块属性
        self.e += ["0", "ATTDEF", "8", layer,
                   "10", self._n(x), "20", self._n(y), "30", "0",
                   "40", self._n(h), "1", default, "3", tag, "2", prompt,
                   "70", "0", "72", "1", "73", "2",
                   "11", self._n(x), "21", self._n(y), "31", "0",
                   "7", "STANDARD"]

    def point(self, x, y, layer="CONN"):
        # POINT 连接点：程序经 CONN 层读取(固定连接位置)
        self.e += ["0", "POINT", "8", layer,
                   "10", self._n(x), "20", self._n(y), "30", "0"]

    def save(self, path, title):
        header = ["0", "SECTION", "2", "HEADER",
                  "9", "$ACADVER", "1", "AC1009",
                  "9", "$EXTMIN", "10", "-20", "20", "-20", "30", "0",
                  "9", "$EXTMAX", "10", "40", "20", "40", "30", "0",
                  "0", "ENDSEC"]
        tables = ["0", "SECTION", "2", "TABLES",
                  "0", "TABLE", "2", "LTYPE", "70", "1",
                  "0", "LTYPE", "2", "CONTINUOUS", "70", "0", "3", "Solid line",
                  "72", "65", "73", "0", "40", "0.0",
                  "0", "ENDTAB",
                  "0", "TABLE", "2", "LAYER", "70", "4",
                  "0", "LAYER", "2", "0", "70", "0", "62", "7", "6", "CONTINUOUS",
                  "0", "LAYER", "2", "EQUIP", "70", "0", "62", "3", "6", "CONTINUOUS",
                  "0", "LAYER", "2", "TEXT", "70", "0", "62", "7", "6", "CONTINUOUS",
                  "0", "LAYER", "2", "CONN", "70", "0", "62", "4", "6", "CONTINUOUS",
                  "0", "ENDTAB",
                  "0", "TABLE", "2", "STYLE", "70", "1",
                  "0", "STYLE", "2", "STANDARD", "70", "0", "40", "0.0",
                  "41", "1.0", "50", "0.0", "71", "0", "42", "0.2",
                  "3", "txt", "4", "",
                  "0", "ENDTAB",
                  "0", "ENDSEC"]
        blocks = ["0", "SECTION", "2", "BLOCKS"]
        for sp in ("*MODEL_SPACE", "*PAPER_SPACE"):
            blocks += ["0", "BLOCK", "8", "0", "2", sp, "70", "0",
                       "10", "0", "20", "0", "30", "0", "3", sp, "1", "",
                       "0", "ENDBLK", "8", "0"]
        blocks += ["0", "ENDSEC"]
        entities = ["0", "SECTION", "2", "ENTITIES"] + self.e + ["0", "ENDSEC", "0", "EOF"]
        out = "\n".join(header + tables + blocks + entities)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(out)


# ------------------------- 每类块的图形 -------------------------
def draw_sym(d, bid):
    """以块内局部坐标绘制符号（标称 20x12 工作区，中心约 (10,6)）。"""
    if bid in ("INVERTER", "BESS_PCS"):
        d.rect(2, 2, 16, 8)
        d.text(10, 6, "PVS", 2.2)
        d.line(10, 10, 10, 12)  # 上出线
        d.line(10, 2, 10, 0)
    elif bid == "TRANSFORMER":
        d.circle(6.5, 6, 4.2)
        d.circle(13.5, 6, 4.2)
        d.line(6.5, 10.2, 6.5, 12)
        d.line(13.5, 10.2, 13.5, 12)
        d.line(6.5, 1.8, 6.5, 0)
        d.line(13.5, 1.8, 13.5, 0)
    elif bid == "MAIN_TRANSFORMER":
        d.circle(10, 6, 4.2)
        d.circle(10, 6, 2.2)
        d.line(10, 10.2, 10, 12)
        d.line(10, 1.8, 10, 0)
    elif bid == "MV_SWITCHGEAR":
        d.rect(1, 1, 18, 10)
        d.line(5, 1, 5, 11)
        d.line(15, 1, 15, 11)
        d.line(5, 6, 15, 6)
    elif bid == "POI":
        d.rect(4, 2, 12, 8)
        d.text(10, 6, "POI", 2.2)
        d.line(6, 2, 6, 0)
        d.line(14, 2, 14, 0)
    elif bid == "BESS_UNIT":
        # 电池组
        d.rect(3, 2, 14, 8)
        d.line(6, 6, 6, 9)      # +
        d.line(4.5, 7.5, 7.5, 7.5)
        d.line(9, 2.5, 9, 5.5)  # -
        d.line(11.5, 4, 11.5, 4)
        d.text(13, 5, "BESS", 1.6)
    elif bid == "RECLOSER":
        d.circle(10, 6, 4)
        d.line(10, 10, 10, 12)
        d.line(10, 2, 10, 0)
        d.line(7, 8, 13, 4)
    elif bid == "FUSE":
        d.rect(8, 4, 4, 4)
        d.line(10, 8, 10, 12)
        d.line(10, 4, 10, 0)
    elif bid == "DISCONNECT":
        d.circle(8, 6, 1.8)
        d.circle(12, 6, 1.8)
        d.line(8, 8, 12, 4)
        d.line(8, 4.2, 8, 0)
        d.line(12, 4.2, 12, 0)
        d.line(8, 7.8, 8, 12)
        d.line(12, 7.8, 12, 12)
    elif bid == "BUS":
        d.line(2, 6, 18, 6)
        d.line(2, 7.2, 18, 7.2)
        d.line(4, 6, 4, 12)
        d.line(16, 6, 16, 12)
        d.line(4, 6, 4, 0)
        d.line(16, 6, 16, 0)
    elif bid == "FEEDER":
        d.line(2, 6, 18, 6)
        d.line(13, 8.5, 18, 6)
        d.line(13, 3.5, 18, 6)
    elif bid == "GROUND":
        d.line(10, 12, 10, 5)
        d.line(6, 5, 14, 5)
        d.line(7, 3, 13, 3)
        d.line(8, 1, 12, 1)
    elif bid == "CT":
        d.circle(10, 6, 4)
        d.line(4, 6, 16, 6)
        d.line(10, 2, 10, 0)
    elif bid == "PT":
        d.circle(10, 6, 4)
        d.line(6, 6, 14, 6)
        d.line(10, 10, 10, 12)
        d.line(10, 2, 10, 0)
    elif bid == "ARRESTER":
        d.line(10, 12, 10, 5)
        d.line(6, 8, 14, 8)
        d.line(6, 5, 14, 5)
        d.line(7, 3, 13, 3)
        d.line(8, 1, 12, 1)
    elif bid == "METER":
        d.circle(10, 6, 4)
        d.text(10, 6, "M", 2.2)
        d.line(10, 2, 10, 0)
    else:
        d.rect(2, 2, 16, 8)
        d.text(10, 6, bid, 2.0)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="覆盖已存在的块文件(默认跳过，避免覆盖你的真实块)")
    force = ap.parse_args().force
    os.makedirs(OUTDIR, exist_ok=True)
    with open(MANIFEST, "r", encoding="utf-8") as f:
        rows = [r for r in csv.reader(f)
                if r and not r[0].strip().startswith("#")]

    done = []
    for r in rows:
        # 兼容含空格列: ID,BLOCK,FILE,PREFIX,X,Y,ROT,ATTRS
        if len(r) < 8:
            print("skip malformed:", r)
            continue
        bid, block, _file, prefix = r[0].strip(), r[1].strip(), r[2].strip(), r[3].strip()
        attrs = [a.strip() for a in r[7].split(";") if a.strip()]
        d = Dxf()
        draw_sym(d, bid)
        # 固定连接点放 CONN 层：四边中点(上/下/左/右)，CAD 里可再精确调整
        for (cx_, cy_) in [(10, 12), (10, 0), (0, 6), (20, 6)]:
            d.point(cx_, cy_, "CONN")
        # 属性放在符号下方，逐项向左排布；TAG 放最上
        ax = 2.0
        ay = -3.0
        d.attdef(ax + 4, ay, "TAG", "Tag (auto)", prefix + "000")
        rest = [a for a in attrs if a != "TAG"]
        for k, a in enumerate(rest):
            d.attdef(ax + 4 + k * 9, ay - 3.0, a, a, "")
        out = os.path.join(OUTDIR, block + ".dxf")
        if os.path.exists(out) and not force:
            print(f"  跳过(已存在，用 --force 覆盖): {block}.dxf")
            continue
        d.save(out, block)
        done.append((block, len(rest) + 1))

    print(f"生成 {len(done)} 个块模板到: {OUTDIR}")
    for b, na in done:
        print(f"  {b}.dxf  ({na} 个属性)")


if __name__ == "__main__":
    main()
