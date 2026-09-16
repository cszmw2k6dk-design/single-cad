#!/usr/bin/env python3
"""
wiring_raw.py -- 连线生成器【不展平】：把块里的实体原样搬运(仅整体平移)，保留所有实体类型。

原理：
  - 以第一个块文件作为“容器”(host)，保留它的 HEADER/TABLES/BLOCKS(含嵌套块/图层定义)。
  - 每个实例：把该块文件 model-space 的实体【原样复制】，坐标整体平移到自己插入点；
    坐标码 10/20(及 11-14/21-24，排除 ELLIPSE/MTEXT 的方向向量)加偏移。
  - 复制时去掉 handle(5) 与 owner(330)，避免句柄冲突(AutoCAD 导入时重新分配)。
  - 其它块的图层/嵌套块定义按需合并进 host。
  - 最后追加我们的连线(WIRE)与线长标注(TEXT)。

这样 SPLINE/ELLIPSE/HATCH 等全部原样保留，画面与块库一致。
"""

import os
import sys
import math
import re
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blockui_server as ui
import connect_library as cl
import blockpack as bp


def _num(v, d):
    try:
        return "%.6f" % (float(v) + d)
    except (TypeError, ValueError):
        return v


def parse_sections_text(txt):
    """把 DXF 文本切成 ({section_name: [(code, value), ...]}, [顺序])。"""
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")
    lines = txt.split("\n")
    pairs = []
    i = 0
    while i + 1 < len(lines):
        pairs.append((lines[i].strip(), lines[i + 1].strip()))
        i += 2
    sections = {}
    order = []
    j = 0
    while j < len(pairs):
        if pairs[j] == ("0", "SECTION"):
            name = pairs[j + 1][1] if j + 1 < len(pairs) and pairs[j + 1][0] == "2" else None
            body = []
            k = j + 2
            while k < len(pairs) and pairs[k] != ("0", "ENDSEC"):
                body.append(pairs[k])
                k += 1
            if name:
                sections[name] = body
                order.append(name)
            j = k + 1
        else:
            j += 1
    return sections, order


def parse_sections_bytes(data, enc="latin-1"):
    """解析内存里的 DXF 字节（配合 blockpack 的定点插入）。"""
    return parse_sections_text(data.decode(enc, errors="replace"))


def parse_sections(path, enc="latin-1"):
    """返回 ({section_name: [ (code, value), ... ]}, [顺序])。"""
    # 用 latin-1 读(字节透明)：保留 ANSI_936(GBK) 等原字节，避免中文被损坏
    txt = open(path, encoding=enc, errors="replace").read()
    return parse_sections_text(txt)


