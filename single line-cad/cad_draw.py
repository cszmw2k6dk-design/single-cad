#!/usr/bin/env python3
"""
cad_draw.py -- 把生成好的 DXF 内容“回放”进 CAD（ZWCAD COM 直画）

思路：布局算法和自检**一行都不动**，仍然先出 DXF；然后把这份 DXF 的模型空间内容
按实体逐个用 COM 画进 CAD。坐标是同一套，所以画出来的位置和 DXF 里完全一致。

块：INSERT 引用的块如果在目标图里没有，就用外框图/块库里的块定义（**展平**后的图元）
     在目标图里现造一个同名块定义，再 INSERT 它。

实测过的 ZWCAD 2026 差异：InsertBlock 是 7 个参数（多一个 Zscale），AutoCAD 是 5 个；
其余 AddLine/AddCircle/AddArc/AddText/AddPoint/AddLightWeightPolyline/Blocks.Add/Layers.Add
都和 AutoCAD 一致。
"""

import math
import os
import time

import wiring_raw as wr

try:
    import pythoncom
    from win32com.client import VARIANT
    import win32com.client
    HAVE_COM = True
except ImportError:
    HAVE_COM = False

PROGIDS = ("ZWCAD.Application", "ZWCAD.Application.2026", "AutoCAD.Application")

# 我们自己画的内容只在这几个层上；外框图自带的实体在别的层，别把它们再画一遍。
OUR_LAYERS = ("WIRE", "WIRE_LABEL", "CONN_POS", "CONN_NEG")


def pt(x, y):
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, (float(x), float(y), 0.0))


def flat2(seq):
    """把 [(x,y), ...] 变成 COM 要的扁平数组。"""
    vals = []
    for x, y in seq:
        vals.append(float(x)); vals.append(float(y))
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, tuple(vals))


def connect(visible=True, log=None):
    log = log if log is not None else []
    if not HAVE_COM:
        log.append("⚠ 画到 CAD 需要 pywin32：装一个 pip install pywin32")
        return None
    pythoncom.CoInitialize()
    last = None
    for pid in PROGIDS:
        try:
            app = win32com.client.Dispatch(pid)
            if visible:
                app.Visible = True
            log.append("已连上 CAD: %s（版本 %s）" % (pid, getattr(app, "Version", "?")))
            return app
        except Exception as ex:
            last = ex
    log.append("⚠ 连不上 CAD（ZWCAD 装了没 / 是不是被权限挡住）: %s" % last)
    return None


def find_doc(app, path, open_if_missing=True):
    want = os.path.basename(path).lower()
    try:
        for d in app.Documents:
            if (d.Name or "").lower() == want:
                return d, False
    except Exception:
        pass
    if not open_if_missing:
        return None, False
    return app.Documents.Open(path), True


def insert_block(space, x, y, name, sx=1.0, sy=1.0, rot=0.0):
    """ZWCAD 7 参数、AutoCAD 5 参数，都试一遍。"""
    last = None
    for args in ((pt(x, y), name, sx, sy, 1.0, rot),
                 (pt(x, y), name, sx, sy, 1.0, rot, ""),
                 (pt(x, y), name, sx, sy, rot)):
        try:
            return space.InsertBlock(*args)
        except Exception as ex:
            last = ex
    raise last


def ensure_layer(doc, name):
    if not name:
        return
    try:
        doc.Layers.Item(name)
    except Exception:
        try:
            doc.Layers.Add(name)
        except Exception:
            pass


def has_block(doc, name):
    try:
        doc.Blocks.Item(name)
        return True
    except Exception:
        return False


def header_nums(sec, names):
    """从 DXF 表头里取几个系统变量（如 $PDMODE / $PDSIZE）的数值。"""
    want = {n.upper() for n in names}
    out, cur = {}, None
    for c, v in (sec.get("HEADER", []) or []):
        if c == "9":
            cur = str(v).strip().upper()
            continue
        if cur in want:
            try:
                out[cur] = float(v)
            except (TypeError, ValueError):
                pass
            cur = None
    return out


