#!/usr/bin/env python3
"""
array_gen.py -- 光伏阵列 + 线束 自动生成（交接手册第 13 章 · 阶段①②）

一条命令排出一片组件阵列（串数 × 每串板数），串内按极性连线，
线束排在阵列正下方，每串正极引一根跨接线到对应那根正极支线；
整套内容按“可用区”等比缩放后放进外框图，输出可与 CAD 打开的 DXF。

用法:
  python array_gen.py --frame "templates/外框模板(EU) 09072026.dxf" \
      --module-first PV-POS --module-mid MIDDLE-PV --module-last END-NEG \
      --n-per 20 --n-strings 4 --gap-x 2 --gap-y 30 \
      --harness POS,POS,POS,FUSE --pos-feeder POS --neg-feeder NEG \
      --awg-main "2/0 AWG" --awg-branch "6 AWG"

输出: out/array_<时间戳>.dxf + .csv(线长清单) + .svg(预览)
"""

import argparse
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import wiring_raw as wr          # noqa: E402

OUTDIR = os.path.join(HERE, "out")
FRAMES_DIR = os.path.join(HERE, "templates")


def write_retry(path, text):
    """CAD 打开着文件会被锁：换个名字再写。"""
    cand, i = path, 0
    while True:
        try:
            with open(cand, "w", encoding="latin-1", newline="") as f:
                f.write(text)
            return cand
        except PermissionError:
            i += 1
            root, ext = os.path.splitext(path)
            cand = "%s_%d%s" % (root, i, ext)


def write_csv(path, wires):
    # 表头中英对照：中英文用户拿到的都是同一份文件，列数不变
    lines = ["#阵列+线束 线长清单 / Array+harness wire list（线上不写长度，写的是线号 / lengths are on the wire labels）",
             "序号 No.,范围 Range,线号AWG AWG,长度 Length"]
    for i, (what, awg, L) in enumerate(wires, 1):
        lines.append("%d,%s,%s,%.3f" % (i, what, awg, L))
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write("\n".join(lines) + "\n")


def write_csv_multi(path, wires):
    """拼图的线长清单：多一列“图号 / Sheet”，一张一张分开编序号。

    wires 每项 (图号, 范围, 线号, 长度)；没有图号的老三列写法也认。
    """
    lines = ["#拼图 线长清单 / Multi-sheet wire list（线上不写长度，写的是线号 / lengths are on the wire labels）",
             "图号 Sheet,序号 No.,范围 Range,线号AWG AWG,长度 Length"]
    seq = 0
    for row in wires:
        if len(row) >= 4:
            no, what, awg, L = row[0], row[1], row[2], row[3]
        else:
            no, what, awg, L = "", row[0], row[1], row[2]
        seq += 1
        lines.append("%s,%d,%s,%s,%.3f" % (no, seq, what, awg, L))
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write("\n".join(lines) + "\n")


