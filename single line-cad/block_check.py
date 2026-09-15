#!/usr/bin/env python3
"""
block_check.py -- 块库体检：尺寸 + 接点，看看各块是不是同一个量级

为什么需要它：连线生成器要"让块与块之间的线水平"，前提是各块的接点间距一致。
块库里的块如果各画各的尺寸，程序就只能猜（现在就是靠对角线/几何平均在猜），
猜出来总有一头不对——要么支线大得离谱，要么 FUSE 小到看不见。

约定（画块时照这个来）：
  1. 接点用 POINT 标在 CONN / CONN_POS / CONN_NEG 层上；
  2. 每个"串在链上的"块：左边 2 个接点、右边 2 个接点，
     同一侧两个接点的**纵向间距 = 节距 P**（大家一样），接点落在块的左右边界上；
  3. 组件块（板子）：CONN_POS / CONN_NEG 两个出线点，**横向间距也 = P**；
  4. 外形只要彼此协调即可（本脚本会给出相对板子的尺寸，方便你判断）。

用法:
  python block_check.py                    # 体检 blocklib/blocks 下所有块
  python block_check.py --ref PV-POS       # 指定哪个块当基准（默认 PV-POS）
  python block_check.py --pitch 3.0        # 手工指定节距 P（默认取基准块的出线点间距）
"""

import argparse
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import wiring_raw as wr          # noqa: E402
import blockui_server as ui      # noqa: E402


def read_block(path):
    """返回 (尺寸, 接点列表, 展平图元数)。接点 = [(层, x, y), ...]。"""
    sec, _o = wr.parse_sections_text(wr.read_dxf_text(path))
    bmap = wr._blocks_map(sec)
    recs = wr.group_entities(sec.get("ENTITIES", []))
    prims = []
    wr._prim_list(recs, bmap, (1, 0, 0, 1, 0, 0), 0, prims)
    bb = wr._prims_bbox(prims)
    if bb is None:
        return None, [], len(prims)
    pts = []

    def collect(ent_recs):
        for r in ent_recs:
            if r[0][1] == "POINT":
                lay = (wr._g1(r, "8") or "").upper()
                if lay.startswith("CONN"):
                    pts.append((lay, wr._gf(r, "10"), wr._gf(r, "20")))

    collect(recs)
    for nm, mem in bmap.items():
        collect(mem)
    # 去重
    seen, uniq = set(), []
    for p in pts:
        k = (p[0], round(p[1], 4), round(p[2], 4))
        if k not in seen:
            seen.add(k); uniq.append(p)
    return (bb[0], bb[1], bb[2], bb[3]), uniq, len(prims)


def side_spans(pts):
    """把接点按 x 归成“竖列”，返回 (最左列纵向间距, 最右列纵向间距, 列间横向距离)。

    用“x 相近归为一列”而不是“按中位数左右切”：像 POS 这种块，顶上还有一个
    CONNPOS 点在 x=0，按中位数切会把它算进左组，间距就变成整块高度（84.91）了。
    """
    if not pts:
        return None, None, None
    cols = []
    for p in sorted(pts, key=lambda q: q[1]):
        if cols and abs(p[1] - cols[-1][0][1]) <= max(0.5, abs(p[1]) * 0.02):
            cols[-1].append(p)
        else:
            cols.append([p])
    if len(cols) == 1:                       # 所有点在同一竖线上（比如公头）
        ys = [q[2] for q in cols[0]]
        return None, (max(ys) - min(ys)) if len(ys) >= 2 else None, None
    L = sorted(cols[0], key=lambda p: -p[2])
    R = sorted(cols[-1], key=lambda p: -p[2])
    ls = (L[0][2] - L[-1][2]) if len(L) >= 2 else None
    rs = (R[0][2] - R[-1][2]) if len(R) >= 2 else None
    lr = abs(cols[-1][0][1] - cols[0][0][1])
    return ls, rs, lr