def match_point_style(doc, sec, log=None):
    """把 DXF 表头里的“点怎么显示”抄到 CAD 里。

    DXF 里 $PDMODE/$PDSIZE 决定 POINT 画出来是什么样子；用 COM 画到 CAD 时，
    点显示的是**目标图自己的设置**，两个值不一样时，同一个连接点在 DXF 里是
    一个小点、在 CAD 里却变成十字光标。这里按 DXF 的值对齐，两边看着就一致。
    """
    hv = header_nums(sec, ("$PDMODE", "$PDSIZE", "$PDSTYLE"))
    got = []
    for var, key in (("PDMODE", "$PDMODE"), ("PDSIZE", "$PDSIZE"),
                     ("PDSTYLE", "$PDSTYLE")):
        if key not in hv:
            continue
        try:
            doc.SetVariable(var, hv[key])
            got.append("%s=%g" % (var, hv[key]))
        except Exception:
            pass
    if got and log is not None:
        log.append("连接点(POINT)显示按 DXF 对齐：" + "、".join(got))


def draw_prims(space, prims):
    """把展平后的图元画进某个 Block / ModelSpace。返回画了几个。

    图元自带 (图层, 颜色)，画的时候要设回去——否则块里的线全挤到 0 层、颜色全丢。
    """
    n = 0

    def style(o, lay, col):
        try:
            if lay:
                o.Layer = lay
        except Exception:
            pass
        try:
            o.color = col if col else 256      # 256 = 随层
        except Exception:
            pass

    for p in prims:
        lay, col = prim_style(p)
        try:
            if p[0] == "poly":
                pts = p[1]
                if len(pts) < 2:
                    continue
                o = space.AddLightWeightPolyline(flat2(pts))
                if p[2]:
                    o.Closed = True
            elif p[0] == "circle":
                o = space.AddCircle(pt(p[1], p[2]), float(p[3]))
            elif p[0] == "text":
                if not str(p[3]).strip():
                    continue
                o = space.AddText(str(p[3]), pt(p[1], p[2]), max(float(p[4]), 0.1))
            else:
                continue
            style(o, lay, col)
            n += 1
        except Exception:
            pass
    return n


def prim_style(p):
    """取图元的 (图层, 颜色)。

    注意：三种图元的字段位置都不一样 ——
      poly   : (poly, 点表, 闭合, 层, 色)          -> 3/4
      circle : (circle, cx, cy, 半径, 层, 色)      -> 4/5   （p[3] 是半径！）
      text   : (text, x, y, 文字, 字高, 层, 色)    -> 5/6
    取错字段会抛异常，结果就是“块建出来了但是空的”。
    """
    if p[0] == "text":
        return (p[5] if len(p) > 5 else ""), (p[6] if len(p) > 6 else 0)
    if p[0] == "circle":
        return (p[4] if len(p) > 4 else ""), (p[5] if len(p) > 5 else 0)
    return (p[3] if len(p) > 3 else ""), (p[4] if len(p) > 4 else 0)


