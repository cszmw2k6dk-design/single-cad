#!/usr/bin/env python3
"""
dxf_info.py -- DXF 体检工具（只读）

用途：在不装 AutoCAD 的情况下，快速看清一个 DXF 里有什么：
  段(SECTION)、块定义(BLOCK)、块记录(BLOCK_RECORD)、图层(LAYER)、
  ENTITIES 里的实体类型统计、句柄范围、以及 CONN 层上的连接点。

用法:
  python dxf_info.py "blocklib/blocks/正极支线.dxf"
  python dxf_info.py "templates/外框模板(EU) 09072026.dxf" --blocks
"""

import argparse
import collections
import os
import sys


def _safe(s):
    """保证在 GBK 控制台也不炸：能编码就原样，不能就转义。"""
    s = str(s)
    enc = sys.stdout.encoding or "utf-8"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode("unicode_escape").decode("ascii")


def load_pairs(path, enc="latin-1"):
    txt = open(path, encoding=enc, errors="replace").read()
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")
    lines = txt.split("\n")
    return [(lines[i].strip(), lines[i + 1].strip())
            for i in range(0, len(lines) - 1, 2)]


def split_sections(pairs):
    sections, order = {}, []
    j = 0
    while j < len(pairs):
        if pairs[j] == ("0", "SECTION"):
            name = pairs[j + 1][1] if j + 1 < len(pairs) and pairs[j + 1][0] == "2" else None
            body, k = [], j + 2
            while k < len(pairs) and pairs[k] != ("0", "ENDSEC"):
                body.append(pairs[k]); k += 1
            if name:
                sections[name] = body; order.append(name)
            j = k + 1
        else:
            j += 1
    return sections, order


def group(pairs):
    """按 0 组码切分实体。"""
    out, cur = [], None
    for c, v in pairs:
        if c == "0":
            if cur is not None:
                out.append(cur)
            cur = [(c, v)]
        elif cur is not None:
            cur.append((c, v))
    if cur is not None:
        out.append(cur)
    return out


def g1(rec, code):
    for c, v in rec:
        if c == code:
            return v
    return None


def table_names(tables, table):
    names, i = [], 0
    want = None
    while i < len(tables):
        if tables[i] == ("0", "TABLE") and i + 1 < len(tables) and tables[i + 1][1] == table:
            want = table
            i += 2
            continue
        if tables[i] == ("0", "ENDTAB"):
            want = None
        elif want and tables[i][0] == "0":
            names.append((tables[i][1], g1(group(tables[i:i + 12])[0], "2")))
        i += 1
    return names


def layers(tables):
    out, i = [], 0
    while i < len(tables):
        if tables[i] == ("0", "LAYER"):
            rec = group(tables[i:i + 14])[0]
            out.append((g1(rec, "2"), g1(rec, "62")))
        i += 1
    return out


def block_records(tables):
    out = []
    for rec in group(tables):
        if rec[0][1] == "BLOCK_RECORD":
            out.append((g1(rec, "2"), g1(rec, "5")))
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--blocks", action="store_true", help="列出每个块定义里的实体类型统计")
    ap.add_argument("--encoding", default="latin-1")
    a = ap.parse_args()
    if not os.path.exists(a.path):
        print("文件不存在:", a.path); return 1
    sec, order = split_sections(load_pairs(a.path, a.encoding))
    print("文件: %s (%.0f KB)" % (a.path, os.path.getsize(a.path) / 1024.0))
    print("段: %s" % ", ".join(order))
    handles = []
    for body in sec.values():
        for c, v in body:
            if c == "5":
                try:
                    handles.append(int(v, 16))
                except ValueError:
                    pass
    if handles:
        print("句柄范围: %X .. %X" % (min(handles), max(handles)))
    for c, v in sec.get("HEADER", []):
        if c == "5" and any(sec.get("HEADER", [])[k] == ("9", "$HANDSEED")
                            for k in range(len(sec.get("HEADER", [])))):
            pass

    print("\n块记录(BLOCK_RECORD) %d 个:" % len(block_records(sec.get("TABLES", []))))
    for nm, h in block_records(sec.get("TABLES", [])):
        print("  %s" % _safe(nm))

    print("\n图层 %d 个:" % len(layers(sec.get("TABLES", []))))
    for nm, col in layers(sec.get("TABLES", [])):
        print("  %-20s color=%s" % (_safe(nm), col))

    ents = group(sec.get("ENTITIES", []))
    cnt = collections.Counter(e[0][1] for e in ents)
    print("\nENTITIES 共 %d 个: %s" % (len(ents), dict(cnt)))
    conn = [e for e in ents if (g1(e, "8") or "").upper().startswith("CONN")]
    if conn:
        print("CONN 层实体 %d 个:" % len(conn))
        for e in conn[:20]:
            print("  %-8s layer=%-6s (%.4s, %.4s)" %
                  (e[0][1], _safe(g1(e, "8")), g1(e, "10"), g1(e, "20")))

    if a.blocks:
        print("\n--- 块定义明细 ---")
        for b in group(sec.get("BLOCKS", [])):
            if b[0][1] == "BLOCK":
                print("BLOCK %s base=(%s,%s)" % (_safe(g1(b, "2")), g1(b, "10"), g1(b, "20")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
