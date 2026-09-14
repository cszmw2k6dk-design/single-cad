#!/usr/bin/env python3
"""
pack_frame.py -- 把块库里的块“合并进外框图”，产出一个自带块库的外框图。

为什么需要它：
  生成器往图里插的是 INSERT（引用块名）。如果外框图里没有这个块定义，
  AutoCAD 不会报错，但**什么也不显示** —— “正极支线不显示”就是这个原因。
  这个脚本把 blocklib/blocks/*.dxf 的块定义正式写进外框图的 BLOCKS 段。

用法:
  python pack_frame.py "templates/外框模板(EU) 09072026.dxf"
        -> 生成 templates/外框模板(EU) 09072026+块库.dxf
  python pack_frame.py <frame> --only FUSE,PVMODULE     # 只合并指定块
  python pack_frame.py <frame> --out "templates/xxx.dxf"
  python pack_frame.py <frame> --inplace                # 直接改原外框(自动 .bak 备份)

只做“字节级定点插入”，不重建 DXF，避免 AutoCAD 报“无效或不完整的 DXF 输入”。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blockpack as bp
import blockui_server as ui


def library_blocks():
    """块库里的所有 .dxf（按文件名排序）。"""
    d = ui.BLOCKS_DIR
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d))
            if f.lower().endswith(".dxf") and not f.lower().endswith(".bak.dxf")]


def frame_block_names(path):
    data = open(path, "rb").read()
    gl = list(bp.iter_groups(data))
    tr = bp.table_range(gl, b"BLOCK_RECORD")
    if tr is None:
        return set()
    return {bp.rec_get(r, b"2") for r in bp.split_records(gl, tr[1], tr[2])
            if bp.rec_get(r, b"2")}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("frame")
    ap.add_argument("--out", default=None)
    ap.add_argument("--only", default=None, help="只合并这些块名，逗号分隔")
    ap.add_argument("--all", action="store_true", help="合并块库全部块(默认)")
    ap.add_argument("--inplace", action="store_true", help="直接改原外框图(先备份 .bak)")
    a = ap.parse_args()

    if not os.path.exists(a.frame):
        print("找不到外框图:", a.frame); return 1
    paths = library_blocks()
    if a.only:
        want = {s.strip() for s in a.only.split(",") if s.strip()}
        paths = [p for p in paths
                 if os.path.splitext(os.path.basename(p))[0] in want]
        missing = want - {os.path.splitext(os.path.basename(p))[0] for p in paths}
        if missing:
            print("块库里没有:", ", ".join(sorted(missing)))
    if not paths:
        print("块库是空的(blocklib/blocks/*.dxf)"); return 1

    before = open(a.frame, "rb").read()
    names = []
    for p in paths:
        n = os.path.splitext(os.path.basename(p))[0]
        if n.encode("utf-8") not in frame_block_names(a.frame):
            names.append(n)
    print("外框图: %s (%.0f KB)" % (a.frame, len(before) / 1024.0))
    print("外框图已有块: %d 个" % len(frame_block_names(a.frame)))
    print("准备合并 %d 个块: %s" % (len(paths), ", ".join(
        os.path.splitext(os.path.basename(p))[0] for p in paths)))

    out, info = bp.pack_into(before, paths)
    for line in info["log"]:
        print("  " + line)
    problems = bp.verify(out, names)
    print("\n合并结果: 新增 %d 个块, 文件 %.0f KB -> %.0f KB"
          % (len(info["added"]), len(before) / 1024.0, len(out) / 1024.0))
    if problems:
        print("⚠ 结构检查发现问题:")
        for p in problems:
            print("   - " + p)
        print("已放弃写出（避免生成打不开的文件）")
        return 2
    print("结构检查: 通过")

    if a.inplace:
        bak = a.frame + ".bak"
        if not os.path.exists(bak):
            open(bak, "wb").write(before)
            print("已备份 ->", bak)
        target = a.frame
    else:
        if a.out:
            target = a.out
        else:
            root, ext = os.path.splitext(a.frame)
            target = root + "+块库" + ext
    open(target, "wb").write(out)
    print("已写出 ->", target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