def ensure_block(doc, name, bmap, log, refresh=True):
    """目标图里没有这个块，就用展平图元现造一个。返回 (是否可用, 画了几个图元)。

    refresh=True（画到 CAD 的默认）：图里已经有同名块时**删掉重建**。
    为什么必须重建：画到 CAD 是“按这次生成的 DXF 重画一遍”，图里那个同名块往往是
    上一次生成留下的、内容已经和这次不一样了。以前这里是“已存在就跳过”，于是
    批量连画几张时，第二张以后的图直接沿用了第一次的块内容 —— 这就是
    “单独画一张正常、连着画几张就不对”的根子。
    """
    if not name:
        return False, 0
    recs = bmap.get(name)
    if not recs:
        log.append("⚠ 块 %s 在图里和 DXF 里都没有定义，跳过" % name)
        return False, 0
    prims = []
    wr._prim_list(recs, bmap, (1, 0, 0, 1, 0, 0), 0, prims)
    if has_block(doc, name):
        if refresh and not str(name).startswith("*"):
            try:
                doc.Blocks.Item(name).Delete()
                log.append("块 %s 已存在：已删掉重建（保证和这次的 DXF 一模一样）" % name)
            except Exception as ex:
                log.append("⚠ 重建块 %s 失败(%s)：沿用图里已有的那份，位置对但内容可能是旧的"
                           % (name, ex))
                return True, 0
        else:
            # 不重建时：块已存在就直接用。但如果它是**空的**（上一次画到一半失败
            # 留下的），后面每次都会“因为已存在而跳过”，那个块就永远是空的、
            # INSERT 什么都不显示 —— 所以空块还是要把内容补上。
            try:
                blk = doc.Blocks.Item(name)
                if blk.Count == 0 and prims:
                    for lay in sorted({prim_style(p)[0] for p in prims if prim_style(p)[0]}):
                        ensure_layer(doc, lay)
                    n = draw_prims(blk, prims)
                    n += add_dims(blk, doc, dims_in_records(recs), log)
                    log.append("块 %s 已存在但是空的，补画了 %d 个图元" % (name, n))
                    return True, n
            except Exception as ex:
                log.append("⚠ 检查已有块 %s 失败: %s" % (name, ex))
            return True, 0
    try:
        blk = doc.Blocks.Add(pt(0, 0), name)
    except Exception as ex:
        log.append("⚠ 建块 %s 失败: %s" % (name, ex))
        return False, 0
    # 块里用到的图层得先在图上存在，不然颜色/线型会丢
    for lay in sorted({prim_style(p)[0] for p in prims if prim_style(p)[0]}):
        ensure_layer(doc, lay)
    n = draw_prims(blk, prims)
    n += add_dims(blk, doc, dims_in_records(recs), log)   # 块里的线号标注
    log.append("现造块定义 %s（展平后 %d 个图元；原来是嵌套块/样条线的话会变成折线）" % (name, n))
    return True, n


def dims_in_records(recs):
    """从一组记录里挑出我们的线号标注（DIMENSION 实体），返回
    [(界线点1, 界线点2, 尺寸线位置, 文字位置, 文字), ...]。

    模型空间（replay）和块定义里（合图时每张图是一个块）都用这一套，
    保证 CAD 里建的标注和 DXF 里写的是同一批点。
    """
    out = []
    for e in recs:
        if not e or e[0] != ("0", "DIMENSION"):
            continue
        if (wr._g1(e, "8") or "").upper() != "WIRE_LABEL":
            continue
        out.append(((wr._gf(e, "13"), wr._gf(e, "23")),
                    (wr._gf(e, "14"), wr._gf(e, "24")),
                    (wr._gf(e, "10"), wr._gf(e, "20")),
                    (wr._gf(e, "11"), wr._gf(e, "21")),
                    (wr._g1(e, "1") or "").strip()))
    return out


def add_dims(space, doc, dims, log):
    """按 (界线点1, 界线点2, 尺寸线位置, 文字位置, 文字) 建 CAD 原生对齐标注。

    space 可以是模型空间，也可以是块定义（合图时每张图是一个块，标注在块里）。
    返回建了几个。文字用的是**线号**（和 DXF 里一致），不是量出来的长度。
    """
    n = 0
    for (o1, o2, dpos, tpos, txt) in dims:
        try:
            d = space.AddDimAligned(pt(o1[0], o1[1]), pt(o2[0], o2[1]),
                                    pt(dpos[0], dpos[1]))
            # 样式：按用户图纸的格式用 Voltage（注释性 1:1、文字样式 Voltage）
            for _st in ("Voltage", "ISO-25", "Standard"):
                try:
                    d.StyleName = _st
                    break
                except Exception:
                    pass
            for _attr, _val in (("TextHeight", 8.0), ("TextGap", 0.1),
                                ("TextStyle", "Voltage"), ("TextColor", 5)):
                try:
                    setattr(d, _attr, _val)
                except Exception:
                    pass
            _ok = False
            if txt:
                try:
                    d.TextOverride = txt      # 尺寸线上写线号
                    _ok = True
                except Exception:
                    _ok = False
            if txt and not _ok:               # 改不了文字就单独补一个文字，别丢线号
                try:
                    o = space.AddText(txt, pt(tpos[0], tpos[1]), 8.0)
                    o.Layer = "WIRE_LABEL"
                except Exception:
                    pass
            try:
                if float(d.ArrowheadSize) < 3.0:
                    d.ArrowheadSize = 6.6
            except Exception:
                pass
            ensure_layer(doc, "DIM")
            try:
                d.Layer = "DIM"
            except Exception:
                pass
            n += 1
        except Exception as ex:
            log.append("⚠ 加线号标注失败: %s" % ex)
    return n


