#!/usr/bin/env python3
"""
blockui_server.py -- 块库数据库可视化看板（零第三方依赖，纯 stdlib）

把 blocklib/_manifest.csv 做成一个可视化 UI：每个块一块卡片，显示真实符号预览、
块名、前缀、属性、文件状态(.dxf/.dwg/缺)。支持搜索、编辑、新增、删除，并保存回 CSV。

用法:
    python blockui_server.py             # 启动并自动打开浏览器
    python blockui_server.py --port 9000

数据源:  blocklib/_manifest.csv          (块库"数据库")
         blocklib/blocks/<块名>.dxf      (符号预览取自 .dxf)
"""

import argparse
import csv
import json
import math
import os
import re
import socketserver
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler


HERE = os.path.dirname(os.path.abspath(__file__))
try:                                  # 打包成 exe 后走 runtime_paths（块库在 exe 旁边）
    import runtime_paths as _rp
    LIB = _rp.BLOCKLIB_DIR
    MANIFEST = _rp.MANIFEST
    BLOCKS_DIR = _rp.BLOCKS_DIR
except Exception:                     # 源码运行 / 单独拷这个文件时，维持原样
    LIB = os.path.join(HERE, "blocklib")
    MANIFEST = os.path.join(LIB, "_manifest.csv")
    BLOCKS_DIR = os.path.join(LIB, "blocks")
DEFAULT_PORT = 8765


# --------------------------------------------------------------------------
# 读取注册表
# --------------------------------------------------------------------------
def parse_manifest():
    rows = []
    if not os.path.exists(MANIFEST):
        return rows
    with open(MANIFEST, encoding="utf-8") as f:
        for r in csv.reader(f):
            if not r or r[0].strip().startswith("#"):
                continue
            if len(r) < 8:
                continue
            rows.append({
                "id": r[0].strip(),
                "block": r[1].strip(),
                "file": r[2].strip(),
                "prefix": r[3].strip(),
                "xs": r[4].strip(),
                "ys": r[5].strip(),
                "rot": r[6].strip(),
                "attrs": [a.strip() for a in r[7].split(";") if a.strip()],
            })
    return rows


def write_manifest(rows):
    with open(MANIFEST, "w", encoding="utf-8", newline="") as f:
        f.write("# 块库注册表 (Block Registry)  --  由 blockui_server.py 管理\n")
        f.write("# 格式: ID,BLOCK,FILE,PREFIX,XSCALE,YSCALE,ROT,ATTRS\n")
        w = csv.writer(f, lineterminator="\n")
        for r in rows:
            attrs = ";".join(r.get("attrs", []))
            w.writerow([
                r.get("id", ""),
                r.get("block", ""),
                r.get("block", "") + ".dwg",
                r.get("prefix", ""),
                r.get("xs", "1"),
                r.get("ys", "1"),
                r.get("rot", "0"),
                attrs,
            ])


# --------------------------------------------------------------------------
# 解析 .dxf 生成 SVG 符号预览
# --------------------------------------------------------------------------
def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _g(e, code):
    for c, v in e.get("codes", []):
        if c == code:
            return v
    return None


def _f(e, code, default=0.0):
    v = _g(e, code)
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _matmul(P, M):
    a, b, c, d, e, f = P
    A, B, C, D, E, F = M
    return (a * A + c * B, b * A + d * B,
            a * C + c * D, b * C + d * D,
            a * E + c * F + e, b * E + d * F + f)


def _apply(m, x, y):
    a, b, c, d, e, f = m
    return a * float(x) + c * float(y) + e, b * float(x) + d * float(y) + f


def _read_dxf(path):
    txt = open(path, encoding="utf-8", errors="replace").read()
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")
    return _parse_dxf(txt)


def _parse_dxf(txt):
    def section(name):
        m = re.search(r"\n\s*0\n\s*SECTION\n\s*2\n\s*" + name +
                      r"\n(.*?)\n\s*0\n\s*ENDSEC", txt, re.S)
        return m.group(1) if m else ""

    def ents(seg):
        lines = seg.split("\n")
        pairs = []
        i = 0
        while i + 1 < len(lines):
            pairs.append((lines[i].strip(), lines[i + 1].strip()))
            i += 2
        out = []
        cur = None
        for code, val in pairs:
            if code == "0":
                if cur is not None:
                    out.append(cur)
                cur = {"type": val, "codes": []}
            else:
                cur["codes"].append((code, val))
        if cur is not None:
            out.append(cur)
        return out

    entities = ents(section("ENTITIES"))
    be = ents(section("BLOCKS"))
    blocks = {}
    i = 0
    while i < len(be):
        if be[i]["type"] == "BLOCK":
            name = _g(be[i], "2")
            members = []
            i += 1
            while i < len(be) and be[i]["type"] != "ENDBLK":
                members.append(be[i])
                i += 1
            blocks[name] = members
        i += 1
    return entities, blocks