def ref_pitch(path):
    """基准块的节距：优先用它 CONN 点的横向间距；没有成对点就用兜底端子（板子的 +/-）。"""
    sec, _o = wr.parse_sections_text(wr.read_dxf_text(path))
    bmap = wr._blocks_map(sec)
    recs = wr.group_entities(sec.get("ENTITIES", []))
    prims = []
    wr._prim_list(recs, bmap, (1, 0, 0, 1, 0, 0), 0, prims)
    pos, neg, _fb = wr._terminal_pair(recs, prims, bmap, "ref", None)
    if pos and neg:
        return abs(pos[0] - neg[0]), ("兜底：底边左1/3 与 右2/3" if _fb else "CONN_POS/CONN_NEG")
    return 0.0, "取不到"


def main(argv=None):
    ap = argparse.ArgumentParser(description="块库体检")
    ap.add_argument("--ref", default="PV-POS", help="基准块（默认 PV-POS）")
    ap.add_argument("--pitch", type=float, default=0.0, help="手工指定节距 P")
    ap.add_argument("--tol", type=float, default=0.02, help="判定容差（相对值，默认 2%）")
    a = ap.parse_args(argv)

    d = ui.BLOCKS_DIR
    files = sorted(f for f in os.listdir(d) if f.lower().endswith(".dxf"))
    if not files:
        print("blocklib/blocks 下没有 .dxf"); return 1

    data = {}
    for f in files:
        nm = os.path.splitext(f)[0]
        bb, pts, npr = read_block(os.path.join(d, f))
        data[nm] = (bb, pts, npr)

    if a.ref not in data:
        print("基准块 %s 不在块库里" % a.ref); return 1
    rbb, rpts, _ = data[a.ref]
    rw, rh = rbb[1] - rbb[0], rbb[3] - rbb[2]
    rdiag = math.hypot(rw, rh)
    # 基准块的节距：优先用它的“左右出线点横向间距”（板子就是正负极两个点）
    _, _, lr = side_spans(rpts)
    auto_p, how = ref_pitch(os.path.join(d, a.ref + ".dxf"))
    pitch = a.pitch or auto_p
    print("基准块 %s: %.2f x %.2f（对角线 %.2f）" % (a.ref, rw, rh, rdiag))
    print("节距 P = %.3f  %s" % (pitch, "(手工指定)" if a.pitch else "(基准块的出线点间距：%s)" % how))
    print()
    print("%-12s %10s %8s %8s %10s %10s  %s" %
          ("块", "尺寸", "对角线", "相对板", "左接点距", "右接点距", "判定"))
    print("-" * 88)
    bad = []
    for nm in sorted(data):
        bb, pts, npr = data[nm]
        if bb is None:
            print("%-12s  (没有几何)" % nm); continue
        w, h = bb[1] - bb[0], bb[3] - bb[2]
        diag = math.hypot(w, h)
        ls, rs, lr2 = side_spans(pts)
        rel = diag / rdiag if rdiag else 0
        notes = []
        if not pts:
            notes.append("没有 CONN 点")
        elif len(pts) == 1:
            notes.append("只有 1 个 CONN 点（组件块应标 CONN_POS + CONN_NEG 两个）")
        elif ls is None and rs is None:
            # 只有两个并排的出线点（像板子）：查这两点的横向间距
            if pitch and abs(lr2 - pitch) > pitch * a.tol:
                notes.append("两点间距 %.2f≠P" % lr2)
        elif ls is None or rs is None:
            notes.append("左/右接点不成对")
        else:
            for tag, v in (("左", ls), ("右", rs)):
                if pitch and abs(v - pitch) > pitch * a.tol:
                    notes.append("%s接点间距 %.2f≠P" % (tag, v))
        warn = ""
        if rel and not (0.3 <= rel <= 3.0):
            warn = "  ⚠ 相对板子 %.1f 倍" % rel
        verdict = ("OK" if not notes else "✗ " + "；".join(notes)) + warn
        if notes:
            bad.append(nm)
        print("%-12s %4.1f x %-5.1f %8.2f %8.2fx %10s %10s  %s" %
              (nm, w, h, diag, rel,
               ("%.2f" % ls) if ls is not None else "-",
               ("%.2f" % rs) if rs is not None else "-", verdict))
    print()
    if bad:
        print("要改的块 (%d): %s" % (len(bad), ", ".join(bad)))
    else:
        print("全部合格：接点间距一致、尺寸在同一个量级。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
