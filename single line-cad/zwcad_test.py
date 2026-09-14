#!/usr/bin/env python3
"""
zwcad_test.py -- 最小验证：用 COM 直接让 ZWCAD 画东西（不写 DXF）

做四件事：
  1. 连上 ZWCAD（没开就启动它，Visible=True，你能看见）
  2. 把外框图另存一份到 out/ 里，打开这份副本（**不动你的模板文件**）
  3. 读一遍图里已有的块名（证明能读图纸）
  4. 插 3 个块 + 画 2 条线（放在 WIRE 层，没有就建一个）

用法:  python zwcad_test.py [--file "templates/外框模板(EU) 09072026.dwg"] [--block T3]
脚本不会保存，看完直接关掉不存就行。
"""

import argparse
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "out")

try:
    import pythoncom
    import win32com.client
    from win32com.client import VARIANT
except ImportError:
    print("这个 Python 没有 pywin32。装一个： pip install pywin32")
    print("（或者用带 pywin32 的 Python 跑这个脚本）")
    raise SystemExit(2)

PROGIDS = ("ZWCAD.Application", "ZWCAD.Application.2026", "AutoCAD.Application")


def pt(x, y):
    """COM 要的点：三个 double 的数组。"""
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, (float(x), float(y), 0.0))


def connect(visible=True):
    last = None
    for pid in PROGIDS:
        try:
            app = win32com.client.Dispatch(pid)
            if visible:
                app.Visible = True
            print("已连上: %s  (版本 %s)" % (pid, getattr(app, "Version", "?")))
            return app, pid
        except Exception as ex:
            last = ex
    print("连不上 CAD：%s" % last)
    print("检查：ZWCAD 装了没、是不是被别的用户/权限挡住了。")
    return None, None


def find_doc(app, path, open_if_missing=True):
    """图纸已经开着就用它，没开就打开。返回 (doc, 是不是新开的)。"""
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


def user_blocks(doc):
    out = []
    for b in doc.Blocks:
        try:
            nm = b.Name
        except Exception:
            continue
        if nm and not nm.startswith("*") and not nm.startswith("_") and not nm.startswith("A$"):
            out.append(nm)
    return out


def extents_center(doc):
    try:
        lo = doc.GetVariable("EXTMIN")
        hi = doc.GetVariable("EXTMAX")
        cx = (float(lo[0]) + float(hi[0])) / 2.0
        cy = (float(lo[1]) + float(hi[1])) / 2.0
        if abs(cx) + abs(cy) > 1e-6:
            return cx, cy
    except Exception:
        pass
    return 0.0, 0.0


def insert_block(ms, x, y, name, sx=1.0, sy=1.0, rot=0.0):
    """插块。ZWCAD 的 InsertBlock 是 7 个参数（多一个 Zscale），AutoCAD 是 5 个，都试一遍。

    ZWCAD 实测签名: InsertBlock(InsertionPoint, Name, Xscale, Yscale, Zscale, Rotation, Password)
    """
    last = None
    for args in ((pt(x, y), name, sx, sy, 1.0, rot),
                 (pt(x, y), name, sx, sy, 1.0, rot, ""),
                 (pt(x, y), name, sx, sy, rot)):
        try:
            return ms.InsertBlock(*args)
        except Exception as ex:
            last = ex
    raise last


def main(argv=None):
    ap = argparse.ArgumentParser(description="COM 直画 ZWCAD 最小验证")
    ap.add_argument("--file", default=os.path.join(HERE, "templates", "外框模板(EU) 09072026.dwg"))
    ap.add_argument("--block", default="", help="插哪个块（默认自动挑一个用户块）")
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--no-copy", action="store_true", help="直接开原文件（默认开 out/ 里的副本）")
    ap.add_argument("--step", type=float, default=0.0, help="块间距（默认按图幅自动算）")
    a = ap.parse_args(argv)

    if not os.path.exists(a.file):
        print("找不到文件: " + a.file); return 1

    pythoncom.CoInitialize()
    app, pid = connect()
    if app is None:
        return 1

    if a.no_copy:
        target = os.path.abspath(a.file)
    else:
        os.makedirs(OUTDIR, exist_ok=True)
        target = os.path.join(OUTDIR, "_zwcad验证_" + os.path.basename(a.file))

    try:
        doc, opened = find_doc(app, target, open_if_missing=False)
        if doc is None and not a.no_copy:
            # 还没开着，才复制一份（已经开着就别动，ZWCAD 锁着文件呢）
            shutil.copyfile(a.file, target)
            print("已复制一份用于验证: " + target)
            doc, opened = find_doc(app, target)
        print("图纸: %s%s" % (doc.Name, "（脚本刚打开的）" if opened else "（本来就开着）"))

        names = user_blocks(doc)
        print("图里的用户块 %d 个，例如: %s" % (len(names), ", ".join(names[:8])))

        blk = a.block or ("T3" if "T3" in names else (names[0] if names else ""))
        if not blk:
            print("这张图里没有可插的用户块"); return 1
        print("准备插的块: %s" % blk)

        ms = doc.ModelSpace
        cx, cy = extents_center(doc)
        if a.step > 0:
            step = a.step
        else:
            try:
                lo, hi = doc.GetVariable("EXTMIN"), doc.GetVariable("EXTMAX")
                step = max(abs(float(hi[0]) - float(lo[0])) / 20.0, 10.0)
            except Exception:
                step = 50.0
        print("落点: (%.1f, %.1f)，间距 %.1f" % (cx, cy, step))

        # 图层：没有 WIRE 就建一个
        try:
            doc.Layers.Item("WIRE")
        except Exception:
            try:
                doc.Layers.Add("WIRE")
                print("新建图层 WIRE")
            except Exception as ex:
                print("建图层失败(不影响验证): %s" % ex)

        pts = []
        for i in range(a.count):
            x = cx + (i - (a.count - 1) / 2.0) * step
            insert_block(ms, x, cy, blk, 1.0, 1.0, 0.0)
            pts.append((x, cy))
            print("  插块 %s @ (%.1f, %.1f)" % (blk, x, cy))

        for i in range(len(pts) - 1):
            ln = ms.AddLine(pt(pts[i][0], pts[i][1] - step * 0.5),
                            pt(pts[i + 1][0], pts[i + 1][1] - step * 0.5))
            try:
                ln.Layer = "WIRE"
            except Exception:
                pass
            print("  画线 (%.1f,%.1f) -> (%.1f,%.1f) 在 WIRE 层"
                  % (pts[i][0], pts[i][1] - step * 0.5,
                     pts[i + 1][0], pts[i + 1][1] - step * 0.5))

        try:
            app.ZoomExtents()
        except Exception:
            pass
        doc.Regen(1) if hasattr(doc, "Regen") else None
        print("\n画完了，去 ZWCAD 窗口里看。脚本不保存，看完关掉不存就行。")
        return 0
    except Exception as ex:
        import traceback
        print("出错: %s: %s" % (type(ex).__name__, ex))
        print("\n".join(traceback.format_exc().strip().splitlines()[-6:]))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