def read_dxf_text(path):
    """读 DXF 文本：文件常见 ANSI_936(GBK)，也有 UTF-8；都不行就按 latin-1 字节透明读。

    预览要显示块里的中文时用它——用错编码中文会变成乱码。
    """
    data = open(path, "rb").read()
    for enc in ("gbk", "utf-8"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def max_handle(sections):
    mx = 0
    for body in sections.values():
        for c, v in body:
            if c == "5":
                try:
                    mx = max(mx, int(v, 16))
                except (TypeError, ValueError):
                    pass
    return mx


def model_space_handle(sections):
    t = sections.get("TABLES", [])
    i = 0
    while i < len(t):
        if t[i] == ("0", "BLOCK_RECORD"):
            j = i + 1; name = None; hnd = None
            while j < len(t) and t[j][0] != "0":
                if t[j][0] == "2" and name is None:
                    name = t[j][1]
                if t[j][0] == "5" and hnd is None:
                    hnd = t[j][1]
                j += 1
            if name and name.upper() == "*MODEL_SPACE":
                return hnd
            i = j
        else:
            i += 1
    return None


def clone_entity(ent, dx, dy, handle, owner):
    """复制实体：分配新句柄、合并 owner，并平移坐标(不删句柄)。"""
    etype = ent[0][1]
    out = []
    has5 = has330 = False
    skip102 = False
    for c, v in ent:
        # 去掉 102 {ACAD_XDICTIONARY / ACAD_REACTORS ... } 组：跨文件后引用会悬空
        if c == "102":
            if str(v).strip() in ("{ACAD_XDICTIONARY", "{ACAD_REACTORS"):
                skip102 = True
                continue
            if str(v).strip() == "}":
                skip102 = False
                continue
        if skip102:
            continue
        if c == "5":
            out.append((c, handle)); has5 = True; continue
        if c == "330":
            out.append((c, owner if owner else v)); has330 = True; continue
        if c == "10":
            v = _num(v, dx)
        elif c == "20":
            v = _num(v, dy)
        elif c in ("11", "12", "13", "14"):
            if not (etype in _NO_OFF and c in _NO_OFF[etype]):
                v = _num(v, dx)
        elif c in ("21", "22", "23", "24"):
            if not (etype in _NO_OFF and c in _NO_OFF[etype]):
                v = _num(v, dy)
        out.append((c, v))
    if not has5:
        out.insert(1, ("5", handle))
    if not has330 and owner:
        out.insert(2, ("330", owner))
    return out


def update_handseed(header, h):
    out = []
    i = 0
    while i < len(header):
        if header[i] == ("9", "$HANDSEED"):
            out.append(header[i]); out.append(("5", "%X" % h)); i += 2; continue
        out.append(header[i]); i += 1
    return out


def emit_section(name, body):
    parts = ["0", "SECTION", "2", name]
    for it in body:
        if it and isinstance(it[0], str):
            parts += [it[0], it[1]]
        else:
            for c, v in it:
                parts += [c, v]
    parts += ["0", "ENDSEC"]
    return "\n".join(parts) + "\n"


def add_layers(tables, desired, have, next_handle):
    """把需要的图层(desired=[(name,color)])并入 host 的 LAYER 表(不重复)。"""
    add = []
    for nm, color in desired:
        if nm and nm not in have:
            have.add(nm)
            add.append([("0", "LAYER"), ("5", next_handle()), ("330", "0"),
                        ("100", "AcDbSymbolTableRecord"), ("100", "AcDbLayerTableRecord"),
                        ("2", nm), ("70", "0"), ("62", str(color)), ("6", "CONTINUOUS")])
    if not add:
        return tables
    out = list(tables)
    for i in range(len(out) - 1):
        if out[i] == ("0", "TABLE") and out[i + 1] == ("2", "LAYER"):
            k = i + 2
            cnt_idx = None; endtab = None
            while k < len(out):
                if out[k][0] == "70" and cnt_idx is None:
                    cnt_idx = k
                if out[k] == ("0", "ENDTAB"):
                    endtab = k; break
                k += 1
            if cnt_idx is not None:
                try:
                    out[cnt_idx] = ("70", str(int(out[cnt_idx][1]) + len(add)))
                except (TypeError, ValueError):
                    pass
            if endtab is not None:
                ins = []
                for rec in add:
                    ins += rec
                out[endtab:endtab] = ins
            break
    return out


def group_entities(pairs):
    """把一串 (code,value) 按 0 分组为实体。"""
    ents = []
    cur = None
    for c, v in pairs:
        if c == "0":
            if cur is not None:
                ents.append(cur)
            cur = [(c, v)]
        else:
            if cur is None:
                cur = [(c, v)]
            else:
                cur.append((c, v))
    if cur is not None:
        ents.append(cur)
    return ents


_NO_OFF = {"ELLIPSE": {"11", "21"}, "MTEXT": {"11", "21"}}  # 这些是方向向量，不平移


def translate_entity(ent, dx, dy):
    etype = ent[0][1]
    out = []
    for c, v in ent:
        if c in ("5", "330"):          # 去掉句柄/所有者，避免冲突
            continue
        if c == "10":
            v = _num(v, dx)
        elif c == "20":
            v = _num(v, dy)
        elif c in ("11", "12", "13", "14"):
            if not (etype in _NO_OFF and c in _NO_OFF[etype]):
                v = _num(v, dx)
        elif c in ("21", "22", "23", "24"):
            if not (etype in _NO_OFF and c in _NO_OFF[etype]):
                v = _num(v, dy)
        out.append((c, v))
    return out


def count_types(ents):
    from collections import Counter
    return Counter(e[0][1] for e in ents if e and e[0][0] == "0")


def layer_records(tables):
    """从 TABLES 段里取所有 LAYER 记录。"""
    recs = []
    i = 0
    while i < len(tables):
        if tables[i] == ("0", "LAYER"):
            cur = [(tables[i][0], tables[i][1])]
            i += 1
            while i < len(tables) and tables[i][0] != "0":
                cur.append(tables[i]); i += 1
            recs.append(cur)
        else:
            i += 1
    return recs


def layer_name(rec):
    for c, v in rec:
        if c == "2":
            return v
    return None


def _tbl_handle(tables, table_name):
    for i in range(len(tables) - 1):
        if tables[i] == ("0", "TABLE") and tables[i + 1] == ("2", table_name):
            k = i + 2
            while k < len(tables) and tables[k][0] != "0":
                if tables[k][0] == "5":
                    return tables[k][1]
                k += 1
    return None


def _br_names(tables):
    names = set(); i = 0
    while i < len(tables):
        if tables[i] == ("0", "BLOCK_RECORD"):
            j = i + 1; nm = None
            while j < len(tables) and tables[j][0] != "0":
                if tables[j][0] == "2" and nm is None:
                    nm = tables[j][1]
                j += 1
            if nm:
                names.add(nm)
            i = j
        else:
            i += 1
    return names


def _insert_into_table(tables, name, entry):
    out = list(tables)
    for i in range(len(out) - 1):
        if out[i] == ("0", "TABLE") and out[i + 1] == ("2", name):
            k = i + 2; cnt_idx = None; endtab = None
            while k < len(out):
                if out[k][0] == "70" and cnt_idx is None:
                    cnt_idx = k
                if out[k] == ("0", "ENDTAB"):
                    endtab = k; break
                k += 1
            if cnt_idx is not None:
                try:
                    out[cnt_idx] = ("70", str(int(out[cnt_idx][1]) + 1))
                except (TypeError, ValueError):
                    pass
            if endtab is not None:
                out[endtab:endtab] = entry
            break
    return out


def merge_blocks(blocks_body, tables_body, other_paths, next_handle, mspace, log):
    """把其它文件的块定义(BLOCK + BLOCK_RECORD)合并进 host，供 INSERT 解析。"""
    have = _br_names(tables_body)
    brt = _tbl_handle(tables_body, "BLOCK_RECORD")
    added = 0
    for path in other_paths:
        osec, _o = parse_sections(path)
        ob = group_entities(osec.get("BLOCKS", []))
        i = 0
        while i < len(ob):
            if ob[i][0][1] == "BLOCK":
                name = _g1(ob[i], "2")
                inner = []; j = i + 1
                while j < len(ob) and ob[j][0][1] != "ENDBLK":
                    inner.append(ob[j]); j += 1
                endblk = ob[j] if j < len(ob) and ob[j][0][1] == "ENDBLK" else None
                if name and not name.startswith("*") and name not in have:
                    have.add(name)
                    brh = next_handle()
                    entry = [("0", "BLOCK_RECORD"), ("5", brh), ("330", brt or "0"),
                             ("100", "AcDbSymbolTableRecord"), ("100", "AcDbBlockTableRecord"),
                             ("2", name)]
                    tables_body = _insert_into_table(tables_body, "BLOCK_RECORD", entry)
                    blocks_body = blocks_body + clone_entity(ob[i], 0, 0, next_handle(), mspace or "0")
                    for e in inner:
                        blocks_body = blocks_body + clone_entity(e, 0, 0, next_handle(), brh)
                    if endblk is not None:
                        blocks_body = blocks_body + clone_entity(endblk, 0, 0, next_handle(), mspace or "0")
                    added += 1
                i = j + 1 if endblk is not None else j
            else:
                i += 1
    if added:
        log.append("合并子块定义 %d 个" % added)
    return blocks_body, tables_body


def _prims_bbox(prims):
    xs = []; ys = []
    for p in prims:
        if p[0] == "poly":
            for x, y in p[1]:
                xs.append(x); ys.append(y)
        elif p[0] == "circle":
            xs += [p[1] - p[3], p[1] + p[3]]; ys += [p[2] - p[3], p[2] + p[3]]
        elif p[0] == "text":
            xs.append(p[1]); ys.append(p[2])
    return (min(xs), max(xs), min(ys), max(ys)) if xs else None


def _records_bbox(records, blocks):
    pr = []
    _prim_list(records, blocks, (1, 0, 0, 1, 0, 0), 0, pr)
    return _prims_bbox(pr)


def frame_draw_rect(sec, min_ratio=0.05):
    """外框图的“画图区”矩形 = **内框**（纸边往里那一圈），再扣掉标题栏那一条。

    以前取的是面积最大的闭合矩形，那是**纸边/外框**：内容会画到内框外面，
    甚至压到标题栏上（用户要的是“绘图区域不要超过里面的内框”）。现在的口径：
      1. 面积最大的闭合矩形 = 外框（纸边）；
      2. 它里面、面积 ≥ 外框 60% 的最大闭合矩形 = 内框（往里那一圈）；
      3. 内框里“从上到下贯通”的竖线 = 标题栏左边线；“从左到右贯通”的横线 =
         标题栏上边线。有就把标题栏那一条切掉，只留真正的绘图区。
    找不到闭合矩形时返回 None。
    """
    bmap = _blocks_map(sec)
    prims = []
    _prim_list(group_entities(sec.get("ENTITIES", [])), bmap,
               (1, 0, 0, 1, 0, 0), 0, prims)
    allbox = _prims_bbox(prims)
    rects = []
    for p in prims:
        if p[0] != "poly" or len(p) < 3 or not p[2] or len(p[1]) < 4:
            continue
        xs = [q[0] for q in p[1]]
        ys = [q[1] for q in p[1]]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        if w <= 1e-6 or h <= 1e-6:
            continue
        if allbox:
            if w * h < (allbox[1] - allbox[0]) * (allbox[3] - allbox[2]) * min_ratio:
                continue
        rects.append((w * h, (min(xs), max(xs), min(ys), max(ys))))
    if not rects:
        return None
    rects.sort(reverse=True)
    outer = rects[0][1]
    inner = None
    for area, bb in rects[1:]:
        if area < rects[0][0] * 0.6:
            break
        if (bb[0] >= outer[0] - 1e-6 and bb[1] <= outer[1] + 1e-6
                and bb[2] >= outer[2] - 1e-6 and bb[3] <= outer[3] + 1e-6):
            inner = bb
            break
    x0, x1, y0, y1 = inner or outer
    w, h = x1 - x0, y1 - y0
    # 标题栏：内框里贯通整高/整宽的边线（取最靠里的那条，宁可留多点白）
    cut_r = cut_t = None
    for p in prims:
        if p[0] != "poly" or len(p) < 2:
            continue
        v = p[1]
        ring = v + ([v[0]] if (len(p) > 2 and p[2]) else [])
        for a, b in zip(v, ring[1:]):
            ax, ay = a[0], a[1]
            bx, by = b[0], b[1]
            if abs(ax - bx) < 1e-6:                    # 竖线
                ya, yb = min(ay, by), max(ay, by)
                if ((yb - ya) >= h * 0.8 and ya >= y0 - 1.0 and yb <= y1 + 1.0
                        and x0 + w * 0.3 < ax < x1 - 1e-6):
                    cut_r = ax if cut_r is None else min(cut_r, ax)
            elif abs(ay - by) < 1e-6:                  # 横线
                xa, xb = min(ax, bx), max(ax, bx)
                if ((xb - xa) >= w * 0.8 and xa >= x0 - 1.0 and xb <= x1 + 1.0
                        and y0 + h * 0.3 < ay < y1 - 1e-6):
                    cut_t = ay if cut_t is None else max(cut_t, ay)
    if cut_r is not None:
        x1 = cut_r
    if cut_t is not None:
        y0 = cut_t
    return (x0, x1, y0, y1)


# ================= 线号标注：CAD 原生线性标注（DIMENSION） =================
# 用户要求：标注要用 CAD 原生的“线性标注”，标注点取块里 CONN-Label 层的点。
# 原生标注 = DIMENSION 实体 + 它引用的（缓存）块 —— 块里装尺寸界线/尺寸线/箭头/文字。
# 这里完全照外框模板里 ZWCAD 自己写出来的那份结构生成（模板里的 *D14 就是这个格式）：
#   DIMENSION：10=尺寸线位置 11=文字中点(= CONN-Label 点) 13/14=两条界线原点
#              42=实测长度 1=文字覆盖 3=标注样式 50=旋转角 70=128
#              末尾带 ACAD/DSTYLE xdata：140=文字高 41=箭头大小（这样在 CAD 里
#              “标注更新/拉伸”之后还是同一副样子）
#   缓存块  ：2×尺寸界线 + 1×尺寸线 + 2×箭头(SOLID) + 1×MTEXT + 3×DEFPOINTS 点

# 标注样式优先级：ISO-25 是外框模板里 ZWCAD 自己那批标注在用的样式（值也正常）；
# SLDDIMSTYLE2 那套的值是按别的比例做的（DIMTXT=700），放最后。
_DIM_STYLE_PREF = ("ISO-25", "Voltage", "SLDDIMSTYLE2", "Standard")


def dim_style_name(sec):
    """外框里已有的标注样式名，优先项目自己的；一个都没有就返回空串。"""
    names = []
    tb = sec.get("TABLES", [])
    i = 0
    while i < len(tb):
        if tb[i] == ("0", "DIMSTYLE"):
            j, nm = i + 1, ""
            while j < len(tb) and tb[j][0] != "0":
                if tb[j][0] == "2" and not nm:
                    nm = tb[j][1]
                j += 1
            if nm:
                names.append(nm)
            i = j
        else:
            i += 1
    for p in _DIM_STYLE_PREF:
        if p in names:
            return p
    return names[0] if names else ""


def _dim_line(p1, p2, layer="0"):
    # 记录里必须带 owner(330)：没有的话并进外框后就是“没有属主的实体”，CAD 会判文件无效。
    # pack 会把它改写成新块记录的句柄。
    return [("0", "LINE"), ("330", "0"), ("100", "AcDbEntity"), ("8", layer),
            ("100", "AcDbLine"),
            ("10", "%.6f" % p1[0]), ("20", "%.6f" % p1[1]), ("30", "0.0"),
            ("11", "%.6f" % p2[0]), ("21", "%.6f" % p2[1]), ("31", "0.0")]


def _dim_solid(tip, base1, base2, layer="0"):
    """箭头：尖在 tip，底边是 base1-base2（照模板用 SOLID）。"""
    return [("0", "SOLID"), ("330", "0"), ("100", "AcDbEntity"), ("8", layer),
            ("100", "AcDbTrace"),
            ("10", "%.6f" % base1[0]), ("20", "%.6f" % base1[1]), ("30", "0.0"),
            ("11", "%.6f" % base2[0]), ("21", "%.6f" % base2[1]), ("30", "0.0"),
            ("12", "%.6f" % tip[0]), ("22", "%.6f" % tip[1]), ("32", "0.0"),
            ("13", "%.6f" % tip[0]), ("23", "%.6f" % tip[1]), ("33", "0.0")]


def _dim_mtext(pos, txt, h, ang, layer="0"):
    ca, sa = math.cos(ang), math.sin(ang)
    return [("0", "MTEXT"), ("330", "0"), ("100", "AcDbEntity"), ("8", layer),
            ("100", "AcDbMText"),
            ("10", "%.6f" % pos[0]), ("20", "%.6f" % pos[1]), ("30", "0.0"),
            ("40", "%.4f" % h), ("41", "0.0"), ("46", "0.0"),
            ("71", "5"), ("72", "1"), ("1", txt),
            ("11", "%.6f" % ca), ("21", "%.6f" % sa), ("31", "0.0"),
            ("73", "1"), ("44", "1.0")]


def _dim_points(pts, layer="DEFPOINTS"):
    out = []
    for x, y in pts:
        out += [("0", "POINT"), ("330", "0"), ("100", "AcDbEntity"), ("8", layer),
                ("100", "AcDbPoint"), ("10", "%.6f" % x), ("20", "%.6f" % y), ("30", "0.0")]
    return out


def dim_geom(a, b, anchor, txt, th):
    """一条“对齐线性标注”的图元（块内容，用图纸坐标）。

    a / b   = 这条线上两个接点（尺寸界线的原点）
    anchor  = 块里 CONN-Label 层的点（尺寸线过它，文字压在它旁边）
    返回 (实体对列表, 几何)，线段长度为 0 时返回 (None, None)。
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return None, None
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux
    asz = th * 0.83                                  # 箭头长（照模板 6.0 字 : 5.0 箭头）
    exo = th * 0.10                                  # 界线起点离原点的距离
    exe = th * 0.21                                  # 界线超出尺寸线的长度
    d = (anchor[0] - a[0]) * nx + (anchor[1] - a[1]) * ny     # 尺寸线相对线段的偏移
    sgn = 1.0 if d >= 0 else -1.0
    if abs(d) < th:                       # 锚点几乎落在导线上：尺寸线让开一点，
        # 否则尺寸线会和导线重合、看不出是标注；完全在导线上时往**下**让（上面是阵列）
        d = (-1.0 if ny > 0 else 1.0) * th * 1.4 if abs(d) < 1e-9 else sgn * th * 1.4
    A = (a[0] + nx * d, a[1] + ny * d)
    B = (b[0] + nx * d, b[1] + ny * d)
    nxs, nys = nx * sgn, ny * sgn
    ents = []
    for (ox, oy), (px, py) in ((a, A), (b, B)):      # 两条尺寸界线
        ents.append(_dim_line((ox + nxs * exo, oy + nys * exo),
                              (px + nxs * exe, py + nys * exe)))
    ents.append(_dim_line((A[0] + ux * asz, A[1] + uy * asz),
                          (B[0] - ux * asz, B[1] - uy * asz)))   # 尺寸线
    hw = asz / 3.0
    ents.append(_dim_solid(A, (A[0] + ux * asz - uy * hw, A[1] + uy * asz + ux * hw),
                           (A[0] + ux * asz + uy * hw, A[1] + uy * asz - ux * hw)))
    ents.append(_dim_solid(B, (B[0] - ux * asz - uy * hw, B[1] - uy * asz + ux * hw),
                           (B[0] - ux * asz + uy * hw, B[1] - uy * asz - ux * hw)))
    tpos = (anchor[0] + nxs * th * 0.62, anchor[1] + nys * th * 0.62)
    ents.append(_dim_mtext(tpos, txt, th, math.atan2(uy, ux)))    # 文字
    ents.append(_dim_points([a, b, A]))                           # 定义点
    return ents, {"A": A, "B": B, "len": L, "ang": math.degrees(math.atan2(uy, ux)),
                  "asz": asz}


def dim_entity_pairs(name, style, a, b, anchor, txt, g, th):
    """DIMENSION 实体（对齐线性标注），字段顺序照 ZWCAD 写出来的那份。"""
    return [("0", "DIMENSION"), ("100", "AcDbEntity"), ("8", "WIRE_LABEL"),
            ("100", "AcDbDimension"), ("280", "0"),
            ("2", name),
            ("10", "%.6f" % g["A"][0]), ("20", "%.6f" % g["A"][1]), ("30", "0.0"),
            ("11", "%.6f" % anchor[0]), ("21", "%.6f" % anchor[1]), ("31", "0.0"),
            ("12", "0.0"), ("22", "0.0"), ("32", "0.0"),
            ("70", "128"),
            ("1", txt), ("71", "5"), ("42", "%.6f" % g["len"]),
            ("73", "0"), ("74", "0"), ("75", "0"),
            ("3", style),
            ("100", "AcDbAlignedDimension"),
            ("13", "%.6f" % a[0]), ("23", "%.6f" % a[1]), ("33", "0.0"),
            ("14", "%.6f" % b[0]), ("24", "%.6f" % b[1]), ("34", "0.0"),
            ("50", "%.4f" % g["ang"]),
            ("100", "AcDbRotatedDimension"),
            ("1001", "ACAD"), ("1000", "DSTYLE"), ("1002", "{"),
            ("1070", "140"), ("1040", "%.4f" % th),
            ("1070", "41"), ("1040", "%.4f" % g["asz"]),
            ("1002", "}")]


def dim_block_file(name, ent_records, path):
    """把标注块内容写成一个“块库式”小 DXF（交给 blockpack.pack_into 并进外框）。

    文件名 = 块名；ENTITIES 段 = 块内容；顺带把 WIRE_LABEL 图层定义带上，
    这样外框里没有这个层时也会被补出来（标注实体本身就在这个层上）。
    """
    out = []

    def sec(nm, body):
        out.append(("0", "SECTION"))
        out.append(("2", nm))
        out.extend(body)
        out.append(("0", "ENDSEC"))

    # 图层记录必须是 R2000+ 的完整写法：句柄/owner + 两条 100 子类标记 + 名字。
    # 少写 330 或 100（或者把 2 放到 100 前面）→ CAD 判“无效或不完整的 DXF 输入”，
    # 整张图被放弃。这里照外框图里自带图层记录的格式写。
    sec("TABLES", [("0", "TABLE"), ("2", "LAYER"), ("70", "1"),
                   ("0", "LAYER"), ("5", "1"), ("330", "0"),
                   ("100", "AcDbSymbolTableRecord"), ("100", "AcDbLayerTableRecord"),
                   ("2", "WIRE_LABEL"), ("70", "0"),
                   ("62", "7"), ("6", "Continuous"),
                   ("0", "ENDTAB")])
    sec("ENTITIES", [p for rec in ent_records for p in rec])
    out.append(("0", "EOF"))
    with open(path, "wb") as f:
        f.write("".join("%s\r\n%s\r\n" % (c, v) for c, v in out).encode("utf-8"))
    return path


def renumber_handles(data, handle):
    """把这段实体里的句柄(5)整体换一段新的（返回 bytes，并把 handle[0] 推到后面）。

    用在哪：我们自己画的实体和“后并进来的标注块”都从外框原最大句柄往上发号，
    不挪一段就会撞句柄（CAD 里会报重复句柄/实体错乱）。
    """
    out = bytearray()
    prev = 0
    for g in bp.iter_groups(data):
        start, vstart, vend, end, code, _val = g
        out += data[prev:start]
        if code == b"5":
            newv = ("%X" % handle[0]).encode("ascii")
            handle[0] += 1
            out += data[start:vstart] + newv + data[vend:end]
        else:
            out += data[start:end]
        prev = end
    out += data[prev:]
    return bytes(out)


def _content_bbox(insts, places, scales):
    xs = []; ys = []
    for idx, it in enumerate(insts):
        s = scales[idx]; P = places[idx]["P"]; b = cl.bbox(it["prims"])
        xs += [P[0] + b[0] * s, P[0] + b[1] * s]
        ys += [P[1] + b[2] * s, P[1] + b[3] * s]
    return (min(xs), max(xs), min(ys), max(ys)) if xs else None


def _match_pairs(a_pts, b_pts):
    """按 Y 就近把两组接点一一配对(贪心)。返回 [(i, j), ...]。

    比距离之前，先用两组的“中心高度差”估一个纵向偏移：
    块与块之间的纵向偏移常常比两个接点自己的间距还大，直接比绝对 Y 会把
    上面的接点配到下面那个上（一个对齐、一个错开一整格），线就成斜的了。
    """
    if not a_pts or not b_pts:
        return []
    off = (sum(p[1] for p in a_pts) / len(a_pts)
           - sum(p[1] for p in b_pts) / len(b_pts))
    cand = sorted((abs(a[1] - (b[1] + off)), i, j)
                  for i, a in enumerate(a_pts)
                  for j, b in enumerate(b_pts))
    pi, pj, res = set(), set(), []
    for d, i, j in cand:
        if i in pi or j in pj:
            continue
        pi.add(i); pj.add(j); res.append((i, j))
    return res


def _match_span_scales(insts):
    """接点对齐缩放：把每块“右侧接点间距”缩放到几何平均，返回每块的缩放。

    和链模式第 5 章同一套口径。块右侧接点不足 2 个的（或间距为 0）保持 1.0。
    """
    spans = []
    for it in insts:
        r = cl.side_ports(it["prims"], it["pts"], "right")
        if len(r) >= 2:
            sp = r[0][1] - r[-1][1]
            if sp > 1e-6:
                spans.append(sp)
    if not spans:
        return [1.0] * len(insts)
    target = math.prod(spans) ** (1.0 / len(spans))
    out = []
    for it in insts:
        r = cl.side_ports(it["prims"], it["pts"], "right")
        s = 1.0
        if len(r) >= 2:
            sp = r[0][1] - r[-1][1]
            if sp > 1e-6:
                s = target / sp
        out.append(s)
    return out


def _place_chain(insts, gap, match_span, kscale=1.0):
    """计算每块的插入位置/缩放(与 build_chain_raw 同算法)。返回 (places, scales, log)。

    kscale：把“整个世界”等比放大 kscale 倍（块、间隔一起放大）。
    用于整体适配画图区——只放大间隔、不放大块，会让连线端点对不上接点。
    """
    log = []
    scales = [1.0] * len(insts)
    if match_span:
        spans = []
        for it in insts:
            r = cl.side_ports(it["prims"], it["pts"], "right")
            if len(r) >= 2:
                sp = r[0][1] - r[-1][1]
                if sp > 1e-6:
                    spans.append(sp)
        if spans:
            target = math.prod(spans) ** (1.0 / len(spans))
            for idx, it in enumerate(insts):
                r = cl.side_ports(it["prims"], it["pts"], "right")
                if len(r) >= 2:
                    sp = r[0][1] - r[-1][1]
                    if sp > 1e-6:
                        scales[idx] = target / sp
    if abs(kscale - 1.0) > 1e-12:
        scales = [s * kscale for s in scales]
    places = []
    prev_outs = None; prev_right = None
    for idx, it in enumerate(insts):
        s = scales[idx]
        r = [(x * s, y * s) for x, y in cl.side_ports(it["prims"], it["pts"], "right")]
        l = [(x * s, y * s) for x, y in cl.side_ports(it["prims"], it["pts"], "left")]
        b = cl.bbox(it["prims"])
        bl = (b[0] * s, b[1] * s, b[2] * s, b[3] * s)
        if idx == 0:
            P = (0.0, 0.0)
        else:
            pr = _match_pairs(prev_outs, l)
            offs = sorted(prev_outs[i][1] - l[j][1] for (i, j) in pr)
            off = offs[len(offs) // 2] if offs else 0.0
            P = (prev_right + gap * kscale - bl[0], off)
        outs = [(x + P[0], y + P[1]) for x, y in r]
        lins = [(x + P[0], y + P[1]) for x, y in l]
        places.append({"P": P, "s": s, "outs": outs, "lins": lins, "right": P[0] + bl[1]})
        if idx > 0:
            log.append("%s -> %s : 连 %d 条线" %
                       (insts[idx-1]["name"], it["name"], len(_match_pairs(prev_outs, lins))))
        log.append("放块 %s 于 (%.2f, %.2f) 缩放 x%.4f" % (it["name"], P[0], P[1], s))
        prev_outs = outs; prev_right = places[-1]["right"]
    return places, scales, log


def conn_points_on_drawing(sec):
    """图纸上所有“实际”的 CONN 点坐标。

    做法：遍历 ENTITIES 里的 INSERT，用它的插入点/缩放/旋转，把块定义内部的
    CONN* 层 POINT 变换到图纸坐标。用来核对连线端点是否真的落在接点上。
    """
    bmap = _blocks_map(sec)
    out = []
    for rec in group_entities(sec.get("ENTITIES", [])):
        if rec[0][1] != "INSERT":
            continue
        nm = _g1(rec, "2")
        px, py = _gf(rec, "10"), _gf(rec, "20")
        sx = _gf(rec, "41", 1.0) or 1.0
        sy = _gf(rec, "42", 1.0) or 1.0
        rr = math.radians(_gf(rec, "50", 0.0))
        ca, sa = math.cos(rr), math.sin(rr)
        for x, y, _lay in _conn_points(bmap.get(nm, []), bmap):
            x, y = x * sx, y * sy
            out.append((px + x * ca - y * sa, py + x * sa + y * ca))
    return out


def _conn_points(recs, bmap, mtx=(1, 0, 0, 1, 0, 0), depth=0):
    """把一个块定义里的 CONN* 层 POINT 收集出来（**会递归进子块**），返回块局部坐标。

    为什么必须递归：从别的图里抽出来的块（extract_blocks.py 的产物），它的图形
    和接点都在**子块**里（顶层只有一个 INSERT），只读顶层等于没有接点。
    """
    out = []
    if depth > 8 or not recs:
        return out
    for r in recs:
        if not r:
            continue
        t = r[0][1] if r[0][0] == "0" else ""
        if t == "POINT":
            lay = _g1(r, "8") or ""
            if lay.upper().startswith("CONN"):
                x, y = _apply(mtx, _gf(r, "10"), _gf(r, "20"))
                out.append((x, y, lay))
        elif t == "INSERT":
            nm = _g1(r, "2")
            sub = bmap.get(nm)
            if not sub:
                continue
            px, py = _gf(r, "10"), _gf(r, "20")
            sx = _gf(r, "41", 1.0) or 1.0
            sy = _gf(r, "42", 1.0) or 1.0
            rr = math.radians(_gf(r, "50", 0.0))
            T = (1, 0, 0, 1, px, py)
            R = (math.cos(rr), math.sin(rr), -math.sin(rr), math.cos(rr), 0, 0)
            S = (sx, 0, 0, sy, 0, 0)
            out.extend(_conn_points(sub, bmap, _matmul(mtx, _matmul(_matmul(T, R), S)), depth + 1))
    return out


def wires_on_conn(sec, tol=1e-3):
    """检查每条 WIRE 的两个端点是否都落在 CONN 点上。

    返回 (CONN点列表, 没落在接点上的端点列表)。
    """
    return wires_check(sec, (), tol)


def wires_check(sec, extra=(), tol=1e-3):
    """同 wires_on_conn，但允许额外的“锚点”（阵列端子、线束顶端）。

    返回 (CONN点列表, 没落在任何接点/锚点上的端点列表)。
    """
    pts = conn_points_on_drawing(sec)
    allowed = pts + list(extra)
    bad = []
    for rec in group_entities(sec.get("ENTITIES", [])):
        if (_g1(rec, "8") or "").upper() != "WIRE":
            continue
        et = rec[0][1]
        if et == "LINE":
            ends = [(_gf(rec, "10"), _gf(rec, "20")),
                    (_gf(rec, "11"), _gf(rec, "21"))]
        elif et == "LWPOLYLINE":
            v = _lw_verts(rec)          # 折线只查两个“线头”，拐点不是接点
            ends = [(v[0][0], v[0][1]), (v[-1][0], v[-1][1])] if len(v) >= 2 else []
        else:
            continue
        for x, y in ends:
            if not allowed or min(math.hypot(x - a, y - b) for a, b in allowed) > tol:
                bad.append((x, y))
    return pts, bad


def frame_block_names(frame):
    """列出外框图里的用户块名(排除匿名块)。"""
    if not (frame and os.path.exists(frame)):
        return []
    sec, _o = parse_sections(frame, "utf-8")
    return [n for n in _br_names(sec.get("TABLES", []))
            if n and not n.startswith("*") and not n.startswith("A$")]


def frame_block_svg(frame, name):
    """渲染外框图里某块的 SVG 预览。"""
    try:
        sec, _o = parse_sections_text(read_dxf_text(frame))
        bmap = _blocks_map(sec)
        recs = bmap.get(name, [])
        return entities_to_svg(recs, bmap) if recs else ""
    except Exception:
        return ""


def block_file_svg(name):
    """渲染块库某个块文件（blocklib/blocks/<name>.dxf）的 SVG 预览。

    和生成结果用同一套渲染器（SPLINE/HATCH/椭圆/多段线凸度都认），
    这样界面上的块卡片和最后画到图上的样子是一致的。
    """
    p = os.path.join(ui.BLOCKS_DIR, name + ".dxf")
    if not os.path.exists(p):
        return ""
    try:
        sec, _o = parse_sections_text(read_dxf_text(p))
        return entities_to_svg(group_entities(sec.get("ENTITIES", [])),
                               _blocks_map(sec))
    except Exception:
        return ""


def _block_insts(bmap, names, log=None):
    """从块映射里取“几何 + CONN 接点”，得到 build_chain_frame 用的 insts。

    没有 CONN 点时兜底用包围盒四边中点（别再取所有端点，会连出大量乱线）。
    """
    insts = []
    for name in names:
        recs = bmap.get(name)
        if not recs:
            if log is not None:
                log.append("⚠ 外框图里没有 %s 的块定义，跳过" % name)
            continue
        prims = []
        _prim_list(recs, bmap, (1, 0, 0, 1, 0, 0), 0, prims)
        # 递归进子块拿接点：抽出来的块，接点在子块里（顶层只有一个 INSERT）
        # CONN-Label 层的点是“线号标注落点”，不是接线点：混在接点里会被当成块最外侧
        # 那一列接点（Male 的标签在右、Fmale 的在左），于是连线画到标签上、公头母头
        # 也对着标签摆（负极行偏 9 个单位的根源）。
        _conn = _conn_points(recs, bmap)
        pts = [(x, y) for x, y, lay in _conn if "LABEL" not in (lay or "").upper()]
        if not pts:
            pts = [(x, y) for x, y, _lay in _conn]     # 只有标签点的块：退回老口径
        if not pts:
            bb = _prims_bbox(prims)
            if bb:
                cx = (bb[0] + bb[1]) / 2.0
                cy = (bb[2] + bb[3]) / 2.0
                pts = [(cx, bb[3]), (cx, bb[2]), (bb[0], cy), (bb[1], cy)]
        if prims and pts:
            insts.append({"name": name, "prims": prims, "pts": pts})
    return insts


def _pack_missing(fb, sec, names, log):
    """链里用到、外框图里没有的块，定点把块库里的定义并进外框字节。

    返回 (fb, sec, fr_blocks)。fb 是 bytes；没补块时原样返回。
    """
    fr_blocks = set(_br_names(sec.get("TABLES", [])))
    need, miss = [], []
    for n in names:
        if n in fr_blocks or n in need:
            continue
        if os.path.exists(os.path.join(ui.BLOCKS_DIR, n + ".dxf")):
            need.append(n)
        elif n not in miss:
            miss.append(n)
    for n in miss:
        log.append("⚠ 块库里没有 %s，外框里也没有定义，跳过" % n)
    if not need:
        return fb, sec, fr_blocks
    plog = []
    try:
        base_bad = bp.verify(fb)               # 外框图自己就有的毛病（比如自带重复句柄）
        fb2, info = bp.pack_into(fb, [os.path.join(ui.BLOCKS_DIR, n + ".dxf")
                                      for n in need], plog)
        pb = bp.verify(fb2, need)
        if base_bad:
            # 外框原字节本来就不合格时，不能把它算成“我们补块弄坏的”：
            # 只关心“新增”的问题；老问题原样保留，照原样写进日志。
            pb = [x for x in pb if x not in base_bad]
            log.append("注意：外框图本身就有结构问题（%s），补块只看新增问题"
                       % "; ".join(base_bad))
        if pb:
            log.append("⚠ 补块定义后结构检查没过，退回外框原字节: " + "; ".join(pb))
        else:
            fb = fb2
            sec, _o = parse_sections_bytes(fb, "utf-8")
            fr_blocks = set(_br_names(sec.get("TABLES", [])))
            log.append("已把块库定义并入外框图: " + ", ".join(need))
    except Exception as ex:
        log.append("⚠ 补块定义失败(%s)，这些块可能不显示" % ex)
    log.extend(plog)
    return fb, sec, fr_blocks


def _splice_entities(fb, content, next_handle):
    """把内容插到 ENTITIES 段的 ENDSEC 前，并同步 $HANDSEED。返回 bytes 或 None。

    插入点必须精确落在 ENDSEC 那“一个组”的行首：用组解析定位，不能用正则，
    否则会吃掉上一条实体结尾的换行、把新增实体整体错行（12.1 的真 bug）。
    """
    gl = list(bp.iter_groups(fb))
    rng = bp.section_range(gl, b"ENTITIES")
    if rng is None:
        return None
    at = gl[rng[1]][0]
    out = fb[:at] + bytes(content) + fb[at:]
    seed = b"%X" % (next_handle + 1)
    return re.sub(rb"(\$HANDSEED\r?\n[ \t]*5\r?\n[ \t]*)([0-9A-Fa-f]+)",
                  lambda mm: mm.group(1) + seed, out, count=1)


def keep_hand_entities(src_path, prefix, mspace, nh, log=None):
    """从旧输出里挑出手工画的实体（图层名以 prefix 开头），重新编号后返回。

    用途：程序画到 COM 端为止，剩下的线人在 CAD 里接；下次重新生成时，
    把上一版里 HAND_ 层的实体原样搬过来，不用重画。
    返回 [(code, value), ...] 的扁平列表（直接塞进 ENTITIES 段）。
    """
    log = log if log is not None else []
    out = []
    try:
        sec3, _o = parse_sections_text(read_dxf_text(src_path))
    except Exception as ex:
        log.append("⚠ 保留手工内容失败(读不了 %s): %s" % (os.path.basename(src_path), ex))
        return [], set()
    pre = (prefix or "HAND").upper()
    recs = group_entities(sec3.get("ENTITIES", []))
    parent = None            # 上一条 POLYLINE 分到的新句柄（VERTEX/SEQEND 要认它）
    n = 0
    miss = set()
    for rec in recs:
        if not rec:
            continue
        t = rec[0][1]
        if t in ("VERTEX", "SEQEND"):
            if parent is None:
                continue                      # 上一条不是我们保留的多段线
            for c, v in clone_entity(rec, 0.0, 0.0, nh(), parent):
                out.append((c, v))
            continue
        parent = None
        lay = (_g1(rec, "8") or "").upper()
        if not lay.startswith(pre):
            continue
        h = nh()
        for c, v in clone_entity(rec, 0.0, 0.0, h, mspace or "0"):
            out.append((c, v))
        n += 1
        if t == "INSERT":
            miss.add(_g1(rec, "2") or "")
        if t == "POLYLINE":
            parent = h
    if n:
        log.append("保留手工内容: 从 %s 搬来 %d 个 %s* 层实体"
                   % (os.path.basename(src_path), n, pre))
    else:
        log.append("保留手工内容: %s 里没有 %s* 层的实体" % (os.path.basename(src_path), pre))
    return out, miss


def build_chain_frame(frame, chain, gap=40.0, match_span=True, show_len=True, fit=0.55):
    """【最稳】把内容“插入”到外框图原始字节里，其余字节不动。
    内容 = 每个块一个 INSERT(引用块名, 用框内已有定义) + 连线 + 线长。
    外框里没有的块，先从块库把块定义定点插进来（否则 AutoCAD 不显示任何东西）。
    返回 (dxf_text_latin1, log)。
    """
    if not (frame and os.path.exists(frame)):
        return None, ["没有外框图"]
    log = []
    fb = open(frame, "rb").read()
    sec, _order = parse_sections(frame, "utf-8")

    # === 缺块就补：把块库里的块定义定点插进外框字节 ===
    fb, sec, fr_blocks = _pack_missing(fb, sec, chain, log)

    mspace = model_space_handle(sec)
    maxh = max_handle(sec)

    bmap = _blocks_map(sec)
    insts = _block_insts(bmap, chain)
    if not insts:
        return None, ["没有可用块"]

    places, scales, place_log = _place_chain(insts, gap, match_span)

    # 整体适配：把整条链等比缩放，塞进“画图区”矩形，再居中。
    rect = frame_draw_rect(sec)
    fbx = rect or _records_bbox(group_entities(sec.get("ENTITIES", [])),
                                _blocks_map(sec))
    cb = _content_bbox(insts, places, scales)
    k = 1.0
    if fit and cb and fbx and (cb[1] - cb[0]) > 1e-6 and (cb[3] - cb[2]) > 1e-6:
        k = min((fbx[1] - fbx[0]) * fit / (cb[1] - cb[0]),
                (fbx[3] - fbx[2]) * fit / (cb[3] - cb[2]))
        k = max(k, 1e-4)
        if abs(k - 1.0) > 1e-6:
            # 整条链等比放大 k 倍（块 + 间隔一起），再重解一次位置
            places, scales, place_log = _place_chain(insts, gap, match_span, k)
            cb = _content_bbox(insts, places, scales)
            log.append("整体适配画图区: 等比 x%.4f" % k)
    off = (0.0, 0.0)
    if cb and fbx:
        off = ((fbx[0] + fbx[1]) / 2 - (cb[0] + cb[1]) / 2,
               (fbx[2] + fbx[3]) / 2 - (cb[2] + cb[3]) / 2)
    log.extend(place_log)
    log.append("套用外框图: %s (画图区 %s, 内容偏移 %.1f, %.1f)" %
               (os.path.basename(frame),
                ("%.0fx%.0f" % (fbx[1] - fbx[0], fbx[3] - fbx[2])) if fbx else "无",
                off[0], off[1]))

    handle = [maxh + 1]

    def nh():
        v = "%X" % handle[0]; handle[0] += 1; return v

    def blk(c, v):
        return (c + "\r\n" + str(v) + "\r\n").encode("utf-8")

    content = bytearray()
    for idx, it in enumerate(insts):
        P = places[idx]["P"]; s = scales[idx]
        rec = [("0", "INSERT"), ("5", nh()), ("330", mspace or "0"),
               ("100", "AcDbEntity"), ("8", "0"), ("100", "AcDbBlockReference"),
               ("2", it["name"]),
               ("10", "%.6f" % (P[0] + off[0])), ("20", "%.6f" % (P[1] + off[1])),
               ("30", "0.0"), ("41", "%.6f" % s), ("42", "%.6f" % s),
               ("43", "1.0"), ("50", "0.0")]
        for c, v in rec:
            content += blk(c, v)
    for idx in range(1, len(insts)):
        pr = _match_pairs(places[idx-1]["outs"], places[idx]["lins"])
        for (i, j) in pr:
            a = places[idx-1]["outs"][i]; b = places[idx]["lins"][j]
            a = (a[0] + off[0], a[1] + off[1]); b = (b[0] + off[0], b[1] + off[1])
            for c, v in [("0", "LINE"), ("5", nh()), ("330", mspace or "0"),
                         ("100", "AcDbEntity"), ("8", "WIRE"), ("100", "AcDbLine"),
                         ("10", "%.6f" % a[0]), ("20", "%.6f" % a[1]), ("30", "0.0"),
                         ("11", "%.6f" % b[0]), ("21", "%.6f" % b[1]), ("31", "0.0")]:
                content += blk(c, v)
            if show_len:
                L = ((a[0]-b[0]) ** 2 + (a[1]-b[1]) ** 2) ** 0.5
                h = max(4.0, gap * 0.15)
                for c, v in [("0", "TEXT"), ("5", nh()), ("330", mspace or "0"),
                             ("100", "AcDbEntity"), ("8", "TEXT"), ("100", "AcDbText"),
                             ("10", "%.6f" % ((a[0]+b[0])/2)),
                             ("20", "%.6f" % ((a[1]+b[1])/2 + h*1.3)), ("30", "0.0"),
                             ("40", "%.4f" % h), ("1", "%.1f" % L), ("50", "0.0")]:
                    content += blk(c, v)

    # 插入点必须精确落在 ENTITIES 段 ENDSEC 那“一个组”的行首。
    # （以前用 \s*0\r?\nENDSEC 正则会吃掉上一条实体结尾的换行，把值行粘坏。）
    out = _splice_entities(fb, content, handle[0])
    if out is None:
        return None, ["外框图无 ENTITIES 段"]
    missing = [it["name"] for it in insts if it["name"] not in fr_blocks]
    if missing:
        log.append("⚠ 外框里没有这些块定义(可能不显示): " + ", ".join(missing))
    try:
        pts, bad = wires_on_conn(parse_sections_bytes(out, "utf-8")[0])
        if bad:
            log.append("⚠ 连线端点检查: %d 个端点没落在 CONN 点上 %s"
                       % (len(bad), ["(%.2f, %.2f)" % b for b in bad[:4]]))
        else:
            log.append("连线端点检查: 全部落在 CONN 点上（图上共 %d 个 CONN 点）" % len(pts))
    except Exception as ex:
        log.append("连线端点检查失败: %s" % ex)
    return out.decode("latin-1"), log


# ================= 阵列 + 线束（交接手册第 13 章 · 阶段①②） =================

def _terminal_pair(recs, prims, bmap, name="", log=None):
    """模块块的正/负出线点（块局部坐标）。

    优先读 CONN_POS / CONN_NEG 层上的 POINT（**递归进子块**），两个层都齐了才用它。
    缺任何一个就按 13.8 的临时兜底：底边左 1/3 = 正极，底边右 2/3 = 负极。
    等你在 CAD 里把两个 POINT 补上，这段兜底自动失效、不用改代码。
    """
    pos, neg, fb = [], [], False
    for x, y, ln in _conn_points(recs, bmap):
        ln = (ln or "").upper()
        if "POS" in ln:
            pos.append((x, y))
        elif "NEG" in ln:
            neg.append((x, y))
    bb = _prims_bbox(prims)
    if bb is None:
        return None, None, False
    if not pos or not neg:
        w = bb[1] - bb[0]
        if not pos:
            pos = [(bb[0] + w / 3.0, bb[2])]
        if not neg:
            neg = [(bb[0] + 2.0 * w / 3.0, bb[2])]
        fb = True
        if log is not None:
            log.append("块 %s 没有 CONN_POS/CONN_NEG 点，用底边兜底："
                       "左1/3=正极，右2/3=负极" % (name or "?"))
    return sorted(pos)[0], sorted(neg)[-1], fb


def _place_array(tmpl, n_strings, pitch, s_gap, dir="right"):
    """排“串 × 块”阵列（单位空间，k=1）。返回 (cells, 内容包围盒)。

    tmpl：一串的块序列，每项 {"name","bb","pos_l","neg_l"}，长度就是每串块数。
          （老写法是同一个块重复 N 次；现在支持 首块 + N×中间块 + 尾块）
    cells：每块一条 {"i","s","P","name","bb","pos_l","neg_l"}。
    pitch：同一串里相邻两块插入点的距离。
    s_gap：串与串之间的净空。
    dir  ："right" = 每串一行、第 1 串在最左，下一串接在右边；
           "down"  = 每串一行、第 1 串在最上，下一串叠在下面。
    """
    n_per = len(tmpl)
    w_last = tmpl[-1]["bb"][1] - tmpl[-1]["bb"][0]
    span = (n_per - 1) * pitch + w_last      # 一串占的长度
    hmax = max(t["bb"][3] - t["bb"][2] for t in tmpl)
    cells = []
    for s in range(n_strings):
        if dir == "down":
            ox, oy = 0.0, -s * (hmax + s_gap)
        else:
            ox, oy = s * (span + s_gap), 0.0
        for i, t in enumerate(tmpl):
            cells.append({"i": i, "s": s, "P": (ox + i * pitch, oy),
                          "name": t["name"], "bb": t["bb"],
                          "pos_l": t["pos_l"], "neg_l": t["neg_l"]})
    x0 = min(c["P"][0] + c["bb"][0] for c in cells)
    x1 = max(c["P"][0] + c["bb"][1] for c in cells)
    y0 = min(c["P"][1] + c["bb"][2] for c in cells)
    y1 = max(c["P"][1] + c["bb"][3] for c in cells)
    return cells, (x0, x1, y0, y1)


def build_array_frame(frame, spec, log=None, progress=None):
    """阵列（组件）+ 线束 生成，写进外框字节。返回 (dxf_text, log, wires)。

    画面（手册 13.1）：上=组件阵列，中=跨接线，下=线束。
    整体只等比缩一次：k 同时作用于块、间距和接点坐标（12.8 踩过的坑）。
    """
    log = list(log) if log else []
    wires = []

    def pg(pct, stage):
        if progress:
            try:
                progress(pct, stage)
            except Exception:
                pass

    pg(2, "开始")
    if not (frame and os.path.exists(frame)):
        return None, ["没有外框图"], wires

    def _n(key, dflt):
        v = spec.get(key)
        return dflt if v is None or v == "" else float(v)

    module   = (spec.get("module") or "").strip()
    m_first  = (spec.get("module_first") or "").strip()   # 每串第一块（带正极出线）
    m_mid    = (spec.get("module_mid") or "").strip()     # 每串中间块（重复）
    m_last   = (spec.get("module_last") or "").strip()    # 每串最后一块（带负极出线）
    n_per    = int(_n("n_per", 0))
    n_str    = int(_n("n_strings", 0))
    gap_x    = _n("gap_x", 2.0)          # 板与板之间的净空（贴板就填 2 甚至 0）
    gap_y    = _n("gap_y", 2.0)          # 串与串之间的净空（可能放电机，默认先贴紧）
    dirn     = spec.get("dir") or "right"   # right=串从左往右接；down=串从上往下叠
    link     = bool(spec.get("link_array"))  # 是否画“阵列 ↔ 线束”的跨接线（默认不画）
    match_h  = spec.get("match_span")
    match_h  = True if match_h is None else bool(match_h)   # 线束各块接点对齐缩放
    h_scale  = _n("harness_scale", 1.0)                     # 线束整体微调倍率
    sw_in    = spec.get("string_wires")                     # 串内要不要画连线
    fix_gap  = _n("fixed_gap", 30.0)                        # FUSE / CU-AL 与相邻块的固定间距
    fix_gnames = spec.get("fixed_gap_names") or ["FUSE", "CU - AL"]
    head_blk = (spec.get("head_block") or "").strip()      # 摆在阵列最左边、与板子固定距离的块
    head_gap = _n("head_gap", 60.0)                         # 它与阵列左边缘的距离
    neg_auto = spec.get("neg_auto")
    neg_auto = True if neg_auto is None else bool(neg_auto)  # 负极那一行按正极自动生成
    neg_gap  = _n("neg_gap", 30.0)                           # 负极行到正极行的距离
    neg_head = (spec.get("neg_head") or "").strip()          # 负极行最左边那块（公头）
    # 负极支线块的朝向：0 = 正放（插头朝上、接点朝下落在负极行线上，和正极行一样）；
    # 180 = 翻过来挂（块会是倒的）。公头/母头不看这个值，自动朝链内。
    neg_rot  = _n("neg_rotate", 0.0)
    pos_plug = (spec.get("pos_plug") or "").strip()         # 最右边那根支线的正极上插什么块（公头）
    neg_plug = (spec.get("neg_plug") or "").strip()         # 最右边那串的负极上插什么块（母头）
    keep_from   = (spec.get("keep_from") or "").strip()     # 手工内容（HAND_ 层）从哪搬
    keep_prefix = (spec.get("keep_prefix") or "HAND").strip()
    harness  = [n for n in (spec.get("harness") or []) if n]
    _pos_feed = (spec.get("pos_feeder") or "").strip()
    # ---- 线束链不填（或填得不够）也能生成 ----
    #   没填链：自动排一条最基本的正极行 = 末端母头 + 正极支线×(串数-1) + 末端公头
    #   （负极行本来就是自动补的；FUSE 这类串联块想加就自己在链里加）
    if not harness:
        _auto = [x for x in (neg_plug or pos_plug,
                             *([_pos_feed] * max(0, n_str - 1)),
                             pos_plug) if x]
        if _auto:
            harness = _auto
            log.append("线束链没填：自动生成 " + "→".join(_auto))
    #   填了链但正极支线不够根数：把“正极支线块”补到够（每串一根，末端公头顶最后一串）
    if _pos_feed:
        _nf = sum(1 for x in harness if x in (_pos_feed, pos_plug))
        _plus = 1 if (pos_plug and pos_plug not in harness) else 0   # 公头后面会补到链尾
        _need = n_str - _plus
        if _nf < _need:
            _at = next((i for i, x in enumerate(harness)
                        if x in (_pos_feed, pos_plug)), len(harness))
            harness[_at:_at] = [_pos_feed] * (_need - _nf)
            log.append("正极支线 %d 根、串数 %d：自动补到 %d 根" % (_nf, n_str, _need))
    # 公头/母头是线束那一排的一员（和正极支线同一条水平线），不是插在串的正极旁边。
    # 用户没把它们放进链里的话，这里补到链尾。
    # 末端公头补到链尾；末端母头交给“负极行自动生成”那段处理，
    # 这里别再塞一遍，否则会多出一块、还会把正极和负极连出斜线。
    for _x in ((pos_plug,) if neg_auto else (pos_plug, neg_plug)):
        if _x and _x not in harness:
            harness.append(_x)
    # 起始块（汇流箱）也自动补进链里，否则它不会被画出来
    if head_blk and head_blk not in harness:
        harness.insert(0, head_blk)
    gap      = _n("gap", 40.0)
    pos_feed = (spec.get("pos_feeder") or "").strip()
    neg_feed = (spec.get("neg_feeder") or "").strip()
    # 负极行自动生成（和正极那一行对称）：
    #   正极行：头部接头（对齐 CBX） + 正极支线×(串数-1) + 末端公头
    #   负极行：头部接头（对齐 CBX） + 负极支线×(串数-1) + 末端母头
    # 关键在于**头部接头不占板子**：以前头部那根公头是钉在第 1 串的负极上的，
    # 于是第 1 串负极端子下面是公头、不是负极支线（用户要的是每串都有负极支线）。
    # 必须放在 _block_insts 之前补。
    neg_from = None
    if neg_auto and (neg_feed or neg_plug or neg_head) and n_str >= 1:
        _head = neg_head or pos_plug or neg_plug
        _mid  = neg_feed or neg_plug or _head
        _tail = neg_plug or neg_feed or _head
        if n_str == 1:
            _pins = [_tail]
        else:
            _pins = [_mid] * (n_str - 1) + [_tail]
        neg_seq = [_head] + _pins if _head else list(_pins)
        neg_seq = [x for x in neg_seq if x]
        if neg_seq:
            neg_from = len(harness)
            harness.extend(neg_seq)
    # 能接串的“支线” = 正极支线 + 末端公头（公头也顶一根）；负极同理用母头
    pos_names = [x for x in (pos_feed, pos_plug) if x]
    neg_names = [x for x in (neg_feed, neg_plug) if x]
    awg_main = (spec.get("awg_main") or "").strip()
    awg_br   = (spec.get("awg_branch") or "").strip()
    allow_up = bool(spec.get("allow_enlarge"))
    clear_r  = _n("clearance_ratio", 0.5)
    label_r  = _n("label_ratio", 0.006)
    inset_x  = _n("inset_x", 0.04)
    inset_y  = _n("inset_y", 0.06)
    k_floor  = _n("k_floor", 0.35)

    # 先把“实际收到的配置”回显出来，省得某一栏空着还到处找原因
    log.append("配置: 组件 %s/%s/%s  每串%d块×%d串  板净空%.1f 串净空%.1f" %
               (m_first or module, m_mid or module, m_last or module,
                n_per, n_str, gap_x, gap_y))
    log.append("     线束链 %s | 正极支线块=%s 末端公头=%s 末端母头=%s | 负极自动=%s 负极支线块=%s" %
               ("→".join(harness) or "（空）", pos_feed or "（空）", pos_plug or "（空）",
                neg_plug or "（空）", "开" if neg_auto else "关", neg_feed or "（空）"))
    log.append("     起始块=%s 间距%.0f | 跨接线=%s" %
               (head_blk or "（空）", head_gap, "开" if link else "关"))

    seq_mode = bool(m_first and m_mid and m_last)
    if (not module and not seq_mode) or n_per < 1 or n_str < 1:
        return None, ["阵列参数不完整：组件块 / 每串板数 / 串数 都要填"], wires

    fb = open(frame, "rb").read()
    sec, _order = parse_sections(frame, "utf-8")
    need_blocks = ([module] if module else []) + \
                  ([m_first, m_mid, m_last] if seq_mode else []) + harness
    fb, sec, fr_blocks = _pack_missing(fb, sec, list(dict.fromkeys(
        need_blocks + [x for x in (pos_plug, neg_plug) if x])), log)
    pg(20, "块定义已并入外框图")
    mspace = model_space_handle(sec)
    maxh = max_handle(sec)
    bmap = _blocks_map(sec)

    # ---- 组件：每串的块序列 ----
    # 新写法（手册 26 章）：每串 = 首块 + (n-2)×中间块 + 尾块，n 含首尾。
    # 老写法：整串都是同一个 module 重复 n 次。
    if seq_mode:
        seq = [m_first] + [m_mid] * max(0, n_per - 2) + [m_last]
        if n_per == 1:
            seq = [m_last]
        if sw_in is None:
            sw_in = False          # 拼块模式：组件是并排贴着的，串内默认不画线
        log.append("每串拼法: %s" % " + ".join(
            ["1×" + m_first] + (["%d×%s" % (n_per - 2, m_mid)] if n_per > 2 else []) + ["1×" + m_last]))
    else:
        seq = [module] * n_per
    if sw_in is None:
        sw_in = True               # 老模式（同一个块重复）保持画串内线
    sw_in = bool(sw_in)
    kinds = {}
    for nm in dict.fromkeys(seq):
        recs = bmap.get(nm)
        if not recs:
            return None, ["外框图和块库里都没有组件块 " + nm], wires
        pr = []
        _prim_list(recs, bmap, (1, 0, 0, 1, 0, 0), 0, pr)
        bb = _prims_bbox(pr)
        if bb is None:
            return None, ["组件块 %s 没有几何" % nm], wires
        pl, nl, _fbk = _terminal_pair(recs, pr, bmap, nm, log)
        kinds[nm] = {"name": nm, "bb": bb, "pos_l": pl, "neg_l": nl}
    tmpl = [kinds[nm] for nm in seq]
    pos_l = tmpl[0]["pos_l"]         # 串首块的出线点（正极）
    neg_l = tmpl[-1]["neg_l"]        # 串尾块的出线点（负极）
    mbb = (min(t["bb"][0] for t in tmpl), max(t["bb"][1] for t in tmpl),
           min(t["bb"][2] for t in tmpl), max(t["bb"][3] for t in tmpl))
    # 板宽/板高按“首块和中间块”算：尾块可能多伸出一截（钩子），不能拿它当间距基准
    mid_t = tmpl[1] if len(tmpl) > 1 else tmpl[0]
    mw = max(tmpl[0]["bb"][1] - tmpl[0]["bb"][0], mid_t["bb"][1] - mid_t["bb"][0])
    mh = mid_t["bb"][3] - mid_t["bb"][2]
    if gap_x < 0:
        log.append("⚠ 板间净空 %.2f < 0，相邻两块会重叠" % gap_x)
    if gap_y < 0:
        log.append("⚠ 串间净空 %.2f < 0，相邻两串会重叠" % gap_y)

    # ---- 线束：正极支线/负极支线用“每串的 CONN_POS / CONN_NEG 点”定位 ----
    hinsts = _block_insts(bmap, harness, log) if harness else []
    # 支线块名字填错时不能让支线“掉队”（会被当成普通块串在中间）：
    # 在排版之前就退回用链里第一个块当支线，仍然按各串 CONNPOS 从左往右钉。
    _chain = [it["name"] for it in hinsts]
    # 链里一根正极支线都没有（也没公头顶着）时才算“填错名”，否则不用兜底
    if pos_feed and pos_feed not in _chain and not (pos_plug and pos_plug in _chain):
        if _chain:
            log.append("⚠ “正极支线块”填的 %s 不在线束链里（链里是 %s）："
                       "暂时改用链里第一个块 %s 当正极支线（按各串 CONNPOS 从左往右钉）"
                       % (pos_feed, ", ".join(_chain), _chain[0]))
            pos_feed = _chain[0]
        else:
            log.append("⚠ “正极支线块”填的 %s 不在线束链里，而且链是空的" % pos_feed)
    pos_names = [x for x in (pos_feed, pos_plug) if x]
    neg_names = [x for x in (neg_feed, neg_plug) if x]
    clear = mh * clear_r

    def place_harness(cellmap, abox):
        """线束定位（单位空间）。

        规则：第 k 根正极支线钉在第 k 串 CONN_POS 点的正下方，
              第 k 根负极支线钉在第 k 串 CONN_NEG 点的正下方，
              其余块（FUSE 之类）从上一块的右边接着排。
        所有线束块的顶边对齐到同一条线，整条线束挂在阵列下方。
        缩放：按“右侧接点间距一致”对齐（和链模式第 5 章同一套算法）。
        """
        if not hinsts:
            return []
        # 缩放口径：**所有线束块的接点间距统一**（取各块右侧接点间距的几何平均当目标），
        # 这样块与块之间的连线才是平的、间距才一致（“连接点缩放到对齐”）。
        # 再乘一个 harness_scale 方便手工微调。
        panel_span = abs(pos_l[0] - neg_l[0])          # 板子正负极出线点的距离
        scales = [s * h_scale for s in
                  (_match_span_scales(hinsts) if match_h else [1.0] * len(hinsts))]
        px = [cellmap[(0, s)]["P"][0] + pos_l[0] for s in range(n_str)]
        nx = [cellmap[(n_per - 1, s)]["P"][0] + neg_l[0] for s in range(n_str)]
        places, kp, kn = [], 0, 0
        right = None
        head_right = None        # 不钉位的块（保险丝/接头/汇流箱）自己排一行，从阵列左边缘起
        # 头部这一行的起点：**对齐汇流箱(CBX)的中心**（CBX 在阵列左边）
        head_x0 = None
        if head_blk:
            for it0 in hinsts:
                if it0["name"] == head_blk:
                    b0 = cl.bbox(it0["prims"])
                    w0 = (b0[1] - b0[0]) * scales[hinsts.index(it0)]
                    head_x0 = abox[0] - head_gap - w0 / 2.0
                    break
        prev_outs = None
        prev_name = None
        # 链里第一个“支线”出现的位置：它前面的不钉位块算“头部”（排左边），
        # 它后面的不钉位块算“末端”（接在最后一块右边）→ 一条链 = 头 … 支线 … 尾
        # 只看**正极支线**：公头/母头（接头块）不算支线，否则链首那个接头会被当成
        # “第一根支线”钉到板子端子上（正极行的头块会跑到第 1 串负极下面去）。
        _fi = [i for i, it in enumerate(hinsts) if it["name"] in pos_names]
        first_feed = _fi[0] if _fi else len(hinsts)
        # 负极那一行整体往下挪：正极行里最高的块 + neg_gap（只在拿不到正极行实际位置时兜底）
        _ph = [(cl.bbox(it["prims"])[3] - cl.bbox(it["prims"])[2]) * scales[i]
               for i, it in enumerate(hinsts) if it["name"] in pos_names]
        neg_dy = (max(_ph) if _ph else 0.0) + neg_gap
        # 正极行到底排在哪一行，得边排边量：正极行的 y 由链首（CBX→FUSE→支线）那串
        # 接点对齐算出来，光看“支线块的高度”是估不准的。以前用高度当代理，头部块一进链
        # 就差 30~40 个单位，负极行直接被排到正极行**上面**去了（两行叠在一起）。
        # pos_bottom = 正极行里最低的那一点（块底和接点取更低者），负极行的行线照它往下 neg_gap。
        pos_bottom = None
        neg_row_y = None          # 负极行整排共用的 y（首块定下来，后面都跟它）
        # ---- 负极行每块的朝向 ----
        #  · 竖着的“支线块”（NEG 这类：接点在块的一头、身子是竖的）**正着放**，
        #    和正极行一样：插头朝上、接点朝下落在负极行线上。以前整行硬转 180°，
        #    画出来就是“负极支线倒过来”（测试里看到的那张）。要那副样子，
        #    把界面的“负极行旋转”填 180 就行。
        #  · 横着的“接头块”（公头/母头：接点只在一侧、身子是横的）自动转到
        #    **接点朝链内、身子朝外**，不管它排在链首还是链尾（Male 在链首→180°，
        #    Fmale 在链尾→180°；反过来摆也能自己认）。
        neg_rot_by, neg_above = {}, {}   # idx -> 实际旋转角度 / 该块伸出负极行线以上的高度
        if neg_from is not None:
            for _i in range(neg_from, len(hinsts)):
                _it = hinsts[_i]
                _b = cl.bbox(_it["prims"])
                _s = scales[_i]
                _R = [(x * _s, y * _s) for x, y in
                      cl.side_ports(_it["prims"], _it["pts"], "right")]
                _L = [(x * _s, y * _s) for x, y in
                      cl.side_ports(_it["prims"], _it["pts"], "left")]
                _rot = neg_rot
                _pw, _ph2 = (_b[1] - _b[0]) * _s, (_b[3] - _b[2]) * _s
                _pins = _R + _L
                _pxs = [p[0] for p in _pins]
                if _pins and _pw > _ph2 and (max(_pxs) - min(_pxs)) < max(0.5, _pw * 0.1):
                    # 单侧接点的横块 = 接头：接点要朝链内（首块朝右、其余朝左）
                    _pin_right = ((min(_pxs) + max(_pxs)) / 2.0) > (_b[0] + _b[1]) / 2.0 * _s
                    _rot = 0.0 if _pin_right == (_i == neg_from) else 180.0
                # 这块“伸出负极行线以上”多少：行线按它往下让，免得压住正极行
                _top = (-_b[2] * _s) if _rot else (_b[3] * _s)
                _ab = 0.0
                for _c in (_R, _L):
                    if not _c:
                        continue
                    _mid = sum(p[1] for p in _c) / len(_c)
                    if _rot:
                        _mid = -_mid
                    _ab = max(_ab, _top - _mid)
                neg_rot_by[_i] = _rot
                neg_above[_i] = _ab
        for idx, it in enumerate(hinsts):
            b = cl.bbox(it["prims"])
            s = scales[idx]
            cw = (b[0] + b[1]) / 2.0 * s          # 块中心到插入点的横向距离
            bw = b[1] * s                          # 块右边界到插入点
            r = [(x * s, y * s) for x, y in cl.side_ports(it["prims"], it["pts"], "right")]
            l = [(x * s, y * s) for x, y in cl.side_ports(it["prims"], it["pts"], "left")]
            is_neg = (neg_from is not None and idx >= neg_from)
            _rot = neg_rot_by.get(idx, 0.0) if is_neg else 0.0
            if is_neg and _rot:
                # 这块转 180°：局部坐标 (x,y) -> (-x,-y)
                r = [(-x, -y) for x, y in r]
                l = [(-x, -y) for x, y in l]
                cw = -cw
                bw = -b[0] * s
            # 横向：支线把“接点”对准板子的出线点（不是把块中心对过去）；
            #       正极支线对左接点，负极支线对右接点；其余的接着上一块排。
            if is_neg:
                # 负极行：**头部接头**（第 1 块）对齐 CBX —— 和正极行的头部同一个横坐标，
                # 只是排在下面那一行；其余每块钉在第 k 串的负极出线点正下方
                # （第 1 串下面就是负极支线，不再是公头）。
                # 块里标了 CONNNEG 就用**那个接点**去对（和正极用 CONNPOS 一个道理）；
                # 没标就退回用块中心。
                _k = idx - neg_from
                if _k == 0:
                    cx = head_x0 if head_x0 is not None else (nx[0] if nx else abox[0])
                else:
                    _j = _k - 1
                    cx = nx[_j] if _j < len(nx) else (nx[-1] if nx else abox[0])
                _nn = [(x, y) for x, y, _ly in _conn_points(bmap.get(it["name"], []), bmap)
                       if "NEG" in (_ly or "").upper()]
                if _k == 0:
                    # 头部接头不钉板子：按**块中心**对齐 CBX。正极行的头部块也是中心对齐，
                    # 这样两个头部的中心才在同一条竖线上（下面才算插针）
                    pxx = cx - cw
                elif _nn:
                    px0, py0 = _nn[0]
                    # 转过 180° 的块，接点在图上跑到 -x
                    pxx = cx - ((-px0) if _rot else px0) * s
                else:
                    # 没有 CONNNEG 点的块（公头 Male / 母头 Fmale）以前按“块中心”对，
                    # 可它们的块中心离接点 9~11 个单位，插针就落在出线点旁边。
                    # 改成按**接点竖列的中线**对：只有一列的块（公头/母头）接点正好压在
                    # 出线点正下方；左右对称的块（NEG）中线≈块中心，位置和以前一样。
                    _xs = [p1[0] for p1 in (l + r)]
                    _off = ((min(_xs) + max(_xs)) / 2.0) if _xs else cw
                    pxx = cx - _off
            # 正极支线 + 末端公头都算“正极那一路”：第 k 个钉在第 k 串出线点的正下方
            elif it["name"] in pos_names and kp < len(px) and l:
                pxx = px[kp] - l[0][0]; kp += 1
            elif it["name"] in neg_names and neg_from is None and kn < len(nx) and r:
                pxx = nx[kn] - r[0][0]; kn += 1
            elif head_blk and it["name"] == head_blk:
                # 起始块（CBX）摆在阵列左边 head_gap 处；它的中心 x = head_x0，
                # 正极行的头部块和负极行的头部块都对齐这个 x（上下一条竖线，互不重叠）。
                pxx = (head_x0 if head_x0 is not None else abox[0]) - cw
            elif idx > first_feed:
                # 支线**之后**的块（末端接头之类）：接着最后一块往右排 → 落在最右边
                g = fix_gap if ((it["name"] in fix_gnames) or (prev_name in fix_gnames)) else gap
                cx = (abox[0] + cw) if right is None else (right + g + cw)
                pxx = cx - cw
            else:
                # FUSE / CU-AL 这类块：和相邻块之间用固定间距（默认 30），不跟界面上的 GAP 走
                g = gap
                if (it["name"] in fix_gnames) or (prev_name in fix_gnames):
                    g = fix_gap
                # 它们自己排一行，从**阵列左边缘**起头；不接着支线往后排
                # （接着支线排的话，保险丝/接头会被推到线束中间去）
                cx = ((head_x0 if head_x0 is not None else abox[0] + cw)
                      if head_right is None else (head_right + g + cw))
                head_right = cx + bw
                pxx = cx - cw
            if is_neg:
                # 负极行整排**共一条水平线**：
                #   每块把“朝链内那一列的接点中线”放到同一条 y 上，行内每根线两端一样高。
                # （以前每块各自按上一块接点算，Male 的接点在块局部 ±2、NEG 的插头在
                #   ±72，差 70 个单位，于是蓝线是斜的，看着像整排“倒过来”。）
                # 锚点必须是同一种点：Male/Fmale 的接点在插入点两侧、NEG 的接点在块的一头，
                # 取“离插入点最远的那个接点”当锚，公头/母头就比 NEG 高出一格接点间距，
                # 两端的线于是斜 3~4 个单位。改成一整列接点的中线就不会错格。
                # 行线高度 = 正极行最低点再往下 neg_gap（拿不到就用老口径 -neg_dy）。
                _col = l if idx == neg_from else r        # 首块看“出去”那一列，其余看“进来”那一列
                if not _col:
                    _col = l + r
                _row = (sum(p1[1] for p1 in _col) / len(_col)) if _col else 0.0
                if neg_row_y is None:
                    # 行线 = 正极行最低点 - neg_gap - “负极行里最高那块伸出来的高度”
                    # （块正放时它有大半个身子在行线以上，得把这段让出来，否则压住正极行）
                    _ab = max([neg_above.get(_i, 0.0)
                               for _i in range(neg_from, len(hinsts))] or [0.0])
                    neg_row_y = ((pos_bottom - neg_gap - _ab) if pos_bottom is not None
                                 else -neg_dy)
                pyy = neg_row_y - _row
            elif head_blk and it["name"] == head_blk:
                # 它跟板子同一水平线（块中心对齐阵列那一行的中线），不是挂在线束行上
                pyy = -(b[2] + b[3]) / 2.0 * s
            elif it["name"] in neg_names and neg_from is None:
                # 负极那一行（负极端子直接放进链里、没自动补齐的情况）：顶边挂在同一条行线上
                pyy = ((pos_bottom - neg_gap - max(neg_above.values() or [0.0]))
                       if pos_bottom is not None else -neg_dy) - b[3] * s
            elif prev_outs is None:
                pyy = -b[3] * s                 # 第一块：顶边挂在 y=0（阵列下沿）
            else:
                # 纵向按接点对齐：本块左接点落到上一块右接点的高度上，连线才是平的
                pr = _match_pairs(prev_outs, [(x + pxx, y) for x, y in l])
                offs = sorted(prev_outs[i][1] - l[j][1] for (i, j) in pr)
                pyy = offs[len(offs) // 2] if offs else (-b[3] * s)
            if (not is_neg) and not (head_blk and it["name"] == head_blk):
                # 记下正极行最低的那一点（块底 vs 接点，取更低者）：负极行照它往下排
                _low = min([pyy + b[2] * s] + [pyy + p1[1] for p1 in (l + r)])
                pos_bottom = _low if pos_bottom is None else min(pos_bottom, _low)
            P = (pxx, pyy)
            places.append({"P": P, "s": s,
                           "bb": b, "name": it["name"],
                           "rot": _rot,
                           # 转过的负极端（180°）：块局部的“左列”在图上跑到右边，
                           # 所以进线/出线要对调，线才接在**朝链内**的那一侧接点上
                           # （不换的话线会从块的外侧兜过来、压在块身上）。
                           "outs": [(x + P[0], y + P[1]) for x, y in (l if _rot else r)],
                           "lins": [(x + P[0], y + P[1]) for x, y in (r if _rot else l)],
                           "right": P[0] + bw})
            prev_outs = places[-1]["outs"]
            right = P[0] + bw
            prev_name = it["name"]
        if kp < n_str and not pos_plug:
            log.append("⚠ 正极支线只有 %d 根、串数 %d：多出来的串没有正极支线"
                       "（把末端公头块填上，它顶一根）" % (kp, n_str))
        # 负极支线的根数：自动补齐时 = 负极行里钉在板子上的块数（头部的接头不算）
        _n_neg = ((len(hinsts) - neg_from - 1) if neg_from is not None else kn)
        if neg_feed and _n_neg < n_str and not neg_plug:
            log.append("⚠ 负极支线只有 %d 根、串数 %d" % (_n_neg, n_str))
        if match_h:
            log.append("线束缩放: " + ", ".join(
                "%s x%.3f" % (hinsts[i]["name"], scales[i]) for i in range(len(hinsts)))
                + "（所有线束块按“接点间距一致”缩放；板子出线点间距 %.2f）" % panel_span)
        return places

    def layout(gx, gy):
        """gx=板间净空, gy=串间净空（都用净空，pitch 由块宽算出来）。"""
        cells, abox = _place_array(tmpl, n_str, mw + gx, gy, dirn)
        cellmap = {(c["i"], c["s"]): c for c in cells}
        hp = place_harness(cellmap, abox)
        # 起始块（CBX）钉在阵列那一排，不参与“线束行”的定位
        head_i = [i for i, p in enumerate(hp)
                  if head_blk and p.get("name") == head_blk]
        row_i = [i for i in range(len(hp)) if i not in head_i]
        hbox = (_content_bbox([hinsts[i] for i in row_i], [hp[i] for i in row_i],
                              [hp[i]["s"] for i in row_i]) if row_i else None)
        hoff = (0.0, 0.0)
        if hbox:
            # 支线是“钉”在端子的 x 上的，所以线束整体不再横向居中，
            # 只把线束顶边挂到阵列下沿（净空 clear）下方。
            hoff = (0.0, abox[2] - clear - hbox[3])
            box = (min(abox[0], hbox[0] + hoff[0]), max(abox[1], hbox[1] + hoff[0]),
                   min(abox[2], hbox[2] + hoff[1]), max(abox[3], hbox[3] + hoff[1]))
        else:
            box = abox
        # 起始块：横向在阵列左边固定距离、纵向对齐阵列那一行的中线
        for i in head_i:
            b0 = hp[i]["bb"]; s0 = hp[i]["s"]
            # 单位空间里“阵列那一行”的中线就是 y=0，所以这里不减 hoff（它不跟着下移）
            hp[i]["y0"] = -(b0[2] + b0[3]) / 2.0 * s0
            box = (min(box[0], hp[i]["P"][0] + b0[0] * s0),
                   max(box[1], hp[i]["P"][0] + b0[1] * s0),
                   min(box[2], hp[i]["y0"] + b0[2] * s0),
                   max(box[3], hp[i]["y0"] + b0[3] * s0))
        return {"cells": cells, "abox": abox, "hoff": hoff, "box": box, "hp": hp}

    L = layout(gap_x, gap_y)
    pg(45, "阵列/线束排布完成")
    log.append("阵列: %d 串 x %d 块，%s，板间净空 %.1f / 串间净空 %.1f，内容 %.1f x %.1f" %
               (n_str, n_per, "串从左往右接" if dirn != "down" else "串从上往下叠",
                gap_x, gap_y, L["box"][1] - L["box"][0], L["box"][3] - L["box"][2]))

    # ---- 缩放（13.7）：可用区 = 画图区 左右各内缩 inset_x、上下各内缩 inset_y ----
    rect = frame_draw_rect(sec)
    fbx = rect or _records_bbox(group_entities(sec.get("ENTITIES", [])), bmap)
    k = 1.0
    if fbx:
        av_w = (fbx[1] - fbx[0]) * (1 - 2 * inset_x)
        av_h = (fbx[3] - fbx[2]) * (1 - 2 * inset_y)
        cw = L["box"][1] - L["box"][0]
        ch = L["box"][3] - L["box"][2]
        if cw > 1e-6 and ch > 1e-6:
            k = min(av_w / cw, av_h / ch)
            if not allow_up:
                k = min(k, 1.0)
            log.append("可用区 %.0f x %.0f，内容 %.0f x %.0f，k=%.4f%s" %
                       (av_w, av_h, cw, ch, k, "" if allow_up else "（只缩不放）"))
            if k < k_floor:
                gx_min, gy_min = mw * 0.15, mh * 0.15
                if gap_x > gx_min + 1e-9 or gap_y > gy_min + 1e-9:
                    log.append("k=%.3f 偏小，按 13.7 先把间距压到下限再算一次" % k)
                    gap_x, gap_y = min(gap_x, gx_min), min(gap_y, gy_min)
                    L = layout(gap_x, gap_y)
                    cw = L["box"][1] - L["box"][0]
                    ch = L["box"][3] - L["box"][2]
                    k = min(av_w / cw, av_h / ch)
                    if not allow_up:
                        k = min(k, 1.0)
                    log.append("压缩间距后: 板间净空 %.1f 串间净空 %.1f，k=%.4f" %
                               (gap_x, gap_y, k))
                if k < k_floor:
                    log.append("⚠ 装不进当前外框：%d 串 x %d 块（k<%.2f）在可用区里最多约 "
                               "%d 串 x %d 块，请减少串数/板数，或换更大的框"
                               % (n_str, n_per, k_floor,
                                  max(1, int(av_h / (k_floor * max(mh + gap_y, 1e-6)))),
                                  max(1, int(av_w / (k_floor * max(mw + gap_x, 1e-6))))))

    off = (0.0, 0.0)
    if fbx:
        off = ((fbx[0] + fbx[1]) / 2.0 - (L["box"][0] + L["box"][1]) / 2.0 * k,
               (fbx[2] + fbx[3]) / 2.0 - (L["box"][2] + L["box"][3]) / 2.0 * k)
    log.append("套用外框图: %s（画图区 %s，内容偏移 %.1f, %.1f）" %
               (os.path.basename(frame),
                ("%.0fx%.0f" % (fbx[1] - fbx[0], fbx[3] - fbx[2])) if fbx else "无",
                off[0], off[1]))
    pg(60, "缩放/定位完成")

    def F(p):
        """单位空间 -> 图纸坐标。"""
        return (p[0] * k + off[0], p[1] * k + off[1])

    th = max(0.5, label_r * (fbx[3] - fbx[2])) if fbx else 1.0
    # ---- 线号标注形式：CAD 原生线性标注（默认） / 老式 TEXT ----
    _dim_style = dim_style_name(sec)
    # 默认走**文字标注**：ZWCAD 2025 目前对本程序生成的 DIMENSION 会报
    # “无效或不完整的 DXF 输入 —— 图形被放弃”，等原生标注那条路验完再打开。
    _annot = (spec.get("annot") or "text").strip().lower()
    _dim_ok = (_annot != "text") and bool(_dim_style)
    dim_jobs = []           # [(块名, 块内容实体)]
    dim_reqs = []           # [(a, b, anchor, txt)] —— 并入失败时退回文字用
    dim_ent_pairs = []      # DIMENSION 实体的组（句柄等块定义并进来之后再发）
    if _annot != "text" and not _dim_style:
        log.append("⚠ 外框图里没有标注样式(DIMSTYLE)，线号退回文字标注")
    cell = {(c["i"], c["s"]): c for c in L["cells"]}

    def mod_pts(i, s):
        """(i,s) 那块在图纸上的：插入点 / 正极 / 负极 / 块中心。

        每块用自己的 geo：串首块的正极、串尾块的负极都是它自己块里的接点。
        """
        c = cell[(i, s)]
        P = F(c["P"])
        b = c["bb"]
        return ((P[0], P[1]),
                (P[0] + c["pos_l"][0] * k, P[1] + c["pos_l"][1] * k),
                (P[0] + c["neg_l"][0] * k, P[1] + c["neg_l"][1] * k),
                (P[0] + (b[0] + b[1]) / 2.0 * k, P[1] + (b[2] + b[3]) / 2.0 * k))

    handle = [maxh + 1]

    def nh():
        v = "%X" % handle[0]; handle[0] += 1; return v

    def blk(c, v):
        return (c + "\r\n" + str(v) + "\r\n").encode("utf-8")

    content = bytearray()

    def emit_insert(name, x, y, s):
        return emit_insert_rot(name, x, y, s, 0.0)

    def emit_insert_rot(name, x, y, s, rot):
        for c, v in [("0", "INSERT"), ("5", nh()), ("330", mspace or "0"),
                     ("100", "AcDbEntity"), ("8", "0"), ("100", "AcDbBlockReference"),
                     ("2", name),
                     ("10", "%.6f" % x), ("20", "%.6f" % y), ("30", "0.0"),
                     ("41", "%.6f" % s), ("42", "%.6f" % s),
                     ("43", "1.0"), ("50", "%.4f" % rot)]:
            content.extend(blk(c, v))

    def emit_wire(a, b):
        return emit_wire_c(a, b, 1)

    def emit_wire_c(a, b, col):
        """col: 1=红(正极那一路)  7=白(负极那一路)"""
        for c, v in [("0", "LINE"), ("5", nh()), ("330", mspace or "0"),
                     ("100", "AcDbEntity"), ("8", "WIRE"), ("62", str(col)),
                     ("100", "AcDbLine"),
                     ("10", "%.6f" % a[0]), ("20", "%.6f" % a[1]), ("30", "0.0"),
                     ("11", "%.6f" % b[0]), ("21", "%.6f" % b[1]), ("31", "0.0")]:
            content.extend(blk(c, v))

    def emit_label(x, y, txt):
        for c, v in [("0", "TEXT"), ("5", nh()), ("330", mspace or "0"),
                     ("100", "AcDbEntity"), ("8", "WIRE_LABEL"), ("100", "AcDbText"),
                     ("10", "%.6f" % x), ("20", "%.6f" % y), ("30", "0.0"),
                     ("40", "%.4f" % th), ("1", txt), ("50", "0.0")]:
            content.extend(blk(c, v))

    def emit_dim(a, b, anchor, txt):
        """CAD 原生“线性标注”：界线原点 = 这条线的两个接点，尺寸线过 CONN-Label 点。

        标注实体先攒在 dim_ents 里，等标注块定义并进外框之后再一起拼进去
        （块定义没进去的话，CAD 里什么都不显示）。
        """
        if not _dim_ok:
            return False
        ents, g = dim_geom(a, b, anchor, txt, th)
        if not g:
            return False
        idx = len(dim_jobs) + 1
        temp = "SLDDIM%04d" % idx                     # 临时文件名 = 打包时的块名
        final = "*D%04d" % (9000 + idx)               # 打包后改成 CAD 自己的匿名标注块名
        dim_jobs.append((temp, final, ents))
        dim_reqs.append((a, b, anchor, txt))
        dim_ent_pairs.append(dim_entity_pairs(final, _dim_style, a, b, anchor, txt, g, th))
        return True

    def emit_point(x, y, layer):
        """打连接点：POINT 实体放在 CONN_POS / CONN_NEG 层，供后续接线引用。"""
        for c, v in [("0", "POINT"), ("5", nh()), ("330", mspace or "0"),
                     ("100", "AcDbEntity"), ("8", layer), ("100", "AcDbPoint"),
                     ("10", "%.6f" % x), ("20", "%.6f" % y), ("30", "0.0")]:
            content.extend(blk(c, v))

    def emit_poly(pts):
        return emit_poly_c(pts, 1)

    def emit_poly_c(pts, col):
        """跨接线走折线：一条线一个实体，CAD 里看也是一根线。"""
        head = [("0", "LWPOLYLINE"), ("5", nh()), ("330", mspace or "0"),
                ("100", "AcDbEntity"), ("8", "WIRE"), ("62", str(col)),
                ("100", "AcDbPolyline"),
                ("90", str(len(pts))), ("70", "0")]
        for c, v in head:
            content.extend(blk(c, v))
        for x, y in pts:
            content.extend(blk("10", "%.6f" % x))
            content.extend(blk("20", "%.6f" % y))

    def poly_len(pts):
        return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
                   for i in range(len(pts) - 1))

    anchors = []          # 允许当连线端点的“锚点”：阵列端子 + 线束顶端

    for s in range(n_str):
        for i in range(n_per):
            P, pa, na, _c = mod_pts(i, s)
            emit_insert(cell[(i, s)]["name"], P[0], P[1], k)
            anchors += [pa, na]
            if i == 0:                 # 每串开头的正极 -> CONN_POS
                emit_point(pa[0], pa[1], "CONN_POS")
            if i == n_per - 1:         # 每串结束的负极 -> CONN_NEG
                emit_point(na[0], na[1], "CONN_NEG")
    log.append("已在每串首块正极打 CONN_POS、末块负极打 CONN_NEG（各 %d 个）" % n_str)

    hfinal = []
    for idx, it in enumerate(hinsts):
        hp = L["hp"][idx]
        sc = hp["s"] * k
        yy = hp.get("y0", hp["P"][1] + L["hoff"][1])
        P = F((hp["P"][0] + L["hoff"][0], yy))
        emit_insert_rot(it["name"], P[0], P[1], sc, hp.get("rot", 0.0))
        # 块里 CONN-Label 层的点 = 这个块指定的“标注落点”，换算到图纸坐标备用
        labs = [(x * sc + P[0], y * sc + P[1])
                for x, y, ly in _conn_points(bmap.get(it["name"], []), bmap)
                if "LABEL" in (ly or "").upper()]
        hfinal.append({"name": it["name"], "P": P, "s": sc,
                       "b": cl.bbox(it["prims"]), "labs": labs})

    def hcenter(h):
        return (h["P"][0] + (h["b"][0] + h["b"][1]) / 2.0 * h["s"],
                h["P"][1] + (h["b"][2] + h["b"][3]) / 2.0 * h["s"])

    def hpt(idx, key, i):
        p = L["hp"][idx][key][i]
        return F((p[0] + L["hoff"][0], p[1] + L["hoff"][1]))

    def label_on(a, b, txt):
        """标注放在两点连线的中点，再沿垂直方向偏一个字高（13.6）。"""
        dx, dy = b[0] - a[0], b[1] - a[1]
        d = math.hypot(dx, dy)
        mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        if d > 1e-9:
            nx, ny = -dy / d, dx / d
            if ny < 0:
                nx, ny = -nx, -ny
            mx += nx * th * 1.2
            my += ny * th * 1.2
        if not emit_dim(a, b, (mx, my), txt):
            emit_label(mx, my, txt)

    def seg(a, b):
        emit_wire(a, b)
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def seg_c(a, b, col):
        emit_wire_c(a, b, col)
        return math.hypot(a[0] - b[0], a[1] - b[1])

    # ---- 串内连线：第 i 块负极 -> 第 i+1 块正极（13.4） ----
    # 板与板之间只画线、不标线号（贴板时那里根本放不下字）
    if sw_in:
        for s in range(n_str):
            for i in range(n_per - 1):
                a = mod_pts(i, s)[2]
                b = mod_pts(i + 1, s)[1]
                d = seg(a, b)
                wires.append(("串%d 第%d块负极-第%d块正极" % (s + 1, i + 1, i + 2), awg_br, d))
    else:
        log.append("串内不画连线（组件块的图形本身就是贴着的；要画就把 string_wires 打开）")

    # ---- 线束内部连线（复用链算法算出来的配对） ----
    # 线号标在这里：块与块之间的连线上
    n_lab_done = 0

    def label_near(a, b, cands, txt):
        """优先用块里 CONN-Label 层的点当标注落点（取离连线中点最近的那个）；
        没有就退回原来的“中点 + 垂直偏一个字高”。
        标注形式默认是 CAD 原生线性标注（尺寸界线压在这条线的两个接点上、
        文字压在 CONN-Label 点上）；标注样式缺失或并入失败才退回 TEXT。"""
        nonlocal n_lab_done
        if cands:
            mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
            q = min(cands, key=lambda p: (p[0] - mx) ** 2 + (p[1] - my) ** 2)
            if emit_dim(a, b, q, txt):
                n_lab_done += 1
                return
            emit_label(q[0], q[1], txt)
            n_lab_done += 1
            return
        label_on(a, b, txt)

    for idx in range(1, len(hinsts)):
        if head_blk and (hinsts[idx - 1]["name"] == head_blk or hinsts[idx]["name"] == head_blk):
            continue      # 起始块（CBX）是独立摆在阵列左边的，不和线束链连线
        _a, _b = hinsts[idx - 1]["name"], hinsts[idx]["name"]
        _pa = (neg_from is not None and idx - 1 >= neg_from)
        _pb = (neg_from is not None and idx >= neg_from)
        if _pa != _pb:
            continue      # 正极那一行和负极那一行之间不连线
        # 颜色按“是不是负极那一行”判（不能按块名判：同一个块名可能两边都用）
        _neg_row = (neg_from is not None)
        _col = 7 if (_neg_row and (idx >= neg_from or idx - 1 >= neg_from)) else 1
        pr = _match_pairs(L["hp"][idx - 1]["outs"], L["hp"][idx]["lins"])
        first = None
        for (i, j) in pr:
            a = hpt(idx - 1, "outs", i)
            b = hpt(idx, "lins", j)
            d = seg_c(a, b, _col)
            wires.append(("%s - %s" % (hinsts[idx - 1]["name"], hinsts[idx]["name"]),
                          awg_main, d))
            if first is None:
                first = (a, b)
        # 一对块只打一个标注（一对块之间常常有 2 根线，逐根打会叠在一起）
        if first:
            label_near(first[0], first[1],
                       (hfinal[idx - 1].get("labs") or []) + (hfinal[idx].get("labs") or []),
                       awg_main)

    # ---- 跨接线（默认不画：正极支线不接板子） ----
    feeds = [h for h in hfinal if h["name"] in pos_names] if pos_names else []
    # 负极那一行 = 链尾自动补出来的那几块（按顺序对应第 1..n 串）
    nfeeds = ([hfinal[i] for i in range(neg_from, len(hfinal))]
              if neg_from is not None
              else ([h for h in hfinal if h["name"] in neg_names] if neg_names else []))
    # 名单填错（比如“正极支线”这种库里已经改名/不存在的名字）会让支线钉错位，
    # 甚至让公头被当成第一根支线钉到最左边——这里直接说清楚。
    chain_names = [h["name"] for h in hinsts]
    if neg_feed and neg_feed not in chain_names and neg_from is None:
        log.append("⚠ “负极支线块”填的 %s 不在线束链里" % neg_feed)
    if not link:
        log.append("跨接线：按你的要求不画（正极支线不接板子）；需要时打开“阵列↔线束 跨接线”")
    elif harness and pos_feed and not feeds:
        log.append("⚠ 线束里没有 %s，跨接线不画" % pos_feed)
    if link and feeds and len(feeds) < n_str:
        log.append("⚠ 正极支线只有 %d 个、串数 %d：多出来的串不画跨接线" % (len(feeds), n_str))
    if link and pos_feed and not (neg_feed or neg_plug):
        log.append("线束里没填负极支线块/末端母头块，负极跨接线不画")
    y_route = (L["abox"][2] - clear * 0.5) * k + off[1]

    def top_of(h):
        return (h["P"][0] + (h["b"][0] + h["b"][1]) / 2.0 * h["s"],
                h["P"][1] + h["b"][3] * h["s"])

    for s in range(min(n_str, len(feeds)) if link else 0):
        top = top_of(feeds[s])
        A = mod_pts(0, s)[1]
        anchors.append(top)
        p1, p2 = (A[0], y_route), (top[0], y_route)
        emit_poly_c([A, p1, p2, top], 1)          # 正极跨接线：红
        d = poly_len([A, p1, p2, top])
        # 标注落点：优先用这根支线块里 CONN-Label 层的点，没有才用折线中段上方
        lab = feeds[s].get("labs") or []
        label_near(p1, p2, lab, awg_main)
        wires.append(("串%d 正极跨接线 -> %s" % (s + 1, pos_feed), awg_main, d))
        if s >= len(nfeeds):
            continue
        top2 = top_of(nfeeds[s])
        B = mod_pts(n_per - 1, s)[2]
        anchors.append(top2)
        q1, q2 = (B[0], y_route), (top2[0], y_route)
        emit_poly_c([B, q1, q2, top2], 7)         # 负极跨接线：白
        d2 = poly_len([B, q1, q2, top2])
        emit_label((q1[0] + q2[0]) / 2.0, y_route - th * 2.2, awg_main)
        wires.append(("串%d 负极跨接线 -> %s" % (s + 1, neg_feed), awg_main, d2))

    # ---- 插入外框字节 + 自检 ----
    pg(80, "写入 DXF 字节")
    # 先把上一版手工画的内容（HAND_ 层）搬过来：程序画到 COM 端为止，
    # 剩下人接的那几根线，改了参数重新生成也不用重画。
    if keep_from and os.path.exists(keep_from):
        hand, miss = keep_hand_entities(keep_from, keep_prefix, mspace, nh, log)
        for c, v in hand:
            content.extend(blk(c, v))
        miss.discard("")
        if miss:
            log.append("⚠ 手工内容里引用了外框图里没有的块(可能不显示): "
                       + ", ".join(sorted(miss)))
    elif keep_from:
        log.append("⚠ 找不到要保留手工内容的文件: %s" % keep_from)

    # ---- 原生标注：先把标注块定义并进外框，成功了才把 DIMENSION 实体拼进去 ----
    if dim_jobs:
        _ok, _dlog, _paths = False, [], []
        try:
            for _nm, _fin, _ents in dim_jobs:
                _p = os.path.join(tempfile.gettempdir(), _nm + ".dxf")
                _paths.append(dim_block_file(_nm, _ents, _p))
            _base_bad = bp.verify(fb)          # 外框图自带的老毛病不算我们的
            fb2, _info = bp.pack_into(fb, _paths, _dlog)
            # 标注块按 CAD 自己的写法收尾：
            #   块名 *D<号码>；BLOCK 头 70=1（匿名）；
            #   BLOCK_RECORD 保持 70=0 并补 340/280/281（ZWCAD 自己写的 *D 块就是这样）。
            # 名字唯一，直接在这份字节上定点改；匹配不上就保持原样。
            for _nm, _fin, _ in dim_jobs:
                _nb = _fin.encode("utf-8")
                fb2 = fb2.replace(_nm.encode("utf-8"), _nb)
                # BLOCK 头：跟在 70 后面的是 10（基点）→ 只把这一处的 70 改成 1
                fb2 = re.sub(rb"(2\r\n" + re.escape(_nb) + rb"\r\n70\r\n)0\r\n(10\r\n)",
                             rb"\g<1>1\r\n\g<2>", fb2)
                # BLOCK_RECORD：70 后面直接是下一条记录 → 补 340/280/281，70 保持 0
                fb2 = re.sub(rb"(2\r\n" + re.escape(_nb) + rb"\r\n)70\r\n0\r\n(0\r\n)",
                             rb"\g<1>340\r\n0\r\n70\r\n0\r\n280\r\n1\r\n281\r\n0\r\n\g<2>",
                             fb2)
            _pb = [x for x in bp.verify(fb2, [f for _n, f, _e in dim_jobs])
                   if x not in _base_bad]
            if _pb:
                log.append("⚠ 原生标注块并入后结构检查没过，线号退回文字标注: "
                           + "; ".join(_pb))
            else:
                fb = fb2
                _ok = True
        except Exception as _ex:
            log.append("⚠ 原生标注生成失败(%s)，线号退回文字标注" % _ex)
        finally:
            for _p in _paths:
                try:
                    os.remove(_p)
                except OSError:
                    pass
        if _ok:
            # 打包进来的块句柄从“外框原最大句柄+1”开始，我们自己画的实体也用了同一段
            # 号段 → 会撞。把我们的实体整体挪到打包之后的空档里，再发标注实体。
            _fib = parse_sections_bytes(fb, "utf-8")[0]
            handle[0] = max_handle(_fib) + 1
            content[:] = renumber_handles(bytes(content), handle)
            for _pairs in dim_ent_pairs:
                _rec = [("0", "DIMENSION"), ("5", nh()), ("330", mspace or "0")]
                for _c, _v in _pairs[1:]:             # _pairs[0] 就是 ("0","DIMENSION")
                    _rec.append((_c, _v))
                for _c, _v in _rec:
                    content.extend(blk(_c, _v))
            log.append("线号标注: CAD 原生线性标注 %d 个（样式 %s，标注点=CONN-Label）"
                       % (len(dim_jobs), _dim_style))
        else:
            for _a, _b, _anchor, _txt in dim_reqs:      # 退回老式 TEXT 标注
                emit_label(_anchor[0], _anchor[1], _txt)
        log.extend([x for x in _dlog if "跳过" in x or "⚠" in x])

    out = _splice_entities(fb, content, handle[0])
    if out is None:
        return None, ["外框图无 ENTITIES 段"], wires
    try:
        sec2, _o2 = parse_sections_bytes(out, "utf-8")
        pts, bad = wires_check(sec2, anchors)
        if bad:
            log.append("⚠ 连线端点检查: %d 个端点没落在接点/锚点上 %s"
                       % (len(bad), ["(%.2f, %.2f)" % b for b in bad[:4]]))
        else:
            log.append("连线端点检查: 全部落在接点/锚点上"
                       "（图上 CONN 点 %d 个，阵列端子/线束顶端锚点 %d 个）"
                       % (len(pts), len(anchors)))
    except Exception as ex:
        log.append("连线端点检查失败: %s" % ex)
    missing = [n for n in ([module] if module else []) + ([m_first, m_mid, m_last] if seq_mode else []) + harness
               if n and n not in fr_blocks]
    if missing:
        log.append("⚠ 外框里没有这些块定义(可能不显示): " + ", ".join(missing))
    # 落地检查：把我们画的东西的实际范围和外框画图区比一下，超框要说出来
    try:
        s2, _o2 = parse_sections_bytes(out, "utf-8")
        ours = set(x for x in ([module] if module else []) +
                   ([m_first, m_mid, m_last] if seq_mode else []) + harness if x)
        keep = [e for e in group_entities(s2.get("ENTITIES", []))
                if _g1(e, "2") in ours
                or (_g1(e, "8") or "").upper() in ("WIRE", "WIRE_LABEL", "CONN_POS", "CONN_NEG")]
        cb2 = _records_bbox(keep, _blocks_map(s2))
        if cb2 and rect:
            inside = (cb2[0] >= rect[0] - 1e-6 and cb2[1] <= rect[1] + 1e-6
                      and cb2[2] >= rect[2] - 1e-6 and cb2[3] <= rect[3] + 1e-6)
            log.append("内容范围 x %.0f..%.0f  y %.0f..%.0f（画图区 %.0f..%.0f / %.0f..%.0f）：%s"
                       % (cb2[0], cb2[1], cb2[2], cb2[3], rect[0], rect[1], rect[2], rect[3],
                          "在外框内" if inside else "⚠ 超出外框了"))
    except Exception as ex:
        log.append("内容范围检查失败: %s" % ex)
    return out.decode("latin-1"), log, wires


def _unused_content_bbox(insts, places, scales):
    xs = []; ys = []
    for idx, it in enumerate(insts):
        s = scales[idx]; P = places[idx]["P"]; b = cl.bbox(it["prims"])
        xs += [P[0] + b[0] * s, P[0] + b[1] * s]
        ys += [P[1] + b[2] * s, P[1] + b[3] * s]
    return (min(xs), max(xs), min(ys), max(ys)) if xs else None


def build_chain_raw(chain, gap=40.0, match_span=True, show_len=True, frame=None):
    """返回 (dxf_text, log)。不展平：实体原样搬运。"""
    global _LAST_BLOCKS
    log = []
    insts = []
    for name in chain:
        path = os.path.join(ui.BLOCKS_DIR, name + ".dxf")
        if not os.path.exists(path):
            log.append("缺文件: " + name); continue
        sec, _ord = parse_sections(path)
        ents = group_entities(sec.get("ENTITIES", []))
        prims = cl.flatten(name)
        pts = [(p["x"], p["y"]) for p in ui.capture_points(name)]
        insts.append({"name": name, "path": path, "sec": sec, "ents": ents,
                      "prims": prims, "pts": pts})
    if not insts:
        return None, ["没有可用块"]

    # 缩放：把各块“右侧接点间距”对齐到几何平均(使最大缩放倍数最小)
    scales = [1.0] * len(insts)
    if match_span:
        spans = []
        for it in insts:
            r = cl.side_ports(it["prims"], it["pts"], "right")
            if len(r) >= 2:
                sp = r[0][1] - r[-1][1]
                if sp > 1e-6:
                    spans.append(sp)
        if spans:
            target = math.prod(spans) ** (1.0 / len(spans))
            for k, it in enumerate(insts):
                r = cl.side_ports(it["prims"], it["pts"], "right")
                if len(r) >= 2:
                    sp = r[0][1] - r[-1][1]
                    if sp > 1e-6:
                        scales[k] = target / sp

    def match_pairs(a, b):
        cand = sorted((abs(x[1] - y[1]), i, j)
                      for i, x in enumerate(a) for j, y in enumerate(b))
        pi, pj, res = set(), set(), []
        for d, i, j in cand:
            if i in pi or j in pj:
                continue
            pi.add(i); pj.add(j); res.append((i, j))
        return res

    # 计算插入点(与 build_chain 同算法；接点/包围盒用缩放后的值)
    places = []
    prev_outs = None; prev_right = None
    for idx, it in enumerate(insts):
        s = scales[idx]
        r = [(x * s, y * s) for x, y in cl.side_ports(it["prims"], it["pts"], "right")]
        l = [(x * s, y * s) for x, y in cl.side_ports(it["prims"], it["pts"], "left")]
        b = cl.bbox(it["prims"])
        bl = (b[0] * s, b[1] * s, b[2] * s, b[3] * s)
        if idx == 0:
            P = (0.0, 0.0)
        else:
            pr = match_pairs(prev_outs, l)
            offs = sorted(prev_outs[i][1] - l[j][1] for (i, j) in pr)
            off = offs[len(offs) // 2] if offs else 0.0
            P = (prev_right + gap - bl[0], off)
        outs = [(x + P[0], y + P[1]) for x, y in r]
        lins = [(x + P[0], y + P[1]) for x, y in l]
        places.append({"P": P, "s": s, "outs": outs, "lins": lins,
                       "right": P[0] + bl[1]})
        if idx > 0:
            pr2 = match_pairs(prev_outs, lins)
            n = len(pr2)
            log.append("%s -> %s : 连 %d 条线" % (insts[idx-1]["name"], it["name"], n))
            for (i, j) in pr2:
                a = prev_outs[i]; b2 = lins[j]
                L = ((a[0]-b2[0]) ** 2 + (a[1]-b2[1]) ** 2) ** 0.5
                log.append("  线%d: 长 %.2f" % (i+1, L))
        log.append("放块 %s 于 (%.2f, %.2f) 缩放 x%.4f" % (it["name"], P[0], P[1], s))
        prev_outs = outs; prev_right = places[-1]["right"]

    # === 组装：host = 外框图(若有) 或 第1个块；保留全部段 ===
    use_frame = bool(frame and os.path.exists(frame))
    host_path = frame if use_frame else insts[0]["path"]
    host, order = parse_sections(host_path)
    mspace = model_space_handle(host)
    counter = [max_handle(host) + 1]

    def next_handle():
        v = "%X" % counter[0]
        counter[0] += 1
        return v

    base_ents = group_entities(host.get("ENTITIES", []))
    off = (0.0, 0.0)
    if use_frame:
        cb = _content_bbox(insts, places, scales)
        fb = _records_bbox(base_ents, _blocks_map(host))
        if cb and fb:
            off = ((fb[0] + fb[1]) / 2 - (cb[0] + cb[1]) / 2,
                   (fb[2] + fb[3]) / 2 - (cb[2] + cb[3]) / 2)
        log.append("套用外框图: %s (内容偏移 %.1f, %.1f)" %
                   (os.path.basename(frame), off[0], off[1]))

    content = []
    for idx in range(len(insts)):
        s = scales[idx]; P = places[idx]["P"]
        if (not use_frame) and idx == 0 and abs(s - 1.0) < 1e-9:
            continue     # 实例0 用 base_ents 原样
        tx = P[0] + off[0]; ty = P[1] + off[1]
        for e in insts[idx]["ents"]:
            e2 = scale_entity(e, s) if abs(s - 1.0) > 1e-9 else e
            content.append(clone_entity(e2, tx, ty, next_handle(), mspace))
    ent_out = list(base_ents) + content

    for idx in range(1, len(insts)):
        pr = match_pairs(places[idx-1]["outs"], places[idx]["lins"])
        for (i, j) in pr:
            a = places[idx-1]["outs"][i]; b = places[idx]["lins"][j]
            a = (a[0] + off[0], a[1] + off[1])
            b = (b[0] + off[0], b[1] + off[1])
            ent_out.append([("0", "LINE"), ("5", next_handle()), ("330", mspace or "0"),
                            ("100", "AcDbEntity"), ("8", "WIRE"), ("100", "AcDbLine"),
                            ("10", "%.6f" % a[0]), ("20", "%.6f" % a[1]), ("30", "0"),
                            ("11", "%.6f" % b[0]), ("21", "%.6f" % b[1]), ("31", "0")])
            if show_len:
                L = ((a[0]-b[0]) ** 2 + (a[1]-b[1]) ** 2) ** 0.5
                h = max(4.0, gap * 0.15)
                ent_out.append([("0", "TEXT"), ("5", next_handle()), ("330", mspace or "0"),
                                ("100", "AcDbEntity"), ("8", "TEXT"), ("100", "AcDbText"),
                                ("10", "%.6f" % ((a[0]+b[0])/2)),
                                ("20", "%.6f" % ((a[1]+b[1])/2 + h * 1.3)), ("30", "0"),
                                ("40", "%.4f" % h), ("1", "%.1f" % L), ("50", "0")])

    # 图层 + 合并子块(用外框图时，所有块都算“外部”)
    src = insts if use_frame else insts[1:]
    desired = []
    for it in src:
        for rec in layer_records(it["sec"].get("TABLES", [])):
            nm = layer_name(rec)
            color = "7"
            for c, v in rec:
                if c == "62":
                    color = v
            desired.append((nm, color))
    desired += [("WIRE", "1"), ("TEXT", "7")]
    have = {layer_name(r) for r in layer_records(host.get("TABLES", []))}
    tables = add_layers(list(host.get("TABLES", [])), desired, have, next_handle)
    other_paths = []
    for it in src:
        if it["path"] != host_path and it["path"] not in other_paths:
            other_paths.append(it["path"])
    blocks_body = list(host.get("BLOCKS", []))
    blocks_body, tables = merge_blocks(blocks_body, tables, other_paths,
                                       next_handle, mspace, log)
    header = update_handseed(list(host.get("HEADER", [])), counter[0])
    _LAST_BLOCKS = _blocks_map({"BLOCKS": blocks_body})

    out = ""
    for name in order:
        if name == "HEADER":
            body = header
        elif name == "TABLES":
            body = tables
        elif name == "ENTITIES":
            body = ent_out
        elif name == "BLOCKS":
            body = blocks_body
        else:
            body = host[name]
        out += emit_section(name, body)
    out += "0\nEOF\n"
    return out, log, ent_out


def scale_entity(ent, s):
    """按类型做均匀缩放(正确处理 INSERT/SPLINE/ELLIPSE 等，避免破坏块引用)。"""
    t = ent[0][1] if ent and ent[0][0] == "0" else ""
    xy = ("10", "11", "12", "13", "14", "20", "21", "22", "23", "24")
    if t == "INSERT":
        # INSERT 必须同时缩插入点(10/20)和缩放因子(41/42/43)；
        # 若原本没写 41/42(默认=1)，要显式补 = s，否则块内容不会被放大 -> 错位
        out = []
        has = {"41": False, "42": False, "43": False}
        for c, v in ent:
            if c in xy or c in has:
                try:
                    v = "%.6f" % (float(v) * s)
                except (TypeError, ValueError):
                    pass
                if c in has:
                    has[c] = True
            out.append((c, v))
        add = []
        if not has["41"]:
            add.append(("41", "%.6f" % s))
        if not has["42"]:
            add.append(("42", "%.6f" % s))
        if add:
            pos = len(out)
            for idx, (c, v) in enumerate(out):
                if c == "30":
                    pos = idx + 1
            for idx, (c, v) in enumerate(out):
                if c == "20" and pos == len(out):
                    pos = idx + 1
            out[pos:pos] = add
        return out
    out = []
    for c, v in ent:
        if t in ("TEXT", "MTEXT", "ATTDEF"):
            do = c in xy or c == "40"
        elif t in ("CIRCLE", "ARC"):
            do = c in ("10", "20", "40")
        elif t == "ELLIPSE":
            do = c in ("10", "20", "11", "21")
        elif t == "SPLINE":
            do = c in ("10", "20", "11", "21")
        elif t in ("LWPOLYLINE", "POLYLINE", "VERTEX"):
            do = c in ("10", "20")
        else:
            do = c in xy
        if do:
            try:
                v = "%.6f" % (float(v) * s)
            except (TypeError, ValueError):
                pass
        out.append((c, v))
    return out


def _g1(rec, code):
    for c, v in rec:
        if c == code:
            return v
    return None


def _isnum(v):
    try:
        float(v); return True
    except (TypeError, ValueError):
        return False


def _gf(rec, code, default=0.0):
    v = _g1(rec, code)
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _lw_verts(rec):
    verts = []; x = y = b = None; started = False
    for c, v in rec:
        if c == "10":
            if started and x is not None and y is not None:
                verts.append((x, y, b))
            try: x = float(v)
            except (TypeError, ValueError): x = None
            y = None; b = None; started = True
        elif c == "20":
            try: y = float(v)
            except (TypeError, ValueError): y = None
        elif c == "42":
            try: b = float(v)
            except (TypeError, ValueError): b = None
    if started and x is not None and y is not None:
        verts.append((x, y, b))
    return verts


def _bulge_seg(p1, p2, b, segs=10):
    if not b or abs(b) < 1e-9:
        return [p2]
    theta = 4.0 * math.atan(b)
    dx = p2[0] - p1[0]; dy = p2[1] - p1[1]
    chord = math.hypot(dx, dy)
    if chord < 1e-9:
        return [p2]
    mx = (p1[0] + p2[0]) / 2.0; my = (p1[1] + p2[1]) / 2.0
    t = math.tan(theta / 2.0)
    if abs(t) < 1e-12:
        return [p2]
    d = (chord / 2.0) / t
    nx = -dy / chord; ny = dx / chord
    cx = mx + nx * d; cy = my + ny * d
    r = math.hypot(p1[0] - cx, p1[1] - cy)
    a1 = math.atan2(p1[1] - cy, p1[0] - cx)
    a2 = math.atan2(p2[1] - cy, p2[0] - cx)
    if theta > 0 and a2 < a1: a2 += 2 * math.pi
    if theta < 0 and a2 > a1: a2 -= 2 * math.pi
    out = []
    for k in range(1, segs + 1):
        a = a1 + (a2 - a1) * k / segs
        out.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return out


def _poly_points(verts, closed):
    if not verts:
        return []
    pts = [(verts[0][0], verts[0][1])]
    for i in range(len(verts) - 1):
        pts += _bulge_seg((verts[i][0], verts[i][1]),
                          (verts[i + 1][0], verts[i + 1][1]), verts[i][2])
    if closed and len(verts) > 1:
        pts += _bulge_seg((verts[-1][0], verts[-1][1]),
                          (verts[0][0], verts[0][1]), verts[-1][2])
    return pts


def _de_boor(u, ctrl, p, knots, n):
    k = p
    for i in range(p, n + 1):
        if knots[i] <= u < knots[i + 1]:
            k = i; break
    else:
        k = n
    d = [list(ctrl[j]) for j in range(k - p, k + 1)]
    for rr in range(1, p + 1):
        for j in range(p, rr - 1, -1):
            den = knots[j + 1 + k - rr] - knots[j + k - p]
            a = 0.0 if abs(den) < 1e-12 else (u - knots[j + k - p]) / den
            d[j] = [(1 - a) * d[j - 1][t] + a * d[j][t] for t in (0, 1)]
    return (d[p][0], d[p][1])


def _bspline(ctrl, degree, knots, segs=24):
    n = len(ctrl) - 1
    if n < 1:
        return list(ctrl)
    p = max(1, min(degree, n))
    if len(knots) < n + p + 2:
        knots = [0.0] * (p + 1) + [float(i) for i in range(1, n - p + 1)] + [float(n - p + 1)] * (p + 1)
    u0 = knots[p]; u1 = knots[n + 1]
    if u1 <= u0:
        return list(ctrl)
    return [_de_boor(u0 + (u1 - u0) * s / segs, ctrl, p, knots, n) for s in range(segs + 1)]


def _arc_pts(rec, segs=32):
    cx = _gf(rec, "10"); cy = _gf(rec, "20"); r = _gf(rec, "40")
    a0 = math.radians(_gf(rec, "50")); a1 = math.radians(_gf(rec, "51"))
    if a1 <= a0: a1 += 2 * math.pi
    return [(cx + r * math.cos(a0 + (a1 - a0) * k / segs),
             cy + r * math.sin(a0 + (a1 - a0) * k / segs)) for k in range(segs + 1)]


def _ellipse_pts(rec, segs=64):
    cx = _gf(rec, "10"); cy = _gf(rec, "20")
    mx = _gf(rec, "11"); my = _gf(rec, "21"); ratio = _gf(rec, "40", 1.0)
    a0 = _gf(rec, "41", 0.0); a1 = _gf(rec, "42", 2 * math.pi)
    nx = -my; ny = mx
    if a1 <= a0: a1 += 2 * math.pi
    return [(cx + mx * math.cos(a0 + (a1 - a0) * k / segs) + nx * ratio * math.sin(a0 + (a1 - a0) * k / segs),
             cy + my * math.cos(a0 + (a1 - a0) * k / segs) + ny * ratio * math.sin(a0 + (a1 - a0) * k / segs))
            for k in range(segs + 1)]


def _hatch_points(rec):
    pts = []; x = None
    for c, v in rec:
        if c == "10":
            try: x = float(v)
            except (TypeError, ValueError): x = None
        elif c == "20" and x is not None:
            try: pts.append((x, float(v)))
            except (TypeError, ValueError): pass
            x = None
    return pts


def entities_to_svg(records, width=1000, pad=16):
    """把原始 DXF 实体记录渲染成 SVG 预览（支持 LINE/CIRCLE/ARC/LWPOLYLINE/POLYLINE/SPLINE/ELLIPSE/HATCH/POINT/TEXT）。"""
    prims = []   # ("poly",pts,closed) / ("circle",cx,cy,r)
    i = 0
    while i < len(records):
        rec = records[i]
        t = rec[0][1] if rec and rec[0][0] == "0" else ""
        if t == "LINE":
            prims.append(("poly", [(_gf(rec, "10"), _gf(rec, "20")),
                                   (_gf(rec, "11"), _gf(rec, "21"))], False))
        elif t == "CIRCLE":
            prims.append(("circle", _gf(rec, "10"), _gf(rec, "20"), _gf(rec, "40")))
        elif t == "ARC":
            prims.append(("poly", _arc_pts(rec), False))
        elif t == "LWPOLYLINE":
            v = _lw_verts(rec)
            closed = str(_g1(rec, "70") or "0").strip().endswith("1")
            prims.append(("poly", _poly_points(v, closed), closed))
        elif t == "POLYLINE":
            verts = []; j = i + 1
            while j < len(records) and (records[j][0][1] if records[j] and records[j][0][0] == "0" else "") != "SEQEND":
                if records[j][0][1] == "VERTEX":
                    verts.append((_gf(records[j], "10"), _gf(records[j], "20"), _gf(records[j], "42", 0.0)))
                j += 1
            closed = str(_g1(rec, "70") or "0").strip().endswith("1")
            prims.append(("poly", _poly_points(verts, closed), closed))
            i = j
        elif t == "SPLINE":
            ctrl = []; x = None
            for c, v in rec:
                if c == "10":
                    try: x = float(v)
                    except (TypeError, ValueError): x = None
                elif c == "20" and x is not None:
                    try: ctrl.append((x, float(v)))
                    except (TypeError, ValueError): pass
                    x = None
            deg = int(_gf(rec, "71", 3))
            knots = [float(v) for c, v in rec if c == "40" and _isnum(v)]
            prims.append(("poly", _bspline(ctrl, deg, knots) if len(ctrl) >= 2 else ctrl, False))
        elif t == "ELLIPSE":
            prims.append(("poly", _ellipse_pts(rec), True))
        elif t == "HATCH":
            prims.append(("poly", _hatch_points(rec), False))
        elif t == "POINT":
            x = _gf(rec, "10"); y = _gf(rec, "20")
            prims.append(("poly", [(x - 1, y), (x + 1, y)], False))
            prims.append(("poly", [(x, y - 1), (x, y + 1)], False))
        elif t in ("TEXT", "MTEXT"):
            prims.append(("text", _gf(rec, "10"), _gf(rec, "20"),
                          _g1(rec, "1") or "", _gf(rec, "40", 2.0)))
        i += 1

    xs = []; ys = []
    for p in prims:
        if p[0] == "poly":
            for x, y in p[1]:
                xs.append(x); ys.append(y)
        elif p[0] == "circle":
            xs += [p[1] - p[3], p[1] + p[3]]; ys += [p[2] - p[3], p[2] + p[3]]
        elif p[0] == "text":
            xs.append(p[1]); ys.append(p[2])
    if not xs:
        return "<svg></svg>"
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    sc = (width - 2 * pad) / max(maxx - minx, 1e-6)
    H = int((maxy - miny) * sc) + 2 * pad
    parts = []
    for p in prims:
        if p[0] == "poly":
            pts = " ".join("%.1f,%.1f" % (pad + (x - minx) * sc, H - pad - (y - miny) * sc)
                           for x, y in p[1])
            if pts:
                parts.append('<polyline points="%s" fill="none" stroke="#1c1c1c" stroke-width="1"/>' % pts)
        elif p[0] == "circle":
            parts.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="#1c1c1c" stroke-width="1"/>'
                         % (pad + (p[1] - minx) * sc, H - pad - (p[2] - miny) * sc, p[3] * sc))
        elif p[0] == "text":
            sv = str(p[3]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if sv:
                parts.append('<text x="%.1f" y="%.1f" font-size="%.1f" fill="#1466c8" '
                             'text-anchor="middle">%s</text>'
                             % (pad + (p[1] - minx) * sc, H - pad - (p[2] - miny) * sc,
                                max(6.0, p[4] * sc), sv))
    return ('<svg viewBox="0 0 %d %d" style="width:100%%;background:#fff;'
            'border:1px solid #e6e8ee;border-radius:10px">' % (width, H)
            + "".join(parts) + "</svg>")


# ---- INSERT 递归解析(预览) ----
_LAST_BLOCKS = {}


def _blocks_map(sec):
    ents = group_entities(sec.get("BLOCKS", []))
    out = {}
    i = 0
    while i < len(ents):
        if ents[i] and ents[i][0][1] == "BLOCK":
            nm = _g1(ents[i], "2")
            members = []
            j = i + 1
            while j < len(ents) and ents[j][0][1] != "ENDBLK":
                members.append(ents[j]); j += 1
            if nm:
                out[nm] = members
            i = j
        i += 1
    return out


def _matmul(P, M):
    a, b, c, d, e, f = P
    A, B, C, D, E, F = M
    return (a * A + c * B, b * A + d * B, a * C + c * D, b * C + d * D,
            a * E + c * F + e, b * E + d * F + f)


def _apply(m, x, y):
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def _tf(pts, m):
    return [_apply(m, x, y) for (x, y) in pts]


def _closed(rec):
    try:
        return (int(_gf(rec, "70", 0)) & 1) == 1
    except (TypeError, ValueError):
        return False


def _prim_list(records, blocks, mtx, depth, out):
    if depth > 8:
        return
    i = 0
    while i < len(records):
        rec = records[i]
        t = rec[0][1] if rec and rec[0][0] == "0" else ""
        # 每条图元都带上“图层 + 实体颜色(62)”。重画到 CAD 时要按原样设回去，
        # 不然线会全挤到 0 层、颜色全丢（“直接画到 CAD”那条路踩过这个坑）。
        lay = _g1(rec, "8") or ""
        try:
            col = int(_gf(rec, "62", 0))
        except (TypeError, ValueError):
            col = 0
        if t == "INSERT":
            nm = _g1(rec, "2")
            px = _gf(rec, "10"); py = _gf(rec, "20")
            sx = _gf(rec, "41", 1.0) or 1.0
            sy = _gf(rec, "42", 1.0) or 1.0
            rr = math.radians(_gf(rec, "50", 0.0))
            T = (1, 0, 0, 1, px, py)
            R = (math.cos(rr), math.sin(rr), -math.sin(rr), math.cos(rr), 0, 0)
            S = (sx, 0, 0, sy, 0, 0)
            child = _matmul(mtx, _matmul(_matmul(T, R), S))
            if nm in blocks:
                _prim_list(blocks[nm], blocks, child, depth + 1, out)
            i += 1
            continue
        if t == "DIMENSION":
            # CAD 原生标注：画它引用的那块（块里就是尺寸线/尺寸界线/箭头/文字）。
            # 预览（界面上的 SVG、导出的 PNG）以前不认 DIMENSION，改完线号标注
            # 之后预览就“少了一块”——这里补上。
            nm = _g1(rec, "2")
            if nm in blocks:
                px = _gf(rec, "12"); py = _gf(rec, "22")      # 块插入点（一般 0,0）
                _prim_list(blocks[nm], blocks, _matmul(mtx, (1, 0, 0, 1, px, py)),
                           depth + 1, out)
            i += 1
            continue
        if t == "LINE":
            out.append(("poly", _tf([(_gf(rec, "10"), _gf(rec, "20")),
                                     (_gf(rec, "11"), _gf(rec, "21"))], mtx), False, lay, col))
        elif t == "CIRCLE":
            cx, cy = _apply(mtx, _gf(rec, "10"), _gf(rec, "20"))
            out.append(("circle", cx, cy, _gf(rec, "40") * math.hypot(mtx[0], mtx[1]), lay, col))
        elif t == "ARC":
            out.append(("poly", _tf(_arc_pts(rec), mtx), False, lay, col))
        elif t == "LWPOLYLINE":
            cl = _closed(rec)
            out.append(("poly", _tf(_poly_points(_lw_verts(rec), cl), mtx), cl, lay, col))
        elif t == "POLYLINE":
            verts = []; j = i + 1
            while j < len(records) and (records[j][0][1] if records[j] and records[j][0][0] == "0" else "") != "SEQEND":
                if records[j][0][1] == "VERTEX":
                    verts.append((_gf(records[j], "10"), _gf(records[j], "20"), _gf(records[j], "42", 0.0)))
                j += 1
            cl = _closed(rec)
            out.append(("poly", _tf(_poly_points(verts, cl), mtx), cl, lay, col))
            i = j
        elif t == "SPLINE":
            ctrl = []; x = None
            for c, v in rec:
                if c == "10":
                    try: x = float(v)
                    except (TypeError, ValueError): x = None
                elif c == "20" and x is not None:
                    try: ctrl.append((x, float(v)))
                    except (TypeError, ValueError): pass
                    x = None
            deg = int(_gf(rec, "71", 3))
            knots = [float(v) for c, v in rec if c == "40" and _isnum(v)]
            pts = _bspline(ctrl, deg, knots) if len(ctrl) >= 2 else ctrl
            out.append(("poly", _tf(pts, mtx), False, lay, col))
        elif t == "ELLIPSE":
            out.append(("poly", _tf(_ellipse_pts(rec), mtx), True, lay, col))
        elif t == "HATCH":
            out.append(("poly", _tf(_hatch_points(rec), mtx), False, lay, col))
        elif t == "POINT":
            x, y = _apply(mtx, _gf(rec, "10"), _gf(rec, "20"))
            out.append(("poly", [(x - 1, y), (x + 1, y)], False, lay, col))
            out.append(("poly", [(x, y - 1), (x, y + 1)], False, lay, col))
        elif t in ("TEXT", "MTEXT"):
            x, y = _apply(mtx, _gf(rec, "10"), _gf(rec, "20"))
            out.append(("text", x, y, _g1(rec, "1") or "", _gf(rec, "40", 2.0), lay, col))
        i += 1


def entities_to_svg(records, blocks=None, width=1000, pad=16):
    """把原始 DXF 实体记录渲染成 SVG 预览（解析 INSERT 递归）。"""
    if blocks is None:
        blocks = _LAST_BLOCKS
    prims = []
    _prim_list(records, blocks, (1, 0, 0, 1, 0, 0), 0, prims)
    xs = []; ys = []
    for p in prims:
        if p[0] == "poly":
            for x, y in p[1]:
                xs.append(x); ys.append(y)
        elif p[0] == "circle":
            xs += [p[1] - p[3], p[1] + p[3]]; ys += [p[2] - p[3], p[2] + p[3]]
        elif p[0] == "text":
            xs.append(p[1]); ys.append(p[2])
    if not xs:
        return "<svg></svg>"
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    sc = (width - 2 * pad) / max(maxx - minx, 1e-6)
    H = int((maxy - miny) * sc) + 2 * pad
    def X(x): return pad + (x - minx) * sc
    def Y(y): return H - pad - (y - miny) * sc
    parts = []
    for p in prims:
        if p[0] == "poly":
            pts = " ".join("%.1f,%.1f" % (X(x), Y(y)) for x, y in p[1])
            if pts:
                parts.append('<polyline points="%s" fill="none" stroke="#1c1c1c" stroke-width="1"/>' % pts)
        elif p[0] == "circle":
            parts.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="#1c1c1c" stroke-width="1"/>'
                         % (X(p[1]), Y(p[2]), p[3] * sc))
        elif p[0] == "text":
            sv = str(p[3]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if sv:
                parts.append('<text x="%.1f" y="%.1f" font-size="%.1f" fill="#1466c8" text-anchor="middle">%s</text>'
                             % (X(p[1]), Y(p[2]), max(6.0, p[4] * sc), sv))
    return ('<svg viewBox="0 0 %d %d" style="width:100%%;background:#fff;'
            'border:1px solid #e6e8ee;border-radius:10px">' % (width, H)
            + "".join(parts) + "</svg>")


if __name__ == "__main__":
    chain = sys.argv[1:] or ["CU-AI", "CU-AI"]
    text, log, ents = build_chain_raw(chain, 60, True, True)
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    os.makedirs(base, exist_ok=True)
    if text:
        p = os.path.join(base, "raw_test.dxf")
        open(p, "w", encoding="latin-1", newline="").write(text)
        print("saved:", p)
    svgp = os.path.join(base, "raw_test.svg")
    open(svgp, "w", encoding="utf-8").write(entities_to_svg(ents))
    print("svg:", svgp)
    for l in log:
        print(l)