def _collect(ents, blocks, mtx, depth, out):
    if depth > 8:
        return
    i = 0
    while i < len(ents):
        e = ents[i]
        t = e["type"]
        if t == "INSERT":
            nm = _g(e, "2")
            px, py = _f(e, "10"), _f(e, "20")
            sx = _f(e, "41", 1) or 1
            sy = _f(e, "42", 1) or 1
            rot = _f(e, "50", 0)
            rr = rot * math.pi / 180.0
            S = (sx, 0, 0, sy, 0, 0)
            R = (math.cos(rr), math.sin(rr), -math.sin(rr), math.cos(rr), 0, 0)
            T = (1, 0, 0, 1, px, py)
            child = _matmul(mtx, _matmul(_matmul(T, R), S))
            if nm in blocks:
                _collect(blocks[nm], blocks, child, depth + 1, out)
            i += 1
            continue
        if t == "LINE":
            x1, y1 = _apply(mtx, _f(e, "10"), _f(e, "20"))
            x2, y2 = _apply(mtx, _f(e, "11"), _f(e, "21"))
            out.append(("line", x1, y1, x2, y2))
            i += 1
            continue
        if t == "CIRCLE":
            cx, cy = _apply(mtx, _f(e, "10"), _f(e, "20"))
            out.append(("circle", cx, cy, _f(e, "40") * math.hypot(mtx[0], mtx[1])))
            i += 1
            continue
        if t == "ARC":
            lcx, lcy, lr = _f(e, "10"), _f(e, "20"), _f(e, "40")
            a0, a1 = _f(e, "50"), _f(e, "51")
            if a1 < a0:
                a1 += 360
            pts = []
            for k in range(17):
                aa = (a0 + (a1 - a0) * k / 16.0) * math.pi / 180.0
                pts.append(_apply(mtx, lcx + lr * math.cos(aa),
                                  lcy + lr * math.sin(aa)))
            out.append(("poly", pts, False))
            i += 1
            continue
        if t == "LWPOLYLINE":
            pts = []
            curx = cury = None
            for code, val in e.get("codes", []):
                if code == "10":
                    curx = val
                elif code == "20":
                    cury = val
                    pts.append(_apply(mtx, curx, cury))
            if pts:
                out.append(("poly", pts, True))
            i += 1
            continue
        if t == "POLYLINE":
            pts = []
            j = i + 1
            while j < len(ents) and ents[j]["type"] != "SEQEND":
                if ents[j]["type"] == "VERTEX":
                    pts.append(_apply(mtx, _f(ents[j], "10"), _f(ents[j], "20")))
                j += 1
            if pts:
                out.append(("poly", pts, True))
            i = j + 1
            continue
        if t in ("TEXT", "MTEXT"):
            x, y = _apply(mtx, _f(e, "10"), _f(e, "20"))
            val = (_g(e, "1") or "").replace("\\P", " ").replace("\\p", " ")
            val = re.sub(r"\\[A-Za-z][^;]*;", "", val)
            out.append(("text", x, y, val))
            i += 1
            continue
        if t == "ATTDEF":
            x, y = _apply(mtx, _f(e, "10"), _f(e, "20"))
            tag = _g(e, "3") or ""
            if tag:
                out.append(("text", x, y, "[" + tag + "]"))
            i += 1
            continue
        if t == "ELLIPSE":
            lcx, lcy = _f(e, "10"), _f(e, "20")
            maj, mj = _f(e, "11"), _f(e, "21")
            rx = math.hypot(maj, mj) if maj else 1
            pts = []
            for k in range(25):
                th = 2 * math.pi * k / 24.0
                pts.append(_apply(mtx, lcx + rx * math.cos(th),
                                  lcy + rx * math.sin(th)))
            out.append(("poly", pts, True))
            i += 1
            continue
        i += 1


