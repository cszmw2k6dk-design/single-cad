#!/usr/bin/env python3
"""
dwg_to_dxf.py -- 借 ZWCAD 把 DWG 导成 DXF（脚本本身读不了二进制 DWG）

原理：用 COM 让 CAD 打开那个 DWG，然后 SaveAs 成一个 .dxf（**DXF 是另存的新文件，
原来的 DWG 一个字节都不动**），最后关掉不保存。

用法:
  python dwg_to_dxf.py "C:/Users/szk/Desktop/SINGLE LINE.dwg"
  python dwg_to_dxf.py 输入.dwg 输出.dxf
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cad_draw as cd          # noqa: E402


def is_dxf(path, head=4096):
    """粗判：DXF 是文本，开头一段里能找到 SECTION；DWG 是二进制。"""
    try:
        with open(path, "rb") as f:
            b = f.read(head)
    except OSError:
        return False
    return b"SECTION" in b and b"\x00" not in b[:64]


def main(argv=None):
    ap = argparse.ArgumentParser(description="用 ZWCAD 把 DWG 导成 DXF")
    ap.add_argument("src", help="输入 .dwg")
    ap.add_argument("dst", nargs="?", default="", help="输出 .dxf（默认 out/<原名>.dxf）")
    ap.add_argument("--close", action="store_true", help="导完把图纸关掉")
    a = ap.parse_args(argv)

    src = os.path.abspath(a.src)
    if not os.path.exists(src):
        print("找不到:", src); return 1
    dst = a.dst or os.path.join(HERE, "out",
                                os.path.splitext(os.path.basename(src))[0] + ".dxf")
    dst = os.path.abspath(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        try:
            os.remove(dst)
        except OSError as ex:
            print("旧文件删不掉(%s)，换个名字再试" % ex); return 1

    log = []
    app = cd.connect(log=log)
    for l in log:
        print("  " + l)
    if app is None:
        return 1

    doc, opened = cd.find_doc(app, src)
    print("图纸: %s%s" % (doc.Name, "（刚打开的）" if opened else "（本来就开着）"))
    try:
        doc.SaveAs(dst)
    except Exception as ex:
        print("SaveAs 失败:", ex)
        try:
            doc.SaveAs(dst, 1)          # 1 = R12 DXF（有的 CAD 要显式给类型）
        except Exception as ex2:
            print("再试一次也失败:", ex2); return 1
    for _ in range(20):
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            break
        time.sleep(0.3)
    ok = is_dxf(dst)
    print("导出: %s (%d 字节)  %s" % (dst, os.path.getsize(dst) if os.path.exists(dst) else 0,
                                     "确认是 DXF" if ok else "⚠ 看着不像 DXF"))
    if a.close:
        try:
            doc.Close(False)
            print("已关闭图纸（没保存）")
        except Exception:
            pass
    print("原来的 DWG 没有被改动。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
