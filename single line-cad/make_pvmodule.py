#!/usr/bin/env python3
"""
make_pvmodule.py -- 生成 PVMODULE(光伏组件) 块 -> blocklib/blocks/PVMODULE.dxf

符号：组件板面 + 电池片格线 + 底部 +/- 接线端；
接线端接点分别放在 CONN_POS / CONN_NEG 层（便于按极性配对）。
零第三方依赖。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_blocks import Dxf

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "blocklib", "blocks", "PVMODULE.dxf")

# 0=白 3=绿 4=青 1=红 5=蓝 7=白
LAYERS = [("0", 7), ("EQUIP", 3), ("TEXT", 7), ("CONN", 4),
          ("CONN_POS", 1), ("CONN_NEG", 5)]


def save(d, path):
    header = ["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009",
              "9", "$EXTMIN", "10", "-5", "20", "-12", "30", "0",
              "9", "$EXTMAX", "10", "45", "20", "30", "30", "0", "0", "ENDSEC"]
    tables = ["0", "SECTION", "2", "TABLES",
              "0", "TABLE", "2", "LTYPE", "70", "1",
              "0", "LTYPE", "2", "CONTINUOUS", "70", "0", "3", "Solid line",
              "72", "65", "73", "0", "40", "0.0", "0", "ENDTAB",
              "0", "TABLE", "2", "LAYER", "70", str(len(LAYERS))]
    for name, color in LAYERS:
        tables += ["0", "LAYER", "2", name, "70", "0", "62", str(color),
                   "6", "CONTINUOUS"]
    tables += ["0", "ENDTAB",
               "0", "TABLE", "2", "STYLE", "70", "1",
               "0", "STYLE", "2", "STANDARD", "70", "0", "40", "0.0",
               "41", "1.0", "50", "0.0", "71", "0", "42", "0.2",
               "3", "txt", "4", "", "0", "ENDTAB", "0", "ENDSEC"]
    blocks = ["0", "SECTION", "2", "BLOCKS"]
    for sp in ("*MODEL_SPACE", "*PAPER_SPACE"):
        blocks += ["0", "BLOCK", "8", "0", "2", sp, "70", "0",
                   "10", "0", "20", "0", "30", "0", "3", sp, "1", "",
                   "0", "ENDBLK", "8", "0"]
    blocks += ["0", "ENDSEC"]
    entities = ["0", "SECTION", "2", "ENTITIES"] + d.e + ["0", "ENDSEC", "0", "EOF"]
    out = "\n".join(header + tables + blocks + entities)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(out)


def main():
    d = Dxf()
    # 组件板面
    d.rect(2, 26, 36, 22, "EQUIP")
    # 电池片格线（竖 5 条 + 横 1 条）
    for k in range(1, 6):
        x = 2 + k * 6
        d.line(x, 26, x, 4, "EQUIP")
    d.line(2, 15, 38, 15, "EQUIP")
    # 底部 +/- 接线端引线
    d.line(14, 4, 14, 0, "EQUIP")     # 正极
    d.line(26, 4, 26, 0, "EQUIP")     # 负极
    d.text(14, -3, "+", 3.0, "TEXT")
    d.text(26, -3, "-", 3.0, "TEXT")
    # 属性
    d.attdef(20, -7, "TAG", "Tag", "PV-000")
    d.attdef(20, -10, "RATING", "Rating", "")
    # 连接点：正/负极各一个，放不同层
    d.point(14, 0, "CONN_POS")
    d.point(26, 0, "CONN_NEG")
    save(d, OUT)
    print("saved:", OUT, "| 实体:", len(d.e))


if __name__ == "__main__":
    main()