def block_svg(block):
    """从块 .dxf 生成 SVG；无 .dxf 返回 (None, False)。"""
    dxf = os.path.join(BLOCKS_DIR, block + ".dxf")
    if not os.path.exists(dxf):
        return None, False
    # 优先用 wiring_raw 那套完整渲染器（认 SPLINE/HATCH/椭圆短轴/多段线凸度），
    # 这样块卡片和最终画到图上的样子一致。它依赖本模块，所以这里延迟导入。
    try:
        import wiring_raw as _wr
        svg = _wr.block_file_svg(block)
        if svg:
            return svg, True
    except Exception:
        pass
    try:
        entities, blocks = _read_dxf(dxf)
    except Exception:
        return None, True
    out = []
    _collect(entities, blocks, (1, 0, 0, 1, 0, 0), 0, out)
    xs, ys = [], []
    for p in out:
        if p[0] == "line":
            xs += [p[1], p[3]]
            ys += [p[2], p[4]]
        elif p[0] == "circle":
            xs += [p[1] - p[3], p[1] + p[3]]
            ys += [p[2] - p[3], p[2] + p[3]]
        elif p[0] == "poly":
            for (x, y) in p[1]:
                xs.append(x)
                ys.append(y)
        elif p[0] == "text":
            xs += [p[1] - 15, p[1] + 15]
            ys += [p[2] - 4, p[2] + 4]
    if not xs:
        return None, True
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    W, H, pad = 150, 110, 12
    spanx = max(maxx - minx, 1e-6)
    spany = max(maxy - miny, 1e-6)
    scale = min((W - 2 * pad) / spanx, (H - 2 * pad) / spany)

    def px(x):
        return pad + (x - minx) * scale

    def py(y):
        return H - pad - (y - miny) * scale

    parts = []
    for p in out:
        if p[0] == "line":
            parts.append(f'<line x1="{px(p[1]):.1f}" y1="{py(p[2]):.1f}" '
                         f'x2="{px(p[3]):.1f}" y2="{py(p[4]):.1f}" '
                         'stroke="#1c1c1c" stroke-width="2"/>')
        elif p[0] == "circle":
            parts.append(f'<circle cx="{px(p[1]):.1f}" cy="{py(p[2]):.1f}" '
                         f'r="{p[3] * scale:.1f}" fill="none" stroke="#1c1c1c" '
                         'stroke-width="2"/>')
        elif p[0] == "poly":
            pts = " ".join(f"{px(x):.1f},{py(y):.1f}" for (x, y) in p[1])
            if pts:
                parts.append(f'<polyline points="{pts}" fill="none" '
                             'stroke="#1c1c1c" stroke-width="2"/>')
        elif p[0] == "text":
            sv = _esc(p[3])
            if sv:
                parts.append(f'<text x="{px(p[1]):.1f}" y="{py(p[2]):.1f}" '
                             'font-size="8" fill="#1466c8" text-anchor="middle">'
                             f'{sv}</text>')
    svg = (f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet" '
           f'style="max-width:100%;height:120px">' + "".join(parts) + "</svg>")
    return svg, True


def has_file(block):
    return {
        "dxf": os.path.exists(os.path.join(BLOCKS_DIR, block + ".dxf")),
        "dwg": os.path.exists(os.path.join(BLOCKS_DIR, block + ".dwg")),
    }


def read_ports(block):
    """读块的连接点：扫 CONN 层上的 POINT 实体（含块定义内）。

    约定：在块的连接位置放一个 POINT 实体，并放在 CONN 层（或 CONN_* 子层，
    层名即用途，如 CONN_LV / CONN_HV / CONN_DC）。程序据此拿固定连接点。
    返回 list of {x, y, layer, block}。"""
    dxf = os.path.join(BLOCKS_DIR, block + ".dxf")
    if not os.path.exists(dxf):
        return []
    try:
        entities, blocks_def = _read_dxf(dxf)
    except Exception:
        return []
    pts = []

    def collect(ents, bname):
        for e in ents:
            layer = (_g(e, "8") or "")
            if e["type"] == "POINT" and layer.upper().startswith("CONN"):
                pts.append({"x": _f(e, "10"), "y": _f(e, "20"),
                            "layer": layer, "block": bname})

    collect(entities, "*Model_Space")
    for name, members in blocks_def.items():
        collect(members, name)
    return pts