def render_png(sec, path, width=1800):
    """不用 CAD 也能看图：底图灰，我们生成的线/标注分别用红/蓝。"""
    from PIL import Image, ImageDraw
    bmap = wr._blocks_map(sec)
    prims = []
    wr._prim_list(wr.group_entities(sec.get("ENTITIES", [])), bmap,
                  (1, 0, 0, 1, 0, 0), 0, prims)

    # 我们自己的实体（在当前 ENTITIES 里，不展平）单独上色
    ours = []
    for rec in wr.group_entities(sec.get("ENTITIES", [])):
        lay = (wr._g1(rec, "8") or "").upper()
        if lay == "WIRE":
            if rec[0][1] == "LINE":
                ours.append(("line", wr._gf(rec, "10"), wr._gf(rec, "20"),
                             wr._gf(rec, "11"), wr._gf(rec, "21"), (200, 30, 30)))
            elif rec[0][1] == "LWPOLYLINE":
                ours.append(("poly", [(v[0], v[1]) for v in wr._lw_verts(rec)],
                             (200, 30, 30)))
        elif lay == "WIRE_LABEL" and rec[0][1] == "TEXT":
            ours.append(("text", wr._gf(rec, "10"), wr._gf(rec, "20"),
                         wr._g1(rec, "1") or "", (20, 70, 200)))
    if not prims and not ours:
        return None
    xs, ys = [], []
    for p in prims + ours:
        if p[0] == "line":
            xs += [p[1], p[3]]; ys += [p[2], p[4]]
        elif p[0] == "circle":
            xs += [p[1] - p[3], p[1] + p[3]]; ys += [p[2] - p[3], p[2] + p[3]]
        elif p[0] == "poly":
            xs += [q[0] for q in p[1]]; ys += [q[1] for q in p[1]]
        elif p[0] == "text":
            xs.append(p[1]); ys.append(p[2])
    if not xs:
        return None
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    sc = (width - 40) / max(x1 - x0, 1e-6)
    hgt = int((y1 - y0) * sc) + 40
    img = Image.new("RGB", (width, hgt), "white")
    d = ImageDraw.Draw(img)

    def X(x):
        return 20 + (x - x0) * sc

    def Y(y):
        return hgt - 20 - (y - y0) * sc

    for p in prims + ours:
        if p[0] == "line":
            col = p[5]
        elif p[0] == "poly" and len(p) == 3 and isinstance(p[2], tuple):
            col = p[2]
        elif p[0] == "text" and len(p) == 5 and isinstance(p[4], tuple):
            col = p[4]
        else:
            col = (140, 140, 145)
        if p[0] == "line":
            d.line([X(p[1]), Y(p[2]), X(p[3]), Y(p[4])], fill=col, width=2)
        elif p[0] == "circle":
            d.ellipse([X(p[1] - p[3]), Y(p[2] + p[3]), X(p[1] + p[3]), Y(p[2] - p[3])],
                      outline=col, width=2)
        elif p[0] == "poly":
            pts = [(X(q[0]), Y(q[1])) for q in p[1]]
            if len(pts) > 1:
                d.line(pts, fill=col, width=2)
        elif p[0] == "text":
            d.text((X(p[1]), Y(p[2])), str(p[3])[:24], fill=col)
    img.save(path)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="光伏阵列 + 线束 自动生成")
    ap.add_argument("--frame", default="", help="外框图 DXF（templates/ 里选）")
    ap.add_argument("--module", default="",
                    help="整串用同一个组件块时的块名（老写法；新写法用 --module-first/--module-mid/--module-last）")
    ap.add_argument("--module-first", default="", help="每串第一块（带正极出线），配合 --module-mid/--module-last 用")
    ap.add_argument("--module-mid", default="", help="每串中间块（重复的那个）")
    ap.add_argument("--module-last", default="", help="每串最后一块（带负极出线）")
    ap.add_argument("--n-per", type=int, default=20, help="每串几块板")
    ap.add_argument("--n-strings", type=int, default=4, help="一共几串")
    ap.add_argument("--gap-x", type=float, default=2.0, help="板与板之间的净空（贴板就小）")
    ap.add_argument("--gap-y", type=float, default=2.0,
                    help="串与串之间的净空（中间可能要放电机，放就调大）")
    ap.add_argument("--bha", default="",
                    help="电机 / BHA 桩插在板与板之间（可多处，用 ; 隔开）。"
                         "一处写法：整排第几块之后:桩块:电机块:电机旋转:左净空:右净空。"
                         "位置按整个阵列连续数（不分串）：4 串 × 20 块时 40 = 正中间；"
                         "留空 = 最前面；写“每段” = 每段（每个支架）中点各一处。"
                         "例：--bha \"40:BHA:MOTOR:0\"  或  --bha \"20:BHA:MOTOR:90;60:BHA\"；"
                         "桩右边的板整体右移（位移 = 桩宽 + 左右净空 - 板间净空），阵列自动变长")
    ap.add_argument("--dir", default="right", choices=["right", "down"],
                    help="串的排法：right=从左往右接（默认），down=从上往下叠")
    ap.add_argument("--link-array", action="store_true",
                    help="画“阵列 ↔ 线束”的跨接线（默认不画：正极支线不接板子）")
    ap.add_argument("--harness-scale", type=float, default=1.0,
                    help="线束整体微调倍率（默认 1.0；支线按接点间距=板子出线点间距，其他块按对角线≈板子）")
    ap.add_argument("--harness", default="", help="线束块链，逗号分隔，可重复")
    ap.add_argument("--gap", type=float, default=40.0, help="线束内块间隔")
    ap.add_argument("--pos-feeder", default="POS", help="线束里哪一块是正极支线（一根对一串）")
    ap.add_argument("--neg-feeder", default="NEG", help="线束里哪一块是负极支线（自动补齐负极行时用）")
    ap.add_argument("--awg-main", default="",
                    help="主线线号（标在块与块之间的连线上），如 2/0 AWG；"
                         "界面上是从 750 MCM / 500 MCM / 2/0 AWG / 4 AWG / 6 AWG / 8 AWG / 10 AWG 里选")
    ap.add_argument("--awg-branch", default="",
                    help="支线线号（只进 CSV/日志，板与板之间不标字），如 10 AWG；"
                         "界面上是从 10 AWG / 12 AWG 里选")
    ap.add_argument("--allow-enlarge", action="store_true", help="允许放大到占满（默认只缩不放）")
    ap.add_argument("--keep-from", default="",
                    help="保留这个旧输出里手工画的实体（图层名以 HAND 开头）")
    ap.add_argument("--keep-prefix", default="HAND",
                    help="手工内容的图层名前缀（默认 HAND，即在 CAD 里画在 HAND_ 层上）")
    ap.add_argument("--out", default="", help="输出文件名（默认 out/array_<时间戳>.dxf）")
    ap.add_argument("--no-svg", action="store_true", help="不输出预览 SVG")
    ap.add_argument("--preview-content", action="store_true",
                    help="额外出一张“只看生成内容”的 PNG（去掉外框自己的杂线）")
    a = ap.parse_args(argv)

    frame = a.frame
    if not frame:
        cands = [f for f in sorted(os.listdir(FRAMES_DIR))
                 if f.lower().endswith(".dxf")] if os.path.isdir(FRAMES_DIR) else []
        if not cands:
            print("templates/ 里没有 .dxf 外框图"); return 2
        frame = os.path.join(FRAMES_DIR, cands[0])
        print("没给 --frame，用: " + cands[0])
    elif not os.path.isabs(frame) and not os.path.exists(frame):
        frame = os.path.join(FRAMES_DIR, frame)

    harness = [s for s in (a.harness or "").replace("，", ",").split(",") if s.strip()]
    keep_from = a.keep_from
    if keep_from and not os.path.isabs(keep_from) and not os.path.exists(keep_from):
        keep_from = os.path.join(OUTDIR, keep_from)       # 只给文件名就去 out/ 里找
    spec = dict(module=a.module, module_first=a.module_first,
                module_mid=a.module_mid, module_last=a.module_last,
                n_per=a.n_per, n_strings=a.n_strings,
                gap_x=a.gap_x, gap_y=a.gap_y, dir=a.dir, link_array=a.link_array,
                harness_scale=a.harness_scale,
                bha=a.bha,
                harness=harness, gap=a.gap, pos_feeder=a.pos_feeder,
                neg_feeder=a.neg_feeder, awg_main=a.awg_main,
                awg_branch=a.awg_branch, allow_enlarge=a.allow_enlarge,
                keep_from=keep_from, keep_prefix=a.keep_prefix)

    text, log, wires = wr.build_array_frame(frame, spec)
    for line in log:
        print("  " + line.strip())
    if not text:
        print("生成失败"); return 1

    os.makedirs(OUTDIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.splitext(os.path.basename(a.out))[0] if a.out else ("array_%s" % ts)
    outpath = write_retry(os.path.join(OUTDIR, base + ".dxf"), text)
    print("DXF: " + outpath)

    csvpath = os.path.splitext(outpath)[0] + ".csv"
    write_csv(csvpath, wires)
    print("线长清单: " + csvpath)

    if not a.no_svg:
        try:
            sec, _o = wr.parse_sections(outpath, "utf-8")
            svg = wr.entities_to_svg(wr.group_entities(sec.get("ENTITIES", [])),
                                     wr._blocks_map(sec), width=1600)
            svgpath = os.path.splitext(outpath)[0] + ".svg"
            with open(svgpath, "w", encoding="utf-8") as f:
                f.write(svg)
            print("预览: " + svgpath)
        except Exception as ex:
            print("预览失败: %s" % ex)
        try:
            sec, _o = wr.parse_sections(outpath, "utf-8")
            pngpath = os.path.splitext(outpath)[0] + ".png"
            if render_png(sec, pngpath):
                print("预览图: " + pngpath)
        except Exception as ex:
            print("预览图失败: %s" % ex)
        if a.preview_content:
            try:
                sec, _o = wr.parse_sections(outpath, "utf-8")
                keep_names = set([a.module, a.module_first, a.module_mid, a.module_last]
                                 + harness
                                 + wr.bha_block_names(a.bha, a.n_strings, a.n_per))   # BHA 桩/电机
                keep = [e for e in wr.group_entities(sec.get("ENTITIES", []))
                        if wr._g1(e, "2") in keep_names
                        or (wr._g1(e, "8") or "").upper() in ("WIRE", "WIRE_LABEL")]
                sec2 = {"ENTITIES": [c for e in keep for c in e], "BLOCKS": sec["BLOCKS"]}
                cp = os.path.splitext(outpath)[0] + "_content.png"
                if render_png(sec2, cp, width=1700):
                    print("内容预览: " + cp)
            except Exception as ex:
                print("内容预览失败: %s" % ex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