def replay(doc, dxf_path, log, only_blocks=None, only_layers=OUR_LAYERS,
           progress=None, sheet_names=None, refresh_blocks=True):
    """把 dxf_path 里“我们生成的那部分”画进 doc。返回统计。

    only_blocks：只回放块名在这里面的 INSERT（外框图自己的块不重画）。
    only_layers：只回放这些层上的线/文字/点。
    refresh_blocks：同名块已存在时删掉重建（DXF 才是准的，见 ensure_block）。
    """
    def pg(pct, stage):
        if progress:
            try:
                progress(pct, stage)
            except Exception:
                pass

    LAY = tuple(x.upper() for x in only_layers) if only_layers else None
    sec, _o = wr.parse_sections_text(wr.read_dxf_text(dxf_path))
    bmap = wr._blocks_map(sec)
    recs = wr.group_entities(sec.get("ENTITIES", []))
    ms = doc.ModelSpace
    stat = {"INSERT": 0, "LINE": 0, "LWPOLYLINE": 0, "TEXT": 0, "POINT": 0,
            "ARC": 0, "CIRCLE": 0, "skip": 0, "blk_prim": 0,
            "DIM": 0, "LEADER": 0}
    # 线号标注：**DXF 里是什么点就用什么点**。
    # 以前这里按“离标注最近的那根线 / 离端点最近的 CONN-Label 点”重新算一遍，
    # 于是同一根线跑两次可能挑到不同块的点、甚至挑到隔壁行/隔壁张的线 —— CAD 里
    # 看着就是标注点乱跳。现在只读 DXF（wiring_raw 已经算好：落点=连线中点+
    # 统一偏移，界线两端=这根线自己的接点），画到 CAD 和 DXF 一字不差。
    our_dims = dims_in_records(recs)
    made = set()
    _sheets = set(sheet_names or [])
    _n_sheet, _t_sheet, _t_all = 0, 0.0, time.time()
    pg(94, "正在画 %d 个实体（块定义/连线/文字）…" % len(recs))
    _n_done = 0
    for e in recs:
        if not e:
            continue
        t = e[0][1]
        lay = wr._g1(e, "8") or "0"
        if t == "INSERT":
            if only_blocks is not None and (wr._g1(e, "2") or "") not in only_blocks:
                continue
        elif t == "DIMENSION":
            continue                  # 我们自己的标注到最后统一用 CAD 原生标注建
        elif LAY is not None and lay.upper() not in LAY:
            continue
        try:
            if t == "INSERT":
                nm = wr._g1(e, "2") or ""
                _t0 = time.time() if nm in _sheets else 0.0
                if nm not in made:
                    ok, np = ensure_block(doc, nm, bmap, log, refresh=refresh_blocks)
                    stat["blk_prim"] += np
                    made.add(nm)
                    if not ok:
                        stat["skip"] += 1
                        continue
                # 关键：DXF 里的 50 组码是**角度**，而 COM 的 InsertBlock 要**弧度**。
                # 以前把角度直接当弧度传进去（180 变成 180 弧度 = 10313°），
                # 于是“负极那一行/公头母头”这些带旋转的块在 CAD 里全是乱转的。
                o = insert_block(ms, wr._gf(e, "10"), wr._gf(e, "20"), nm,
                                 wr._gf(e, "41", 1.0) or 1.0,
                                 wr._gf(e, "42", 1.0) or 1.0,
                                 math.radians(wr._gf(e, "50", 0.0)))
                o.Layer = lay
                stat["INSERT"] += 1
                if _t0:                       # 这一张画完了：报一下用时
                    _n_sheet += 1
                    _el = time.time() - _t0
                    _t_sheet += _el
                    log.append("画到 CAD：第 %d/%d 张 %s 画完，用时 %.1f 秒"
                               % (_n_sheet, len(_sheets), nm, _el))
                    pg(94 + min(3, _n_sheet), "第 %d/%d 张 %s 画完（%.1f 秒）"
                       % (_n_sheet, len(_sheets), nm, _el))
            elif t == "LINE":
                o = ms.AddLine(pt(wr._gf(e, "10"), wr._gf(e, "20")),
                               pt(wr._gf(e, "11"), wr._gf(e, "21")))
                o.Layer = lay
                _c = int(wr._gf(e, "62", 0))
                if _c:
                    try:
                        o.color = _c          # 正极红(1) / 负极白(7)，跟 DXF 里一致
                    except Exception:
                        pass
                stat["LINE"] += 1
            elif t == "LWPOLYLINE":
                v = [(a[0], a[1]) for a in wr._lw_verts(e)]
                if len(v) < 2:
                    continue
                o = ms.AddLightWeightPolyline(flat2(v))
                if (int(wr._gf(e, "70", 0)) & 1) == 1:
                    o.Closed = True
                o.Layer = lay
                _c = int(wr._gf(e, "62", 0))
                if _c:
                    try:
                        o.color = _c
                    except Exception:
                        pass
                stat["LWPOLYLINE"] += 1
            elif t == "POINT":
                o = ms.AddPoint(pt(wr._gf(e, "10"), wr._gf(e, "20")))
                o.Layer = lay
                stat["POINT"] += 1
            elif t in ("TEXT", "MTEXT"):
                txt = (wr._g1(e, "1") or "").strip()
                if not txt:
                    continue
                o = ms.AddText(txt, pt(wr._gf(e, "10"), wr._gf(e, "20")),
                               max(wr._gf(e, "40", 2.5), 0.1))
                o.Layer = lay
                stat["TEXT"] += 1
            elif t == "ARC":
                o = ms.AddArc(pt(wr._gf(e, "10"), wr._gf(e, "20")), wr._gf(e, "40"),
                              wr._gf(e, "50"), wr._gf(e, "51"))
                o.Layer = lay
                stat["ARC"] += 1
            elif t == "CIRCLE":
                o = ms.AddCircle(pt(wr._gf(e, "10"), wr._gf(e, "20")), wr._gf(e, "40"))
                o.Layer = lay
                stat["CIRCLE"] += 1
            else:
                stat["skip"] += 1
        except Exception as ex:
            stat["skip"] += 1
            log.append("⚠ 画 %s 失败: %s" % (t, ex))
        _n_done += 1
        if _n_done % 20 == 0:
            pg(94 + min(3, _n_done * 3 // max(1, len(recs))),
               "正在画实体 %d/%d…" % (_n_done, len(recs)))

    # ---- 线号标注：按 DXF 里的点建 CAD 原生标注（坐标一模一样，不再重算）----
    # 只有当这张图里**真的有点**时才去动 PDMODE/PDSIZE（图里没点就别改用户的设置）
    if any(e and e[0][1] == "POINT" for e in recs):
        match_point_style(doc, sec, log)
    if our_dims:
        pg(97, "正在加线号标注（%d 个）…" % len(our_dims))
        stat["DIM"] += add_dims(ms, doc, our_dims, log)
    if _n_sheet:                      # 合图：把每张的用时汇总一下
        log.append("画到 CAD：%d 张图纸，纯画图用时 %.1f 秒（含标注共 %.1f 秒）"
                   % (_n_sheet, _t_sheet, time.time() - _t_all))
    return stat


def clear_ours(doc, blocks, layers, log=None):
    """删掉上一次程序画的内容：我们那几层上的实体 + 这几个块的 INSERT。

    只碰这两类，手工层(HAND_*)和别人画的东西不动。返回删了几个。
    """
    lay = set(x.upper() for x in (layers or ()))
    blk = set(blocks or ())
    ms = doc.ModelSpace
    kill = []
    for i in range(ms.Count):
        try:
            e = ms.Item(i)
            nm = e.ObjectName
        except Exception:
            continue
        try:
            if nm == "AcDbBlockReference":
                if not blk or (e.Name in blk):
                    kill.append(e)
            elif (e.Layer or "").upper() in lay:
                kill.append(e)
        except Exception:
            continue
    for e in kill:
        try:
            e.Delete()
        except Exception:
            pass
    return len(kill)


def draw_dxf_into_cad(dxf_path, dwg_path, log=None, use_original=False,
                      copy_dir=None, visible=True, only_blocks=None,
                      clear_first=True, progress=None, sheet_names=None):
    """把 dxf_path 的内容画进 dwg_path（默认画在副本上，不动原文件）。"""
    log = log if log is not None else []
    _t_start = time.time()

    def pg(pct, stage):
        if progress:
            try:
                progress(pct, stage)
            except Exception:
                pass

    if not HAVE_COM:
        log.append("⚠ 画到 CAD 需要 pywin32：pip install pywin32")
        return False
    if not os.path.exists(dwg_path):
        log.append("⚠ 找不到 DWG: %s（画到 CAD 需要 DWG，不能是 DXF）" % dwg_path)
        return False
    pg(86, "正在连接 CAD（ZWCAD / AutoCAD）…（没开的话会自动启动，可能要等十几秒）")
    app = connect(visible=visible, log=log)
    if app is None:
        return False
    pg(88, "已连上 CAD: %s" % getattr(app, "Version", "?"))
    # CAD 正忙着（有命令在跑、或弹了个对话框）时，COM 调用会一直等下去。
    # 先看一眼，忙就先不画，免得界面卡在“生成中…”。
    try:
        active = app.ActiveDocument.GetVariable("CMDACTIVE")
        if int(active) != 0:
            log.append("⚠ ZWCAD 里还有命令在跑（CMDACTIVE=%s）：回到命令提示符、"
                       "把弹窗关掉，再点一次生成。" % active)
            return False
    except Exception:
        pass
    target = os.path.abspath(dwg_path)
    pg(89, "准备目标图：%s" % os.path.basename(dwg_path))
    if not use_original:
        import shutil
        d = copy_dir or os.path.dirname(os.path.abspath(dxf_path))
        os.makedirs(d, exist_ok=True)
        cand = os.path.join(d, "_画到CAD_" + os.path.basename(dwg_path))
        doc0, _ = find_doc(app, cand, open_if_missing=False)
        if doc0 is None:                       # 副本没开着才拷，开着就被 CAD 锁着
            try:
                shutil.copyfile(dwg_path, cand)
            except Exception as ex:
                log.append("⚠ 复制副本失败(%s)，直接画在原文件上" % ex)
                cand = target
        target = cand
    doc, opened = find_doc(app, target)
    log.append("画到: %s%s" % (doc.Name, "（脚本刚打开的）" if opened else "（本来就开着）"))
    for lay in ("WIRE", "WIRE_LABEL", "CONN_POS", "CONN_NEG", "0"):
        ensure_layer(doc, lay)
    if clear_first:
        pg(92, "清掉上次程序画的连线/标注…")
        n = clear_ours(doc, only_blocks, OUR_LAYERS, log)
        if n:
            log.append("先清掉上一次程序画的内容 %d 个（只删 %s 层和这几个块的插入）"
                       % (n, "/".join(OUR_LAYERS)))
    stat = replay(doc, dxf_path, log, only_blocks=only_blocks, progress=pg,
                  sheet_names=sheet_names)
    pg(98, "实体画完，正在加长度标注/线号，并缩放到范围")
    try:
        app.ZoomExtents()
    except Exception:
        pass
    log.append("画进 CAD: INSERT %d、直线 %d、多段线 %d、文字 %d、点 %d"
               "（跳过的 %d），另外现造块定义用了 %d 个图元"
               % (stat["INSERT"], stat["LINE"], stat["LWPOLYLINE"], stat["TEXT"],
                  stat["POINT"], stat["skip"], stat["blk_prim"]))
    log.append("画到 CAD 总共用时 %.1f 秒" % (time.time() - _t_start))
    log.append("没有自动保存，你在 CAD 里看过再决定存不存。")
    pg(100, "画到 CAD 完成（还没保存，你在 CAD 里确认后自己存）")
    return True