def capture_points(block):
    """捕捉块的连接点（供电线用）。
    优先：CONN 层上的 POINT（显式、可带 CONN_LV/HV/DC 语义）
    兜底：所有 POLYLINE/LWPOLYLINE 的端点（用户用线画点的画法）。
    返回 list of {x, y, layer, kind, block}，去重。"""
    conn = read_ports(block)
    if conn:
        return [{**p, "kind": "conn"} for p in conn]

    dxf = os.path.join(BLOCKS_DIR, block + ".dxf")
    if not os.path.exists(dxf):
        return []
    try:
        entities, blocks_def = _read_dxf(dxf)
    except Exception:
        return []
    pts = []

    def poly_endpoints(members, bname):
        i = 0
        while i < len(members):
            if members[i]["type"] == "POLYLINE":
                v = []
                j = i + 1
                while j < len(members) and members[j]["type"] != "SEQEND":
                    if members[j]["type"] == "VERTEX":
                        v.append((_f(members[j], "10"), _f(members[j], "20")))
                    j += 1
                if len(v) >= 2:
                    for pt in (v[0], v[-1]):
                        pts.append({"x": pt[0], "y": pt[1], "layer": "POLY",
                                    "kind": "edge", "block": bname})
                i = j + 1
            else:
                i += 1

    def lw_points(members, bname):
        for m in members:
            if m["type"] == "LWPOLYLINE":
                cur = None
                for c, v in m["codes"]:
                    if c == "10":
                        cur = v
                    elif c == "20" and cur is not None:
                        pts.append({"x": float(cur), "y": float(v),
                                    "layer": "LWPOLY", "kind": "edge", "block": bname})
                        cur = None

    for name, members in blocks_def.items():
        poly_endpoints(members, name)
        lw_points(members, name)
    poly_endpoints(entities, "*Model_Space")
    lw_points(entities, "*Model_Space")

    # 去重（坐标按 3 位小数）
    seen = set()
    out = []
    for p in pts:
        key = (round(p["x"], 3), round(p["y"], 3), p["layer"])
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def block_base_names():
    """blocks/ 目录下所有块文件的主名（不含扩展名）。"""
    if not os.path.isdir(BLOCKS_DIR):
        return set()
    return {os.path.splitext(f)[0] for f in os.listdir(BLOCKS_DIR)}


def rename_block_file(old, new):
    """把 blocks/<old>.dxf/.dwg 重命名为 <new>。<new> 已存在则不覆盖，避免误删。"""
    renamed = []
    for ext in (".dxf", ".dwg"):
        src = os.path.join(BLOCKS_DIR, old + ext)
        dst = os.path.join(BLOCKS_DIR, new + ext)
        if os.path.exists(src) and not os.path.exists(dst):
            os.rename(src, dst)
            renamed.append(new + ext)
    return renamed


def orphans_and_missing(rows=None):
    """找出：磁盘上无人认领的块文件(orphans)，和注册表里缺文件的块(missing)。"""
    if rows is None:
        rows = parse_manifest()
    known = {r["block"] for r in rows}
    files = block_base_names()
    orphans = sorted(files - known)
    missing = [r["block"] for r in rows
               if not has_file(r["block"])["dxf"]
               and not has_file(r["block"])["dwg"]]
    return orphans, missing


def reconcile():
    """1对1 时把孤儿文件重命名回缺失的块（修复已发生的改名错位）。"""
    orphans, missing = orphans_and_missing()
    report = {"renamed": [], "orphans": orphans, "missing": missing}
    if len(orphans) == 1 and len(missing) == 1:
        old, new = orphans[0], missing[0]
        renamed = rename_block_file(old, new)
        if renamed:
            report["renamed"] = [{"from": old, "to": new, "files": renamed}]
            report["orphans"] = []
            report["missing"] = []
    return report


def build_data():
    rows = parse_manifest()
    blocks = []
    for r in rows:
        svg, made = block_svg(r["block"])
        files = has_file(r["block"])
        if not made or not svg:
            if files["dwg"]:
                svg = _placeholder_svg(r["block"], "仅有 .dwg——请导出 .dxf 生成预览")
            elif files["dxf"]:
                svg = _placeholder_svg(r["block"], "预览异常——请检查该 .dxf")
            else:
                svg = _placeholder_svg(r["block"], "缺失块文件")
        if files["dwg"]:
            status = "dwg"
        elif files["dxf"]:
            status = "dxf"
        else:
            status = "缺失"
        # 预览来源与是否过时（.dwg 比 .dxf 新则说明预览可能没跟上）
        preview_stale = False
        if files["dwg"] and files["dxf"]:
            dxf_p = os.path.join(BLOCKS_DIR, r["block"] + ".dxf")
            dwg_p = os.path.join(BLOCKS_DIR, r["block"] + ".dwg")
            preview_stale = os.path.getmtime(dwg_p) > os.path.getmtime(dxf_p)
        blocks.append({**r, "svg": svg, "status": status,
                       "has_dwg": files["dwg"], "has_dxf": files["dxf"],
                       "preview_stale": preview_stale})
    orphans, missing = orphans_and_missing(rows)
    return {"blocks": blocks, "count": len(blocks),
            "dwg": sum(1 for b in blocks if b["has_dwg"]),
            "dxf": sum(1 for b in blocks if b["has_dxf"]),
            "orphans": orphans, "missing": missing}


def _placeholder_svg(name, msg="暂无预览"):
    return (f'<svg viewBox="0 0 150 110" style="max-width:100%;height:120px">'
            f'<rect x="20" y="20" width="110" height="70" rx="6" fill="#f0f2f5" '
            f'stroke="#c6cbd4" stroke-width="1.5" stroke-dasharray="5 4"/>'
            f'<text x="75" y="62" font-size="11" fill="#8a94a6" '
            f'text-anchor="middle">{msg}</text></svg>')


# --------------------------------------------------------------------------
# HTTP 服务
# --------------------------------------------------------------------------
HTML = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>块库数据库 · 可视化看板</title>
<style>
  :root{
    --bg:#f5f6f8; --card:#ffffff; --line:#e6e8ee; --ink:#1b1f27;
    --muted:#7b8494; --brand:#1466c8; --green:#1c8a4c; --purple:#9b3fd4;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
       background:var(--bg);color:var(--ink)}
  header{background:linear-gradient(90deg,#0f3a75,#1466c8);color:#fff;
         padding:18px 28px;box-shadow:0 2px 10px rgba(0,0,0,.12)}
  header h1{margin:0;font-size:20px;font-weight:600}
  header .sub{font-size:13px;opacity:.85;margin-top:4px}
  .toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;
           padding:16px 28px}
  .search{flex:1;min-width:220px;position:relative}
  .search input{width:100%;padding:10px 14px 10px 36px;border:1px solid var(--line);
                border-radius:10px;font-size:14px;background:#fff}
  .search svg{position:absolute;left:12px;top:11px;opacity:.4}
  .stats{display:flex;gap:14px}
  .stat{background:var(--card);border:1px solid var(--line);border-radius:10px;
        padding:8px 14px;font-size:13px;color:var(--muted)}
  .stat b{color:var(--ink);font-size:16px}
  button.primary{background:var(--brand);border:0;color:#fff;padding:10px 18px;
                 border-radius:10px;font-size:14px;cursor:pointer;font-weight:600}
  button.primary:hover{filter:brightness(1.08)}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));
        gap:16px;padding:0 28px 40px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;
        overflow:hidden;transition:.15s;display:flex;flex-direction:column}
  .card:hover{box-shadow:0 8px 24px rgba(20,60,120,.10);transform:translateY(-2px)}
  .preview{height:150px;background:#fbfcfe;border-bottom:1px solid var(--line);
           display:flex;align-items:center;justify-content:center;padding:6px}
  .body{padding:12px 14px;flex:1}
  .name{font-weight:600;font-size:15px}
  .id{color:var(--muted);font-size:12px}
  .pills{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
  .pill{font-size:11px;padding:2px 8px;border-radius:20px;background:#eef2fb;
        color:#2b5ba8}
  .prefix{border-radius:6px;background:#eaf7ef;color:var(--green)}
  .meta{font-size:12px;color:var(--muted);line-height:1.7}
  .pnote{font-size:11px;margin-top:6px;padding:4px 8px;border-radius:6px;
         background:#f2f4f7;color:#6b7484}
  .pnote.warn{background:#fff4e5;color:#a86a12}
  .foot{display:flex;justify-content:space-between;align-items:center;
        padding:10px 14px;border-top:1px solid var(--line)}
  .badge{font-size:11px;padding:3px 9px;border-radius:8px;font-weight:600}
  .badge.dwg{background:#e7f7ee;color:var(--green)}
  .badge.dxf{background:#e9f1fc;color:var(--brand)}
  .badge.缺失{background:#fdeeee;color:#c0392b}
  .edit{background:#fff;border:1px solid var(--line);border-radius:8px;
        padding:5px 12px;font-size:12px;cursor:pointer;color:var(--ink)}
  .edit:hover{border-color:var(--brand);color:var(--brand)}
  .empty{padding:60px;text-align:center;color:var(--muted)}
  dialog{border:0;border-radius:14px;padding:0;width:min(560px,92vw);
         box-shadow:0 20px 60px rgba(0,0,0,.25)}
  dialog::backdrop{background:rgba(15,30,60,.4)}
  .dlg-head{display:flex;justify-content:space-between;align-items:center;
            padding:16px 20px;border-bottom:1px solid var(--line)}
  .dlg-body{padding:16px 20px;display:flex;flex-direction:column;gap:12px}
  .fld label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}
  .fld input{width:100%;padding:8px 10px;border:1px solid var(--line);
             border-radius:8px;font-size:14px}
  .row{display:flex;gap:12px}.row .fld{flex:1}
  .dlg-foot{display:flex;justify-content:flex-end;gap:10px;padding:14px 20px;
            border-top:1px solid var(--line)}
  .btn{border:0;padding:9px 18px;border-radius:9px;font-size:14px;cursor:pointer}
  .btn.ghost{background:#eef0f4;color:var(--ink)}
  .btn.danger{background:#fbe3e3;color:#c0392b}
</style>
</head>
<body>
<header>
  <h1>块库数据库 · 可视化看板</h1>
  <div class="sub">Barnett Solar &amp; BESS 单线图块库 — 双击卡片编辑，改动保存后写回 _manifest.csv</div>
</header>

<div class="toolbar">
  <div class="search">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="2">
      <circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
    <input id="search" placeholder="搜索块名 / 前缀 / 属性…">
  </div>
  <div class="stats">
    <div class="stat">总数 <b id="nTot">0</b></div>
    <div class="stat">已转 <b id="nDwg">0</b> .dwg</div>
    <div class="stat">模板 <b id="nDxf">0</b> .dxf</div>
  </div>
  <button class="primary" onclick="addBlock()">+ 新增块</button>
</div>

<div id="banner" style="display:none;margin:0 28px 12px;padding:12px 16px;
     background:#fff7e6;border:1px solid #f0d9a6;border-radius:10px;
     font-size:13px;color:#8a5a12"></div>

<div id="full"><div class="grid" id="grid"></div></div>

<dialog id="dlg">
  <div class="dlg-head"><b id="dlgTitle">编辑块</b>
    <button class="edit" onclick="dlg.close()">✕</button></div>
  <div class="dlg-body">
    <div class="row">
      <div class="fld"><label>块名 (BLOCK)</label><input id="f_block" placeholder="POS"></div>
      <div class="fld"><label>ID</label><input id="f_id" placeholder="POS"></div>
    </div>
    <div class="row">
      <div class="fld"><label>编号前缀</label><input id="f_prefix" placeholder="PCS-"></div>
      <div class="fld"><label>属性 (;分隔)</label><input id="f_attrs" placeholder="TAG;ACKW;STR"></div>
    </div>
    <div class="row">
      <div class="fld"><label>X 缩放</label><input id="f_xs"></div>
      <div class="fld"><label>Y 缩放</label><input id="f_ys"></div>
      <div class="fld"><label>旋转(°)</label><input id="f_rot"></div>
    </div>
    <div class="fld"><label>提示：保存后 FILE 列自动设为 &lt;块名&gt;.dwg；符号预览取自 blocks/&lt;块名&gt;.dxf。</label></div>
  </div>
  <div class="dlg-foot">
    <button class="btn danger" id="f_del" onclick="deleteBlock()">删除</button>
    <button class="btn ghost" onclick="dlg.close()">取消</button>
    <button class="btn primary" onclick="saveBlock()">保存</button>
  </div>
</dialog>

<script>
let state = {blocks: []};
let editingIndex = -1;
let editingOldName = '';

const esc = s => String(s==null?'':s).replace(/[&<>"]/g,
      c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

async function load(){
  const r = await fetch('/api/data'); state = await r.json();
  state.blocks.forEach(b => b.oldBlock = b.block);
  updateBanner();
  render();
}

function updateBanner(){
  const bx = document.getElementById('banner');
  const orphs = state.orphans||[], miss = state.missing||[];
  if(!orphs.length && !miss.length){ bx.style.display='none'; bx.innerHTML=''; return; }
  let msg = '';
  if(miss.length) msg += '注册表里有块但磁盘缺少文件: <b>'+miss.join(', ')+'</b>。';
  if(orphs.length) msg += '磁盘上存在未登记的块文件: <b>'+orphs.join(', ')+'</b>。';
  bx.style.display='block';
  bx.innerHTML = msg + ' <button class="btn primary" onclick="fixSync()">一键修复文件名</button>';
}

async function fixSync(){
  const r = await fetch('/api/reconcile',{method:'POST'});
  const rep = await r.json();
  if(rep.renamed && rep.renamed.length){
    alert('已修复: ' + rep.renamed.map(i=>i.from+' → '+i.to).join('\n'));
  } else {
    alert('无法自动判定（孤儿/缺失不止一个）。请手动把磁盘文件名改成注册表块名。');
  }
  await load();
}

function render(){
  const q = (document.getElementById('search').value||'').toLowerCase();
  const grid = document.getElementById('grid'); grid.innerHTML='';
  const list = state.blocks.filter(b =>
    !q || (b.block+' '+b.prefix+' '+b.attrs.join(' ')+' '+b.id).toLowerCase().includes(q));
  document.getElementById('nTot').textContent = state.count;
  document.getElementById('nDwg').textContent = state.dwg;
  document.getElementById('nDxf').textContent = state.dxf;
  if(!list.length){ grid.innerHTML='<div class="empty" style="grid-column:1/-1">没有匹配的块</div>'; return; }
  list.forEach(function(b){
    const card = document.createElement('div'); card.className='card';
    card.innerHTML =
      '<div class="preview">'+b.svg+'</div>'+
      '<div class="body">'+
        '<div class="name">'+esc(b.block)+'</div>'+
        '<div class="id">'+esc(b.id)+'</div>'+
        '<div class="pills"><span class="pill prefix">'+esc(b.prefix||'-')+'</span>'+
          b.attrs.map(a=>'<span class="pill">'+esc(a)+'</span>').join('')+'</div>'+
        '<div class="meta">缩放 '+esc(b.xs)+'×'+esc(b.ys)+' · 旋转 '+esc(b.rot)+'°</div>'+
        previewNote(b)+
      '</div>'+
      '<div class="foot"><span class="badge '+b.status+'">'+esc(b.status)+'</span>'+
      '<button class="edit" onclick="editBlock(\''+esc(b.block)+'\')">编辑</button></div>';
    grid.appendChild(card);
  });
}

function previewNote(b){
  if(b.has_dwg && b.preview_stale)
    return '<div class="pnote warn">⚠ 预览取自 .dxf，但 .dwg 更新——请重新导出 .dxf</div>';
  if(b.has_dwg && b.has_dxf)
    return '<div class="pnote">预览取自 .dxf（与 .dwg 同源）</div>';
  if(b.has_dwg)
    return '<div class="pnote warn">仅有 .dwg，无 .dxf 预览</div>';
  if(b.has_dxf)
    return '<div class="pnote">模板 .dxf 预览</div>';
  return '<div class="pnote warn">缺少块文件</div>';
}

function editBlock(name){
  editingIndex = state.blocks.findIndex(b=>b.block===name);
  if(editingIndex<0) return;
  const b = state.blocks[editingIndex];
  editingOldName = b.oldBlock || b.block;
  document.getElementById('dlgTitle').textContent='编辑 ' + b.block;
  document.getElementById('f_id').value=b.id;
  document.getElementById('f_block').value=b.block;
  document.getElementById('f_prefix').value=b.prefix;
  document.getElementById('f_attrs').value=b.attrs.join(';');
  document.getElementById('f_xs').value=b.xs;
  document.getElementById('f_ys').value=b.ys;
  document.getElementById('f_rot').value=b.rot;
  document.getElementById('f_del').style.display='inline-block';
  dlg.showModal();
}

function addBlock(){
  editingIndex = -1;
  editingOldName = '';
  document.getElementById('dlgTitle').textContent='新增块';
  ['f_id','f_block','f_prefix','f_xs','f_ys','f_rot'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('f_attrs').value='TAG';
  document.getElementById('f_xs').value='1';
  document.getElementById('f_ys').value='1';
  document.getElementById('f_rot').value='0';
  document.getElementById('f_del').style.display='none';
  dlg.showModal();
}

function collect(){
  return {
    id: document.getElementById('f_id').value.trim(),
    block: document.getElementById('f_block').value.trim(),
    prefix: document.getElementById('f_prefix').value.trim(),
    attrs: document.getElementById('f_attrs').value.split(/[;,]/).map(s=>s.trim()).filter(Boolean),
    xs: document.getElementById('f_xs').value.trim()||'1',
    ys: document.getElementById('f_ys').value.trim()||'1',
    rot: document.getElementById('f_rot').value.trim()||'0',
  };
}

async function saveBlock(){
  const b = collect();
  if(!b.block){ alert('块名不能为空'); return; }
  b.oldBlock = editingOldName;
  if(editingIndex>=0) state.blocks[editingIndex]=b;
  else state.blocks.push(b);
  dlg.close();
  await persist();
}

async function deleteBlock(){
  if(editingIndex<0) return;
  if(!confirm('确认删除 '+state.blocks[editingIndex].block+' ?')) return;
  state.blocks.splice(editingIndex,1);
  dlg.close();
  await persist();
}

async function persist(){
  const r = await fetch('/api/save',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(state.blocks)});
  if(r.ok){ await load(); } else { alert('保存失败'); }
}

document.getElementById('search').addEventListener('input',render);
load();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/data":
            data = build_data()
            self._send(200, json.dumps(data).encode("utf-8"), "application/json")
        else:
            self._send(200, HTML.encode("utf-8"))

    def do_POST(self):
        if self.path == "/api/save":
            ln = int(self.headers.get("Content-Length", 0))
            try:
                rows = json.loads(self.rfile.read(ln).decode("utf-8"))
                # 只保留需要的字段
                clean = [{k: r.get(k, "") for k in
                          ("id", "block", "prefix", "xs", "ys", "rot", "attrs")}
                         for r in rows]
                # 改名同步：若块名发生变更，重命名 blocks/ 下的同名块文件
                old_by_id = {r["id"]: r["block"] for r in parse_manifest()}
                for r in rows:
                    new = r.get("block", "")
                    old = r.get("oldBlock", "") or old_by_id.get(r.get("id", ""), "")
                    if old and old != new and new:
                        rename_block_file(old, new)
                write_manifest(clean)
                self._send(200, json.dumps({"ok": True}).encode("utf-8"),
                           "application/json")
            except Exception as e:
                self._send(500, json.dumps({"ok": False, "err": str(e)})
                           .encode("utf-8"), "application/json")
        elif self.path == "/api/reconcile":
            try:
                report = reconcile()
                self._send(200, json.dumps(report).encode("utf-8"),
                           "application/json")
            except Exception as e:
                self._send(500, json.dumps({"ok": False, "err": str(e)})
                           .encode("utf-8"), "application/json")
        else:
            self._send(404, b"not found")


def main():
    ap = argparse.ArgumentParser(description="块库数据库可视化看板")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    class ReuseTCPServer(socketserver.TCPServer):
        allow_reuse_address = True
        daemon_threads = True

    # 端口被占用时向后顺延，避免"地址已占用"卡住
    port = args.port
    httpd = None
    for _ in range(20):
        try:
            httpd = ReuseTCPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
    if httpd is None:
        print("没有可用的本地端口，请稍后再试。")
        return
    url = f"http://127.0.0.1:{port}"
    print(f"块库看板已启动: {url}")
    print("数据源: " + MANIFEST)
    rep = reconcile()
    if rep["renamed"]:
        for item in rep["renamed"]:
            print(f"  已修复文件: {item['from']} -> {item['to']} ({'; '.join(item['files'])})")
    if rep["orphans"] or rep["missing"]:
        print("  待处理")
        if rep["orphans"]:
            print("   孤儿文件(磁盘上无对应注册表项): " + ", ".join(rep["orphans"]))
        if rep["missing"]:
            print("   缺失文件(注册表有但磁盘无): " + ", ".join(rep["missing"]))
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
