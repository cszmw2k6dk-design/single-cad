#!/usr/bin/env python3
"""
wiring_ui.py -- Single-CAD UI（本地网站）

界面：左边从块库选块(可重复)，右边组成一条“链”，点“生成连线” ->
      按顺序放块、解插入点、接点对齐、连完线，输出 DXF(保图层) + 预览。

用法:  python wiring_ui.py [--port 8770]
数据:  blocklib/_manifest.csv + blocklib/blocks/*.dxf
输出:  out/wiring_<时间戳>.dxf
"""

import argparse
import datetime
import json
import os
import socketserver
import threading
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler

from sld_generate import Dxf
import blockui_server as ui
import connect_library as cl
import wiring_raw as wr
import array_gen as ag
import cad_draw as cd

try:                                  # 在线更新（可选，缺了也不影响生成）
    import app_update as upd
except Exception:
    upd = None


HERE = os.path.dirname(os.path.abspath(__file__))
BLOCKS_DIR = ui.BLOCKS_DIR
try:                                  # 打包成 exe 后走 runtime_paths（输出落在 exe 旁边）
    import runtime_paths as _rp
    OUTDIR = _rp.OUTDIR
    FRAMES_DIR = _rp.FRAMES_DIR
except Exception:                     # 源码运行：维持原样
    OUTDIR = os.path.join(HERE, "out")
    FRAMES_DIR = os.path.join(HERE, "templates")
DEFAULT_PORT = 8770


def list_frames():
    if not os.path.isdir(FRAMES_DIR):
        return []
    return [f for f in sorted(os.listdir(FRAMES_DIR)) if f.lower().endswith(".dxf")]


def make_server(port0=DEFAULT_PORT, tries=20):
    """起一个多线程 HTTP 服务。端口被占就从 port0 往后试，返回 (httpd, port)。

    多线程的原因：生成请求跑着的时候，界面上的进度轮询还得能进来。
    """
    class S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    port = port0
    for _ in range(tries):
        try:
            return S(("127.0.0.1", port), Handler), port
        except OSError:
            port += 1
    raise OSError("端口 %d 起不来（%d 个都被占用）" % (port0, tries))


CODE_FILES = ("wiring_raw.py", "wiring_ui.py", "array_gen.py",
              "connect_library.py", "blockui_server.py", "blockpack.py")


def code_version():
    """最近一次改代码的时间。界面上会显示，用来一眼看出跑的是不是旧进程。"""
    ts = 0.0
    for f in CODE_FILES:
        p = os.path.join(HERE, f)
        if os.path.exists(p):
            ts = max(ts, os.path.getmtime(p))
    return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S")


LOAD_VER = code_version()      # 本进程启动时代码的版本


def maybe_reload():
    """代码在本进程启动之后改过 → 自动重载那几个模块，省得反复重启对不上。

    注意：wiring_ui.py 自己改不了自己（正在跑的就是它），那一部分还是要重启；
    但排版/生成/CAD 这些都在别的模块里，自动重载就能生效。
    """
    global LOAD_VER
    now = code_version()
    if now == LOAD_VER:
        return None
    import importlib
    for name in ("connect_library", "blockui_server", "blockpack",
                 "wiring_raw", "array_gen", "cad_draw"):
        m = sys.modules.get(name)
        if m is not None:
            try:
                importlib.reload(m)
            except Exception:
                pass
    LOAD_VER = now
    return "已自动重载改过的代码（%s）" % now

# ----------------------------- 进度 -----------------------------
PROGRESS = {"pct": 0, "stage": "", "running": False, "seq": 0, "tail": []}


def set_progress(pct, stage="", tail=None):
    PROGRESS["pct"] = int(max(0, min(100, pct)))
    PROGRESS["stage"] = stage
    if tail is not None:
        PROGRESS["tail"] = [str(x) for x in list(tail)[-8:]]
    PROGRESS["seq"] += 1


def progress_cb(pct, stage=""):
    set_progress(pct, stage)



def stale_warning():
    """代码在本进程启动之后又改过了 → 让界面直接说清楚，别让人对着旧代码找 bug。"""
    now = code_version()
    if now != LOAD_VER:
        return ("⚠ 代码在你启动之后又改过了（启动时 %s，现在 %s）："
                "**关掉这个黑窗口、重新跑一次 python wiring_ui.py**，"
                "否则跑的还是旧代码，报错会误导人。" % (LOAD_VER, now))
    return None


# ----------------------------- 生成逻辑 -----------------------------
def build_chain(chain, gap=40.0, match_span=True, show_len=True):
    """chain: 块名列表(按顺序)。返回 (dxf, log)。"""
    log = []
    insts = []
    for name in chain:
        prims = cl.flatten(name)
        pts = [(p["x"], p["y"]) for p in ui.capture_points(name)]
        if not prims or not pts:
            log.append("跳过(无几何/连接点): " + name)
            continue
        insts.append({"name": name, "prims": prims, "pts": pts})
    if not insts:
        return None, ["没有可用块"]

    # 可选：把每块“右侧接点间距”缩放到一致，保证线水平
    if match_span:
        base_r = cl.side_ports(insts[0]["prims"], insts[0]["pts"], "right")
        target = (base_r[0][1] - base_r[-1][1]) if len(base_r) >= 2 else 0.0
        if target > 1e-6:
            for it in insts:
                r = cl.side_ports(it["prims"], it["pts"], "right")
                if len(r) >= 2:
                    sp = r[0][1] - r[-1][1]
                    if sp > 1e-6:
                        s = target / sp
                        it["prims"] = cl.scale_prims(it["prims"], s)
                        it["pts"] = [(x * s, y * s) for x, y in it["pts"]]

    def match_pairs(a_pts, b_pts):
        """按 Y 就近，把两组接点一一配对(贪心)。返回 [(i, j), ...]。"""
        cand = sorted((abs(a[1] - b[1]), i, j)
                      for i, a in enumerate(a_pts)
                      for j, b in enumerate(b_pts))
        pi, pj, res = set(), set(), []
        for d, i, j in cand:
            if i in pi or j in pj:
                continue
            pi.add(i); pj.add(j); res.append((i, j))
        return res

    dxf = Dxf()
    prev_outs = None      # 上一块右侧接点(已放到图上的绝对坐标)
    prev_right = None     # 上一块几何右边界 x
    for idx, it in enumerate(insts):
        r = cl.side_ports(it["prims"], it["pts"], "right")   # 局部
        l = cl.side_ports(it["prims"], it["pts"], "left")    # 局部
        if idx == 0:
            P = (0.0, 0.0)
        else:
            # 按 Y 就近配对，取“上一块接点Y - 本块接点Y”的中位数当纵向偏移
            pr = match_pairs(prev_outs, l)
            offs = sorted(prev_outs[i][1] - l[j][1] for (i, j) in pr)
            off = offs[len(offs) // 2] if offs else 0.0
            b_local = cl.bbox(it["prims"])
            P = (prev_right + gap - b_local[0], off)   # 左边界在上一块右侧+间隔
        placed = cl.move_prims(it["prims"], *P)
        cl.emit(dxf, placed)
        b = cl.bbox(placed)
        outs = [(x + P[0], y + P[1]) for x, y in r]
        lins = [(x + P[0], y + P[1]) for x, y in l]
        if idx > 0:
            pr2 = match_pairs(prev_outs, lins)
            for (i, j) in pr2:
                pa = prev_outs[i]
                pb = lins[j]
                dxf.line(pa[0], pa[1], pb[0], pb[1], "WIRE")
                L = ((pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2) ** 0.5
                if show_len:
                    h = max(4.0, gap * 0.15)
                    dxf.text((pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0 + h * 1.3,
                             "%.1f" % L, h, "TEXT")
                log.append("  线%d: 长 %.2f (由插入位置得出)" % (i + 1, L))
            log.append("%s -> %s : 连 %d 条线" %
                       (insts[idx-1]["name"], it["name"], len(pr2)))
        log.append("放块 %s 于 (%.2f, %.2f)" % (it["name"], P[0], P[1]))
        prev_outs = outs
        prev_right = b[1]
    return dxf, log


def dxf_to_svg(dxf):
    minx, maxx, miny, maxy = dxf.minx, dxf.maxx, dxf.miny, dxf.maxy
    W, H, pad = 1000, 600, 16
    sw = max(maxx - minx, 1e-6)
    sh = max(maxy - miny, 1e-6)
    sc = min((W - 2 * pad) / sw, (H - 2 * pad) / sh)

    def X(x):
        return pad + (x - minx) * sc

    def Y(y):
        return H - pad - (y - miny) * sc

    parts = []
    for e in dxf.ents:
        if e[1] == "LINE":
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                         'stroke="#1c1c1c" stroke-width="1"/>'
                         % (X(float(e[5])), Y(float(e[7])),
                            X(float(e[11])), Y(float(e[13]))))
        elif e[1] == "CIRCLE":
            parts.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" '
                         'stroke="#1c1c1c" stroke-width="1"/>'
                         % (X(float(e[5])), Y(float(e[7])), float(e[11]) * sc))
    return ('<svg viewBox="0 0 %d %d" style="width:100%%;background:#fff;'
            'border:1px solid #e6e8ee;border-radius:10px">' % (W, H)
            + "".join(parts) + "</svg>")


def list_blocks():
    names = []
    if os.path.isdir(BLOCKS_DIR):
        for f in sorted(os.listdir(BLOCKS_DIR)):
            if f.lower().endswith(".dxf"):
                names.append(os.path.splitext(f)[0])
    return names


def app_version():
    """给界面显示的版本号：在线更新过就显示更新后的版本。"""
    if upd is not None:
        try:
            return upd.local_version(), upd.local_note()
        except Exception:
            pass
    return code_version(), "内置版本"


# ----------------------- 桌面窗口里的文件操作 -----------------------
# 打包成桌面窗口（pywebview）后，界面里的 <a download> 点了没反应 —— WebView2
# 不会像浏览器那样弹下载框。所以这几件事改由程序自己做：
#   保存到桌面 / 打开输出文件夹 / 用默认程序打开（DXF 一般直接进 ZWCAD）
def _desktop_dir():
    for p in (os.path.join(os.path.expanduser("~"), "Desktop"),
              os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop"),
              os.path.expanduser("~")):
        if os.path.isdir(p):
            return p
    return os.path.expanduser("~")


def _safe_out_file(name):
    """只允许碰输出目录里的文件（防目录穿越）。"""
    p = os.path.join(OUTDIR, os.path.basename(name or ""))
    return p if os.path.isfile(p) else None


def export_file(name, where="desktop"):
    """把输出文件复制到桌面/下载目录，返回 (ok, 说明或目标路径)。"""
    src = _safe_out_file(name)
    if not src:
        return False, "找不到文件：%s" % name
    if where == "downloads":
        dst_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        if not os.path.isdir(dst_dir):
            dst_dir = _desktop_dir()
    else:
        dst_dir = _desktop_dir()
    dst = os.path.join(dst_dir, os.path.basename(src))
    try:
        i = 1
        base, ext = os.path.splitext(os.path.basename(src))
        while os.path.exists(dst):          # 重名就加序号，不覆盖用户已有文件
            dst = os.path.join(dst_dir, "%s (%d)%s" % (base, i, ext))
            i += 1
        import shutil as _sh
        _sh.copy2(src, dst)
        return True, dst
    except Exception as ex:
        return False, "复制失败：%s: %s" % (type(ex).__name__, ex)


def reveal_file(name=""):
    """在资源管理器里定位输出文件（没给名字就打开输出目录）。"""
    import subprocess
    p = _safe_out_file(name) if name else None
    try:
        if p:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])
        else:
            os.makedirs(OUTDIR, exist_ok=True)
            os.startfile(OUTDIR)                      # noqa: S606（Windows 专用）
        return True, p or OUTDIR
    except Exception as ex:
        return False, "%s: %s" % (type(ex).__name__, ex)


def open_with_default(name):
    """用系统默认程序打开输出文件（DXF 通常会直接进 ZWCAD）。"""
    p = _safe_out_file(name)
    if not p:
        return False, "找不到文件：%s" % name
    try:
        os.startfile(p)                               # noqa: S606
        return True, p
    except Exception as ex:
        return False, "%s: %s" % (type(ex).__name__, ex)


# ----------------------------- HTTP -----------------------------
HTML = r"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Single-CAD</title>
<style>
 /* 配色/控件风格参照用户另一套 CAD-MAP 编排器的 QSS（深色 + 蓝色主色 + 橙色强调点） */
 :root{--bg:#131415;--card:#17181a;--field:#1f2124;--log:#101113;--line:#2a2c2e;
       --ink:#DADFE3;--ink2:#B9BEC3;--muted:#727577;--hint:#5c6064;
       --brand:#0432FA;--brand2:#0a46ff;--accent:#F5A800}
 *{box-sizing:border-box}
 body{margin:0;font-family:"Microsoft YaHei","SimHei","Segoe UI",system-ui,sans-serif;
      font-size:14px;background:var(--bg);color:var(--ink)}
 ::-webkit-scrollbar{width:10px;height:10px}
 ::-webkit-scrollbar-thumb{background:#2a2c2e;border-radius:8px}
 ::-webkit-scrollbar-track{background:transparent}
 header{background:var(--bg);border-bottom:1px solid var(--line);padding:14px 24px}
 header .logo{color:var(--brand);font-size:30px;font-weight:800;letter-spacing:-1px}
 header h1{margin:0;font-size:17px;font-weight:600}
 header .sub{font-size:12px;color:var(--muted);margin-top:4px}
 #verTxt{color:var(--muted);font-size:12px}
 #updMsg{color:var(--accent);font-size:12px}
 .wrap{display:grid;grid-template-columns:1.05fr 1fr;gap:16px;padding:16px 24px 8px}
 .panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
 .panel h2{margin:0 0 12px;font-size:16px;font-weight:600;display:flex;align-items:center;gap:8px}
 .panel h2::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--accent);
                   flex:0 0 auto}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;
       max-height:330px;overflow:auto}
 .bcard{background:var(--field);border:1px solid var(--line);border-radius:8px;padding:8px;
        text-align:center;cursor:pointer;transition:border-color .15s,background .15s}
 .bcard:hover{border-color:var(--brand);background:rgba(4,50,250,.10)}
 .bcard .nm{font-size:12px;margin-top:4px;color:var(--ink)}
 .bcard .badge{display:inline-block;margin-left:5px;padding:0 5px;border-radius:8px;
               font-size:10px;background:rgba(4,50,250,.18);color:#9fc0ff;vertical-align:1px}
 .bcard .thumb{background:#eef1f5;border-radius:6px;padding:4px 2px;display:flex;
        align-items:center;justify-content:center;height:82px;overflow:hidden}
 .bcard svg{max-width:100%;max-height:74px}
 .chain{display:flex;flex-wrap:wrap;gap:8px;min-height:66px;padding:10px;
        background:var(--log);border:1px dashed var(--line);border-radius:8px}
 .chain>span{color:var(--hint)!important}
 .chip{background:rgba(4,50,250,.15);color:#9fc0ff;border:1px solid rgba(4,50,250,.35);
       border-radius:20px;padding:5px 12px;font-size:13px;display:flex;gap:8px;align-items:center}
 .chip b{cursor:pointer;color:#ff8a80}
 .row{display:flex;gap:12px;align-items:center;margin-top:12px;flex-wrap:wrap}
 .row label{font-size:13px;color:var(--muted)}
 input[type=number],input[type=text],select{background:var(--field);border:1px solid var(--line);
       border-radius:8px;padding:6px 10px;color:var(--ink);font-size:13px;font-family:inherit}
 input[type=number]{width:90px}
 input[type=text]:focus,input[type=number]:focus,select:focus{outline:none;border-color:var(--brand)}
 input[type=checkbox],input[type=radio]{accent-color:var(--brand)}
 button{background:var(--brand);color:#fff;border:0;border-radius:8px;padding:8px 16px;
        font-size:14px;cursor:pointer;font-weight:600;font-family:inherit}
 button:hover{background:var(--brand2)}
 button.ghost{background:rgba(255,255,255,.06);color:var(--ink);font-weight:500;
        border:1px solid rgba(255,255,255,.10)}
 button.ghost:hover{background:rgba(255,255,255,.12)}
 .out{margin-top:12px}
 a.dl{display:inline-block;margin-top:8px;color:#9fc0ff}
 .dlrow{margin-top:10px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
 .dlrow button{padding:6px 14px;font-size:13px}
 .dlrow a.dl{margin-top:0}
 #log{white-space:pre-wrap;font-size:12px;color:#a8adb2;margin-top:10px;padding:10px 12px;
      background:var(--log);border:1px solid var(--line);border-radius:8px;max-height:260px;overflow:auto;
      font-family:Consolas,"Microsoft YaHei",monospace}
 #progWrap{background:var(--card);border:1px solid var(--line);border-radius:10px;
      padding:10px 12px;margin-top:12px}
 #progTxt{color:var(--ink2)}
 #progLog{background:var(--log);border:1px solid var(--line);border-radius:8px;color:#a8adb2;
      font-family:Consolas,"Microsoft YaHei",monospace}
 .out .dlrow{background:var(--log);border:1px solid var(--line);border-radius:8px;padding:8px 10px}
</style></head>
<body>
<header>
  <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
    <span class="logo">SC</span>
    <h1 style="flex:0 0 auto">Single-CAD</h1>
    <div style="flex:1 1 320px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:flex-end">
      <span id="verTxt">版本 {{VER}}（{{VERNOTE}}）</span>
      <button class="ghost" id="updBtn" onclick="checkUpdate()"
              style="padding:5px 12px;font-size:13px">检查更新</button>
      <span id="updMsg"></span>
    </div>
  </div>
  <div class="sub">选块（可重复）→ 组成链 → 生成连完线的产品
    · 代码版本 {{VER}}（换过代码要重启窗口，否则跑的还是旧代码）</div>
</header>
<div class="wrap">
  <div class="panel"><h2>① 块库（点击加入链）</h2>
    <div class="grid" id="blocks"></div></div>
  <div class="panel"><h2>② 链（按顺序摆放并连线）</h2>
    <div class="chain" id="chain"><span style="color:#aab">点左边块加入…</span></div>
    <div class="row">
      <label>模式</label>
      <label><input type="radio" name="mode" value="chain" checked onchange="setMode('chain')"> 单个链</label>
      <label><input type="radio" name="mode" value="array" onchange="setMode('array')"> 光伏阵列 + 线束</label>
      <label><input type="radio" name="mode" value="batch" onchange="setMode('batch')"> 批量（一行一张·逐张独立）</label>
    </div>
    <div class="row" id="arrayRow" style="display:none">
      <label>组件 首块</label><select id="mod1"></select>
      <label>中间块</label><select id="mod2"></select>
      <label>尾块</label><select id="mod3"></select>
      <label>每串板数</label><input type="number" id="nper" value="20" min="2" step="1">
      <label>串数</label><input type="number" id="nstr" value="4" min="1" step="1">
      <label>板间净空</label><input type="number" id="gapx" value="2" step="1">
      <label>串间净空</label><input type="number" id="gapy" value="2" step="1">
      <label>串的排法</label><select id="dir">
        <option value="right">从左往右接</option>
        <option value="down">从上往下叠</option></select>
      <label><input type="checkbox" id="link"> 画阵列↔线束跨接线</label>
      <label><input type="checkbox" id="hspan" checked> 线束接点对齐缩放</label>
      <label>正极支线块</label><select id="posfeed"><option value="">（选链里的块）</option></select>
      <label>负极支线块</label><select id="negfeed"><option value="">（不指定）</option></select>
      <label>主线线号</label><input type="text" id="awgmain" value="2/0 AWG" style="width:100px">
      <label>支线线号</label><input type="text" id="awgbranch" value="6 AWG" style="width:100px">
      <label>线号标注</label><select id="annot"
        title="text=普通文字（最稳）；shape=画成标注外观（尺寸线/界线/箭头，普通实体，任何 CAD 都能开）；dim=CAD 原生 DIMENSION（可拖动关联，但 ZWCAD 2025 会判无效）">
        <option value="text">文字</option>
        <option value="shape">标注外观（普通实体）</option>
        <option value="dim">CAD 原生标注(DIMENSION)</option>
      </select>
      <label>线束缩放</label><input type="number" id="hscale" value="1" step="0.1" min="0.05">
      <label>FUSE间距</label><input type="number" id="fixgap" value="30" step="5">
      <label>起始块</label><input type="text" id="headblk" value="CBX" style="width:70px" title="摆在阵列最左边、与板子固定距离的块">
      <label>起始块间距</label><input type="number" id="headgap" value="60" step="5">
      <label>负极支线旋转</label><input type="number" id="negrot" value="0" step="90"
             title="负极支线块转多少度：0=正放（插头朝上，和正极行一样，推荐）；180=翻过来挂（块看着是倒的）。公头/母头块不看这个值，自动朝链内">
      <label>负极行间距</label><input type="number" id="neggap" value="30" step="5"
             title="负极行和正极行的净空；负极支线块是竖的，程序会自动把它的身子让出来（行线再往下挪一个块高），不会压住正极行">
      <label>末端公头块</label><input type="text" id="posplug" value="Male" style="width:80px"
             title="填了就自动补到链尾、顶最后一串的正极；想让它排在头部就把这里清空、自己放进链里">
      <label>末端母头块</label><input type="text" id="negplug" value="Fmale" style="width:80px"
             title="填了才会自动生成负极那一行（头部公头 + 中间负极支线 + 末端母头）">
      <label><input type="checkbox" id="enlarge"> 允许放大到占满</label>
      <label>保留手工层</label><select id="keepfrom"><option value="">不保留</option></select>
      <label><input type="checkbox" id="tocad"> 直接画到 CAD(COM)</label>
      <label><input type="checkbox" id="cadorig"> 画在原外框文件上（默认画副本）</label>
    </div>
    <div class="row" id="batchRow" style="display:none;flex-direction:column;align-items:stretch">
      <div class="row" style="margin-top:0">
        <label>份数</label><input type="number" id="bcount" value="3" min="1" max="60" step="1" style="width:70px">
        <label>起始串数</label><input type="number" id="bstart" value="3" min="1" step="1" style="width:70px">
        <label>每张 +</label><input type="number" id="bstep" value="1" step="1" style="width:70px">
        <label>每串板数</label><input type="number" id="bnper" value="20" min="1" step="1" style="width:80px">
        <label>起始图号</label><input type="text" id="bno" value="SLD-001" style="width:110px">
        <button class="ghost" onclick="fillBatch()">按上面参数铺出 N 行</button>
        <button class="ghost" onclick="clearBatch()">清空行</button>
      </div>
      <div style="max-height:250px;overflow:auto;border:1px solid var(--line);border-radius:8px">
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <thead><tr>
            <th style="text-align:left;padding:6px">图号</th>
            <th style="text-align:left;padding:6px">外框图</th>
            <th style="text-align:left;padding:6px">串数</th>
            <th style="text-align:left;padding:6px">每串板数</th>
            <th style="text-align:left;padding:6px">备注</th><th></th>
          </tr></thead>
          <tbody id="bBody"></tbody>
        </table>
      </div>
      <div class="row" style="margin-top:0">
        <button onclick="genBatch()">生成全部（每行一张）</button>
        <span style="font-size:12px;color:var(--muted)">
          一行 = 一张图：每张都重新调一次外框模板，参数互不影响。
          左侧块链和上面那套间距/标注/支线块是各行的公共参数。</span>
      </div>
    </div>
    <div class="row">
      <label>间隔 GAP</label><input type="number" id="gap" value="40" step="5">
      <span class="chainOnly"><label>占框比例%</label><input type="number" id="fit" value="55" step="5" min="10" max="100"></span>
      <span class="chainOnly"><label><input type="checkbox" id="match" checked> 自动缩放对齐接点</label></span>
      <span class="chainOnly"><label><input type="checkbox" id="showlen" checked> 标注线长</label></span>
      <span class="chainOnly"><label><input type="checkbox" id="raw" checked> 不展平(保留原始实体)</label></span>
      <label>外框图</label><select id="frame" onchange="onFrameChange()"><option value="">不用</option></select>
      <button onclick="gen()">生成连线</button>
      <button class="ghost" onclick="clearChain()">清空</button>
    </div>
    <div class="out" id="out"></div>
      <div id="progWrap" style="display:none">
        <div style="height:8px;background:rgba(255,255,255,.12);border-radius:8px;overflow:hidden">
          <div id="progBar" style="height:100%;width:0%;background:var(--brand);border-radius:8px;transition:width .25s"></div>
        </div>
        <div id="progTxt" style="font-size:13px;margin-top:6px;font-weight:600"></div>
        <pre id="progLog" style="display:none;margin:8px 0 0;padding:8px 10px;max-height:170px;
             overflow:auto;font-size:12px;line-height:1.5;white-space:pre-wrap"></pre>
      </div>
  </div>
</div>
<script>
let chain=[];
let lastOut={dxf:'', csv:''};   // 最近一次生成的文件名（桌面窗口的“保存/打开”要用）
let framesReady=false;
let lastBlocks=[];      // 块库里所有块名（给“正极/负极支线块”下拉用）
let mode='chain';
let frameList=[];       // 外框图列表（批量模式的外框图下拉要用）
let batchRows=[];       // 批量模式：一行 = 一张图（各自独立参数）
function setMode(m){
  mode=m;
  const arr=(m==='array'||m==='batch');
  document.getElementById('arrayRow').style.display=arr?'flex':'none';
  document.getElementById('batchRow').style.display=(m==='batch')?'flex':'none';
  document.querySelectorAll('.chainOnly').forEach(e=>{e.style.display=arr?'none':''});
  document.querySelectorAll('h2')[1].textContent=(m==='chain')
    ? '② 链（按顺序摆放并连线）'
    : ('② 线束（可以不填：留空自动排“末端母头 + 正极支线×(串数-1) + 末端公头”；'
       + '想带保险丝等串联块就把块点上来）');
}
async function loadBlocks(){
  const fs=document.getElementById('frame');
  const fr=fs.value;
  const r=await fetch('/api/blocks'+(fr?('?frame='+encodeURIComponent(fr)):'')); const d=await r.json();
  if(!framesReady){
    fs.innerHTML='<option value="">不用</option>';
    frameList=(d.frames||[]).slice();
    (d.frames||[]).forEach(f=>{ const o=document.createElement('option'); o.value=f; o.textContent=f; fs.appendChild(o); });
    framesReady=true;
    if((d.frames||[]).length){ fs.value=d.frames[0]; return loadBlocks(); }
  }
  const picks=[['mod1','PV-POS'],['mod2','MIDDLE-PV'],['mod3','END-NEG']];
  picks.forEach(([id,def])=>{
    const ms=document.getElementById(id);
    if(!ms || ms.options.length) return;
    d.blocks.forEach(b=>{ const o=document.createElement('option'); o.value=b.name; o.textContent=b.name; ms.appendChild(o); });
    const want=[...ms.options].find(o=>o.value===def);
    if(want) ms.value=def;
  });
  const kf=document.getElementById('keepfrom');
  if(kf && !kf.dataset.filled){
    (d.out_files||[]).forEach(f=>{ const o=document.createElement('option'); o.value=f; o.textContent=f; kf.appendChild(o); });
    kf.dataset.filled='1';
  }
  const g=document.getElementById('blocks'); g.innerHTML='';
  lastBlocks=(d.blocks||[]).map(b=>b.name);
  d.blocks.forEach(b=>{
    const c=document.createElement('div'); c.className='bcard';
    const badge=(b.src==='lib')?'<span class="badge" title="来自块库，生成时自动并入外框">库</span>':'';
    c.innerHTML='<div class="thumb">'+(b.svg||'')+'</div>'+'<div class="nm">'+b.name+badge+'</div>';
    c.onclick=()=>{chain.push(b.name); renderChain();};
    g.appendChild(c);
  });
}
function onFrameChange(){ chain=[]; renderChain(); loadBlocks(); }
function renderChain(){
  const c=document.getElementById('chain');
  if(!chain.length){c.innerHTML='<span style="color:#aab">点左边块加入…</span>';}
  else{
    c.innerHTML='';
    chain.forEach((n,i)=>{
      const d=document.createElement('div'); d.className='chip';
      d.innerHTML=n+' <b title="移除">×</b>';
      d.querySelector('b').onclick=()=>{chain.splice(i,1);renderChain();};
      c.appendChild(d);
    });
  }
  // “正极/负极支线块”从**整个块库**里选（不是只从链里选，否则没进链的块就没法指定）
  const uniq=[...new Set(chain)];
  const lib=[...new Set(uniq.concat(lastBlocks))];
  [['posfeed','（选块）'],['negfeed','（不指定）']].forEach(function(pair){
    const id=pair[0], blank=pair[1];
    const s=document.getElementById(id); if(!s) return;
    const old=s.value;
    s.innerHTML='<option value="">'+blank+'</option>';
    lib.forEach(function(n){const o=document.createElement('option');o.value=n;o.textContent=n;s.appendChild(o);});
    if(old && lib.includes(old)) s.value=old;
    else if(id==='posfeed' && lib.includes('POS')) s.value='POS';
    else if(id==='negfeed' && lib.includes('NEG')) s.value='NEG';
  });
}
function clearChain(){chain=[];renderChain();document.getElementById('out').innerHTML='';}
// 取输入值：元素不存在（多半是浏览器缓存了旧页面）就返回默认值，绝不抛错
function v(id, dft){ const e=document.getElementById(id); return e? e.value : (dft===undefined?'':dft); }
function ck(id, dft){ const e=document.getElementById(id); return e? e.checked : !!dft; }
let progTimer=null;
function progStart(txt){
  document.getElementById('progWrap').style.display='block';
  document.getElementById('progBar').style.width='0%';
  document.getElementById('progTxt').textContent=txt||'开始…';
  const _pl=document.getElementById('progLog');
  if(_pl){_pl.style.display='none';_pl.textContent='';}
  if(progTimer) clearInterval(progTimer);
  progTimer=setInterval(async ()=>{
    try{
      const r=await fetch('/api/progress'); const d=await r.json();
      document.getElementById('progBar').style.width=(d.pct||0)+'%';
      document.getElementById('progTxt').textContent=(d.pct||0)+'%  '+(d.stage||'');
      const lg=document.getElementById('progLog');
      if(lg && d.tail && d.tail.length){
        lg.style.display='block';
        lg.textContent=d.tail.join('\n');
        lg.scrollTop=lg.scrollHeight;
      }
    }catch(e){}
  },200);
}
function progStop(finalText){
  if(progTimer){clearInterval(progTimer);progTimer=null;}
  document.getElementById('progBar').style.width='100%';
  document.getElementById('progTxt').textContent=finalText||'完成';
  setTimeout(()=>{document.getElementById('progWrap').style.display='none';},1500);
}
// 阵列模式的一组公共参数（阵列模式与批量模式共用；批量模式每行再覆盖串数/板数）
function arrayCommon(){
  return {harness:chain, gap:parseFloat(document.getElementById('gap').value)||40,
          module_first:document.getElementById('mod1').value,
          module_mid:document.getElementById('mod2').value,
          module_last:document.getElementById('mod3').value,
          n_per:parseInt(document.getElementById('nper').value)||20,
          n_strings:parseInt(document.getElementById('nstr').value)||1,
          gap_x:parseFloat(document.getElementById('gapx').value)||0,
          gap_y:parseFloat(document.getElementById('gapy').value)||0,
          dir:document.getElementById('dir').value,
          harness_scale:parseFloat(document.getElementById('hscale').value)||1,
          fixed_gap:parseFloat(document.getElementById('fixgap').value)||30,
          head_block:document.getElementById('headblk').value.trim(),
          head_gap:parseFloat(document.getElementById('headgap').value)||30,
          neg_rotate:(document.getElementById('negrot')?
                      (parseFloat(document.getElementById('negrot').value)||0):0),
          neg_gap:(document.getElementById('neggap')?
                   (parseFloat(document.getElementById('neggap').value)||30):30),
          pos_plug:document.getElementById('posplug').value.trim(),
          neg_plug:document.getElementById('negplug').value.trim(),
          link_array:document.getElementById('link').checked,
          match_span:document.getElementById('hspan').checked,
          pos_feeder:document.getElementById('posfeed').value.trim(),
          neg_feeder:document.getElementById('negfeed').value.trim(),
          awg_main:document.getElementById('awgmain').value.trim(),
          awg_branch:document.getElementById('awgbranch').value.trim(),
          annot:(document.getElementById('annot')||{}).value||'text',
          allow_enlarge:document.getElementById('enlarge').checked};
}

// ---------- 批量（一行 = 一张图，逐张独立） ----------
function pad3(n){ return String(n).padStart(3,'0'); }
function fillBatch(){
  const cnt=Math.max(1,Math.min(60,parseInt(v('bcount',3))||3));
  const s0=Math.max(1,parseInt(v('bstart',3))||1);
  const st=parseInt(v('bstep',1)); const step=isNaN(st)?1:st;
  const np=Math.max(1,parseInt(v('bnper',20))||20);
  const fr=document.getElementById('frame').value||'';
  const raw=(v('bno','SLD-001')||'SLD-001').trim();
  const m=raw.match(/^(.*?)(\d+)\s*$/);          // SLD-001 → 前缀 SLD-、起始 1
  const pre=m?m[1]:raw, n0=m?parseInt(m[2]):1;
  batchRows=[];
  for(let i=0;i<cnt;i++){
    batchRows.push({no:pre+pad3(n0+i), frame:fr,
                    n_str:Math.max(1,s0+i*step), n_per:np, note:''});
  }
  renderBatch();
}
function clearBatch(){ batchRows=[]; renderBatch(); }
function renderBatch(){
  const tb=document.getElementById('bBody'); tb.innerHTML='';
  batchRows.forEach((r,i)=>{
    const tr=document.createElement('tr'); tr.style.borderTop='1px solid var(--line)';
    const td=()=>{const c=document.createElement('td');c.style.padding='4px';tr.appendChild(c);return c;};
    let c=td(), e=document.createElement('input');
    e.type='text'; e.value=r.no; e.style.width='96px'; e.oninput=()=>{r.no=e.value;}; c.appendChild(e);
    c=td(); const s=document.createElement('select'); s.style.maxWidth='240px';
    ['',...(frameList||[])].forEach(f=>{const o=document.createElement('option');o.value=f;o.textContent=f||'（不选）';s.appendChild(o);});
    s.value=r.frame||''; s.onchange=()=>{r.frame=s.value;}; c.appendChild(s);
    c=td(); e=document.createElement('input'); e.type='number'; e.min='1';
    e.value=r.n_str; e.style.width='70px'; e.oninput=()=>{r.n_str=parseInt(e.value)||1;}; c.appendChild(e);
    c=td(); e=document.createElement('input'); e.type='number'; e.min='1';
    e.value=r.n_per; e.style.width='70px'; e.oninput=()=>{r.n_per=parseInt(e.value)||1;}; c.appendChild(e);
    c=td(); e=document.createElement('input'); e.type='text';
    e.value=r.note||''; e.style.width='100%'; e.oninput=()=>{r.note=e.value;}; c.appendChild(e);
    c=td(); const b=document.createElement('button'); b.className='ghost'; b.textContent='删';
    b.onclick=()=>{batchRows.splice(i,1);renderBatch();}; c.appendChild(b);
    tb.appendChild(tr);
  });
}
async function genBatch(){
  if(!batchRows.length){ alert('还没有要画的图：先填份数，点“按上面参数铺出 N 行”'); return; }
  const frame0=document.getElementById('frame').value||'';
  if(!batchRows.some(r=>r.frame||frame0)){ alert('批量模式要先选外框图'); return; }
  document.getElementById('out').innerHTML='生成中…';
  progStart('提交…');
  const body=arrayCommon();
  body.frame=frame0; body.rows=batchRows;
  body.keep_from=document.getElementById('keepfrom').value;
  body.to_cad=document.getElementById('tocad').checked;
  body.cad_original=document.getElementById('cadorig').checked;
  const r=await fetch('/api/generate_batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json();
  progStop('完成');
  const fl=d.files||[];
  lastOut={dxf:(fl[0]?fl[0].name:''), csv:''};
  const rowsHtml=fl.map(f=>
      '<div class="dlrow"><b style="min-width:110px">'+f.no+'</b>'+
      '<button class="ghost" onclick="fileAct(\'open\',\''+f.name+'\')">用CAD打开</button> '+
      '<button class="ghost" onclick="fileAct(\'export\',\''+f.name+'\')">DXF存桌面</button> '+
      (f.csv_url?('<button class="ghost" onclick="fileAct(\'export\',\''+f.csv+'\')">CSV存桌面</button> '):'')+
      (f.url?('<a class="dl" href="'+f.url+'" download>下载DXF</a> '):'')+
      (f.csv_url?('<a class="dl" href="'+f.csv_url+'" download>下载CSV</a>'):'')+
      '</div>').join('');
  document.getElementById('out').innerHTML =
    '<div class="dlrow"><b>共 '+(d.total||fl.length)+' 张，成功 '+fl.length+' 张</b>'+
      (d.zip_url?('<a class="dl" href="'+d.zip_url+'" download>下载全部(zip)</a> '):'')+
      '<button class="ghost" onclick="fileAct(\'reveal\')">打开输出文件夹</button>'+
      '<span id="fileMsg" style="font-size:12px;color:var(--muted);margin-left:8px"></span></div>'+
    rowsHtml+'<div id="log">'+(d.log||[]).join('\n')+'</div>';
}

async function gen(){
  if(mode==='array' && !document.getElementById('frame').value){alert('阵列模式必须先选外框图');return;}
  if(mode==='batch'){ return genBatch(); }
  if(!chain.length && mode==='chain'){alert('先选块');return;}
  document.getElementById('log') && (document.getElementById('log').textContent='');
  document.getElementById('out').innerHTML='生成中…';
  progStart('提交…');
  const gap=parseFloat(document.getElementById('gap').value)||40;
  let url='/api/generate';
  let body={chain:chain, gap:gap,
              fit:(parseFloat(document.getElementById('fit').value)||55)/100.0,
              match_span:document.getElementById('match').checked,
              show_len:document.getElementById('showlen').checked,
              raw:document.getElementById('raw').checked,
              frame:document.getElementById('frame').value};
  if(mode==='array'){
    url='/api/generate_array';
    body=arrayCommon();
    body.frame=document.getElementById('frame').value;
    body.keep_from=document.getElementById('keepfrom').value;
    body.to_cad=document.getElementById('tocad').checked;
    body.cad_original=document.getElementById('cadorig').checked;
  }
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json();
  progStop('完成');
  const dxf=(d.dxf_file||'').split(/[\\/]/).pop();
  const csv=((d.csv_url||'').split('/').pop());
  lastOut={dxf:dxf, csv:decodeURIComponent(csv||'')};
  document.getElementById('out').innerHTML =
    (d.svg||'') +
    '<div class="dlrow">' +
      '<button class="ghost" onclick="fileAct(\'open\')">用默认程序打开(DXF)</button> ' +
      '<button class="ghost" onclick="fileAct(\'export\')">保存到桌面</button> ' +
      '<button class="ghost" onclick="fileAct(\'reveal\')">打开输出文件夹</button> ' +
      (d.csv_url?('<button class="ghost" onclick="fileAct(\'export\',lastOut.csv)">线长清单存到桌面</button> '):'') +
      (d.dxf_url?('<a class="dl" href="'+d.dxf_url+'" download>下载 DXF</a> '):'') +
      (d.csv_url?('<a class="dl" href="'+d.csv_url+'" download>下载线长清单 CSV</a> '):'') +
      '<span id="fileMsg" style="font-size:12px;color:var(--muted);margin-left:8px"></span>' +
    '</div>' +
    '<div id="log">'+(d.log||[]).join('\n')+'</div>';
}
// 打包成桌面窗口时，<a download> 点了没反应（WebView2 不弹下载框），
// 所以窗口里改用程序自己的保存/打开能力；浏览器模式还走原来的下载链接。
// 注意：pywebview 的 api 是页面加载后异步注入的，所以这里用函数现查，别写成常量
function inApp(){ return !!(window.pywebview && window.pywebview.api); }
async function fileAct(act, name){
  const f = name || lastOut.dxf;
  const box = document.getElementById('fileMsg');
  if(!f){ if(box) box.textContent='还没有生成文件'; return; }
  if(box) box.textContent='处理中…';
  try{
    const r = await fetch('/api/file/'+act+'?name='+encodeURIComponent(f));
    const d = await r.json();
    if(box) box.textContent = (d.ok? '✓ ' : '✗ ') +
      (act==='export' ? ('已保存到 ' + d.msg) : (act==='reveal' ? '已在文件夹里定位' : d.msg));
  }catch(e){ if(box) box.textContent='✗ '+e; }
}
loadBlocks();
setMode('chain');

// ---------- 在线更新 ----------
function updMsg(t,color){const e=document.getElementById('updMsg');e.textContent=t;e.style.color=color||'';}
async function checkUpdate(apply){
  const b=document.getElementById('updBtn'); b.disabled=true; updMsg('检查中…');
  try{
    const r=await fetch('/api/update'+(apply?'?apply=1':'')); const d=await r.json();
    if(!d.ok){ updMsg('✗ '+(d.error||'检查失败'),'#ffd7d7'); return; }
    if(d.message) updMsg(d.message, d.applied? '#c8f7d0':'#ffd7d7');
    else if(d.newer) updMsg('发现新版本 '+d.remote+'（当前 '+d.local+'）','#fff3c4');
    else updMsg('已是最新（'+d.local+'）','#c8f7d0');
    if(d.newer && !apply){
      if(confirm('发现新版本 '+d.remote+'（当前 '+d.local+'）\n'+(d.notes||'')+'\n\n现在下载更新吗？\n（下载完关掉窗口重新打开即生效）')){
        return checkUpdate(true);
      }
    }
    if(d.applied){
      document.getElementById('verTxt').textContent='版本 '+d.remote+'（已下载，重启生效）';
      alert('更新已下载完成。\n\n请关掉本窗口，重新双击程序即生效。');
    }
  }catch(e){ updMsg('✗ 网络错误：'+e,'#ffd7d7'); }
  finally{ b.disabled=false; }
}
checkUpdate();     // 启动时静默检查一次（失败不影响使用）
</script>
</body></html>
"""


def array_spec(req, over=None):
    """把界面送来的一堆阵列参数拼成 build_array_frame 要的 spec。

    批量模式每张图参数不同：把这一张要覆盖的字段放进 over（空值不覆盖），
    其余全部沿用界面上那一套（块链、支线块、间距、标注方式……）。
    """
    spec = dict(module=req.get("module", ""), n_per=req.get("n_per", 20),
                module_first=req.get("module_first", ""),
                module_mid=req.get("module_mid", ""),
                module_last=req.get("module_last", ""),
                n_strings=req.get("n_strings", 1), gap_x=req.get("gap_x", 2),
                gap_y=req.get("gap_y", 30), dir=req.get("dir", "right"),
                harness_scale=req.get("harness_scale", 1.0),
                fixed_gap=req.get("fixed_gap", 30.0),
                head_block=req.get("head_block", ""),
                head_gap=req.get("head_gap", 30.0),
                pos_plug=req.get("pos_plug", ""),
                neg_plug=req.get("neg_plug", ""),
                link_array=bool(req.get("link_array")),
                match_span=bool(req.get("match_span", True)),
                harness=req.get("harness", []), gap=req.get("gap", 40),
                pos_feeder=req.get("pos_feeder", ""),
                neg_feeder=req.get("neg_feeder", ""),
                awg_main=req.get("awg_main", ""),
                awg_branch=req.get("awg_branch", ""),
                annot=req.get("annot", "text"),
                neg_rotate=req.get("neg_rotate", 0.0),
                neg_gap=req.get("neg_gap", 30.0),
                allow_enlarge=bool(req.get("allow_enlarge")),
                keep_from=(os.path.join(OUTDIR, os.path.basename(req["keep_from"]))
                           if req.get("keep_from") else ""))
    for k, v in (over or {}).items():
        if v is None or v == "":
            continue
        spec[k] = v
    return spec


def safe_name(s, dflt="SLD"):
    """图号当文件名用：去掉 Windows 不认的字符，空的就用默认名。"""
    out = "".join(("-" if c in '\\/:*?"<>|' else c) for c in str(s or "")).strip(" .")
    return out or dflt


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _api_update(self, path):
        """在线更新接口。

        GET  /api/update           → 查有没有新版本
        GET  /api/update?apply=1   → 下载并装上新版本（装完要重启程序才生效）
        """
        if upd is None:
            self._send(200, json.dumps({"ok": False,
                                        "error": "更新模块不可用（app_update.py 缺失）"}
                                       ).encode("utf-8"), "application/json")
            return
        try:
            if "apply=1" in path:
                set_progress(1, "开始下载更新")
                ok, msg = upd.download_and_install(progress=progress_cb)
                set_progress(100 if ok else 0, "更新下载完成" if ok else "更新失败")
                info = upd.check()
                info.update({"applied": ok, "message": msg})
                self._send(200, json.dumps(info).encode("utf-8"), "application/json")
                return
            self._send(200, json.dumps(upd.check()).encode("utf-8"), "application/json")
        except Exception as ex:
            import traceback
            self._send(200, json.dumps(
                {"ok": False, "error": "%s: %s" % (type(ex).__name__, ex),
                 "trace": traceback.format_exc().strip().splitlines()[-3:]}
            ).encode("utf-8"), "application/json")

    def _api_file(self, path):
        """桌面窗口里的文件操作（浏览器里不需要，浏览器直接下载就行）。

        GET /api/file/export?name=x.dxf&to=desktop|downloads  → 另存到桌面/下载
        GET /api/file/reveal?name=x.dxf                      → 资源管理器里定位文件
        GET /api/file/open?name=x.dxf                        → 用默认程序打开（进 CAD）
        """
        from urllib.parse import unquote, parse_qs
        q = parse_qs(path.split("?", 1)[1]) if "?" in path else {}
        name = unquote((q.get("name") or [""])[0])
        action = path.split("?", 1)[0].rsplit("/", 1)[-1]
        try:
            if action == "export":
                ok, msg = export_file(name, (q.get("to") or ["desktop"])[0])
            elif action == "reveal":
                ok, msg = reveal_file(name)
            elif action == "open":
                ok, msg = open_with_default(name)
            else:
                ok, msg = False, "不认识的操作：%s" % action
        except Exception as ex:
            ok, msg = False, "%s: %s" % (type(ex).__name__, ex)
        self._send(200, json.dumps({"ok": ok, "msg": str(msg),
                                    "name": name, "action": action}
                                   ).encode("utf-8"), "application/json")

    def do_GET(self):
        if self.path.startswith("/api/progress"):
            self._send(200, json.dumps(PROGRESS).encode("utf-8"), "application/json")
            return
        if self.path.startswith("/api/update"):
            self._api_update(self.path)
            return
        if self.path.startswith("/api/file/"):
            self._api_file(self.path)
            return
        if self.path.startswith("/api/blocks"):
            from urllib.parse import unquote
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            frame_name = ""
            for kv in q.split("&"):
                if kv.startswith("frame="):
                    frame_name = unquote(kv[6:])
            # 界面里只列**块库**(blocklib/blocks) 里的块。
            # 以前连外框图模板自带的那一堆老块（防尘塞/L1/T3/CU - AL/lynx 1-4/旧框…）
            # 也一起列了 —— 它们是画在模板 DXF 里的，删块库文件删不掉，所以看着像
            # “老块库没删干净”。生成时用到的块由程序自动从块库并进外框图，
            # 这里的列表只用来挑线束链上的块，不需要把图框自带的块露出来。
            out, in_frame = [], set()
            for name in list_blocks():
                if name in in_frame:
                    continue
                out.append({"name": name, "svg": wr.block_file_svg(name), "src": "lib"})
            outs = []
            if os.path.isdir(OUTDIR):
                fs = [f for f in os.listdir(OUTDIR) if f.lower().endswith(".dxf")]
                fs.sort(key=lambda f: os.path.getmtime(os.path.join(OUTDIR, f)), reverse=True)
                outs = fs[:30]
            self._send(200, json.dumps({"blocks": out, "frames": list_frames(),
                                        "out_files": outs}).encode("utf-8"),
                       "application/json")
        elif self.path.startswith("/out/"):
            fn = os.path.basename(self.path)
            p = os.path.join(OUTDIR, fn)
            if os.path.exists(p):
                ct = ("text/csv; charset=utf-8" if fn.lower().endswith(".csv")
                      else ("application/zip" if fn.lower().endswith(".zip")
                            else "application/dxf"))
                # 带上附件头：浏览器模式下点了就直接下载（窗口模式走 /api/file/*）
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Disposition",
                                 'attachment; filename="%s"' % fn.encode("ascii", "replace").decode())
                self.send_header("Cache-Control", "no-store")
                body = open(p, "rb").read()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._send(404, b"not found")
        else:
            _v, _vn = app_version()
            self._send(200, HTML.replace("{{VER}}", _v).replace("{{VERNOTE}}", _vn)
                       .encode("utf-8"))

    def do_POST(self):
        try:
            self._do_post()
        except Exception as ex:                      # 出错也要回一个可读的结果，
            import traceback                        # 不能让界面一直卡在“生成中…”
            tb = traceback.format_exc().strip().splitlines()
            head = ["生成失败: %s: %s" % (type(ex).__name__, ex)]
            sw = stale_warning()
            if sw:
                head.insert(0, sw)                  # 先说是旧进程，别对着旧代码找 bug
            self._send(200, json.dumps(
                {"svg": "", "log": head + tb[-5:]}
            ).encode("utf-8"), "application/json")

    def _do_post(self):
        if self.path == "/api/generate_array":
            self._generate_array(); return
        if self.path == "/api/generate_batch":
            self._generate_batch(); return
        if self.path != "/api/generate":
            self._send(404, b"not found"); return
        ln = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(ln).decode("utf-8"))
        chain = req.get("chain", [])
        gap = float(req.get("gap", 40))
        try:
            fit = min(max(float(req.get("fit", 0.55)), 0.05), 1.0)
        except (TypeError, ValueError):
            fit = 0.55
        match = bool(req.get("match_span", True))
        show_len = bool(req.get("show_len", True))
        raw = bool(req.get("raw", True))
        frame_name = req.get("frame", "") or ""
        frame_path = os.path.join(FRAMES_DIR, frame_name) if frame_name else None
        if frame_path:
            # 选了外框图：块来自外框，输出=外框字节+内容插入；不做展平预览
            text, log = wr.build_chain_frame(frame_path, chain, gap, match, show_len, fit)
            if not text:
                self._send(200, json.dumps({"svg": "", "log": log}).encode("utf-8"),
                           "application/json"); return
            content = text
            svg = ""
        else:
            dxf, log = build_chain(chain, gap, match, show_len)
            if dxf is None:
                self._send(200, json.dumps({"svg": "", "log": log}).encode("utf-8"),
                           "application/json"); return
            svg = dxf_to_svg(dxf)
            if not raw:
                content = dxf.build({"title": "WIRING"})
            else:
                text, rlog, rent = wr.build_chain_raw(chain, gap, match, show_len)
                if text:
                    content = text
                    log = rlog
                    svg = wr.entities_to_svg(rent)
                else:
                    content = dxf.build({"title": "WIRING"})
        os.makedirs(OUTDIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        fn = "wiring_%s.dxf" % ts
        outpath = os.path.join(OUTDIR, fn)
        with open(outpath, "w", encoding="latin-1", newline="") as f:
            f.write(content)
        if raw and frame_path and content:
            try:
                s2, _o = wr.parse_sections_text(wr.read_dxf_text(outpath))
                g2 = wr.group_entities(s2.get("ENTITIES", []))
                svg = wr.entities_to_svg(g2, wr._blocks_map(s2))
            except Exception:
                pass
        resp = {"svg": svg, "dxf_url": "/out/" + fn, "log": log,
                "dxf_file": outpath}
        self._send(200, json.dumps(resp).encode("utf-8"), "application/json")

    def _generate_array(self):
        """阵列 + 线束（手册第 13 章）：界面上的线束链复用“块库 + 链”两块。"""
        PROGRESS["running"] = True
        set_progress(1, "准备")
        # 进度回调：把当前阶段 + 最近几行日志一起报给界面（画到 CAD 那段尤其需要，
        # 否则 CAD 连上/在画什么，界面上什么都看不到）
        _box = {"log": []}

        def pg(pct, stage):
            set_progress(pct, stage, _box["log"][-8:])
        ln = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(ln).decode("utf-8"))
        frame_name = req.get("frame", "") or ""
        if not frame_name:
            self._send(200, json.dumps({"svg": "", "log": ["阵列模式必须先选外框图"]}
                                       ).encode("utf-8"), "application/json"); return
        spec = array_spec(req)
        text, log, wires = wr.build_array_frame(os.path.join(FRAMES_DIR, frame_name), spec,
                                                log=_box["log"], progress=pg)
        _box["log"] = log          # 后面 CAD 那段继续往这个列表里追加，界面能看到
        if not text:
            PROGRESS["running"] = False
            self._send(200, json.dumps({"svg": "", "log": log}).encode("utf-8"),
                       "application/json"); return
        set_progress(85, "写 DXF 文件")
        pg(85, "写 DXF 文件")
        os.makedirs(OUTDIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        fn = "array_%s.dxf" % ts
        outpath = os.path.join(OUTDIR, fn)
        with open(outpath, "w", encoding="latin-1", newline="") as f:
            f.write(text)
        csv_fn = ""
        try:
            ag.write_csv(os.path.splitext(outpath)[0] + ".csv", wires)
            csv_fn = os.path.splitext(fn)[0] + ".csv"
        except Exception as ex:
            log.append("⚠ 线长清单没写成: %s" % ex)
        if req.get("to_cad"):
            log.append("—— 画到 CAD ——")
            dwg = os.path.join(FRAMES_DIR, os.path.splitext(frame_name)[0] + ".dwg")
            try:
                cd.draw_dxf_into_cad(outpath, dwg, log,
                                     use_original=bool(req.get("cad_original")),
                                     copy_dir=OUTDIR,
                                       only_blocks=set(
                                           [x for x in (spec["module"], spec["module_first"],
                                                       spec["module_mid"], spec["module_last"])
                                            if x] + list(spec["harness"])),
                                       progress=pg)
            except Exception as ex:
                import traceback
                log.append("⚠ 画到 CAD 失败: %s: %s" % (type(ex).__name__, ex))
                log.extend(traceback.format_exc().strip().splitlines()[-4:])
        svg = ""
        try:
            s2, _o = wr.parse_sections_text(wr.read_dxf_text(outpath))
            svg = wr.entities_to_svg(wr.group_entities(s2.get("ENTITIES", [])),
                                     wr._blocks_map(s2))
        except Exception:
            pass
        resp = {"svg": svg, "dxf_url": "/out/" + fn,
                "csv_url": ("/out/" + csv_fn) if csv_fn else "",
                "log": log, "dxf_file": outpath}
        set_progress(100, "完成", log[-8:])
        PROGRESS["running"] = False
        sw = stale_warning()
        if sw:                       # 代码在本进程启动之后改过：成功也要提醒，不然会对不上
            log = [sw] + list(log)
        self._send(200, json.dumps(resp).encode("utf-8"), "application/json")

    def _generate_batch(self):
        """批量（一框一张）：一行 = 一张图。

        每张都**重新调一次外框模板**、参数各自独立（第 1 张 3 串、第 2 张 4 串都行），
        每张单独落一个 DXF（+ 线长 CSV），最后打包一个 zip。
        """
        PROGRESS["running"] = True
        set_progress(1, "准备")
        ln = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(ln).decode("utf-8"))
        rows = [r for r in (req.get("rows") or []) if isinstance(r, dict)]
        log = []
        if not rows:
            PROGRESS["running"] = False
            self._send(200, json.dumps(
                {"files": [], "zip_url": "", "total": 0,
                 "log": ["批量模式还没有要画的图：先填份数、点“铺出 N 行”"]}
            ).encode("utf-8"), "application/json")
            return
        total = len(rows)
        frame0 = req.get("frame", "") or ""
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(OUTDIR, exist_ok=True)
        files = []
        for n, row in enumerate(rows, 1):
            frame_name = (row.get("frame") or frame0 or "").strip()
            no = safe_name(row.get("no"), "SLD-%03d" % n)
            try:                       # 每行可以单独给串数/每串板数，空着就用界面上那套
                over = {"n_strings": int(row.get("n_strings") or 0) or None,
                        "n_per": int(row.get("n_per") or 0) or None}
            except (TypeError, ValueError):
                over = {}
            if not frame_name:
                log.append("—— 第 %d/%d 张 %s：没选外框图，跳过 ——" % (n, total, no))
                continue
            frame_path = os.path.join(FRAMES_DIR, os.path.basename(frame_name))
            spec = array_spec(req, over)
            log = log + ["—— 第 %d/%d 张 %s（%s，%d 串 × %d 块）——"
                         % (n, total, no, os.path.basename(frame_name),
                            spec.get("n_strings", 0), spec.get("n_per", 0))]

            def pg(pct, stage, _n=n, _log=log):
                set_progress(int(((_n - 1) + max(0.0, min(100.0, float(pct))) / 100.0)
                                 * 100.0 / total),
                             "第 %d/%d 张 · %s" % (_n, total, stage), _log[-8:])

            pg(1, "开始")
            text, rlog, wires = wr.build_array_frame(frame_path, spec, log=log, progress=pg)
            log = rlog or log
            if not text:
                log.append("⚠ %s 没画出来，跳过这张" % no)
                continue
            fn = "%s_%s.dxf" % (no, stamp)
            outpath = os.path.join(OUTDIR, fn)
            with open(outpath, "w", encoding="latin-1", newline="") as f:
                f.write(text)
            csv_fn = ""
            try:
                ag.write_csv(os.path.splitext(outpath)[0] + ".csv", wires)
                csv_fn = os.path.splitext(fn)[0] + ".csv"
            except Exception as ex:
                log.append("⚠ 线长清单没写成: %s" % ex)
            if req.get("to_cad"):
                log.append("—— 第 %d/%d 张画到 CAD ——" % (n, total))
                dwg = os.path.join(FRAMES_DIR, os.path.splitext(frame_name)[0] + ".dwg")
                try:
                    cd.draw_dxf_into_cad(
                        outpath, dwg, log,
                        use_original=bool(req.get("cad_original")), copy_dir=OUTDIR,
                        only_blocks=set(
                            [x for x in (spec.get("module", ""), spec.get("module_first", ""),
                                         spec.get("module_mid", ""), spec.get("module_last", ""))
                             if x] + list(spec.get("harness") or [])),
                        progress=pg)
                except Exception as ex:
                    import traceback
                    log.append("⚠ 画到 CAD 失败: %s: %s" % (type(ex).__name__, ex))
                    log.extend(traceback.format_exc().strip().splitlines()[-4:])
            files.append({"no": no, "name": fn, "url": "/out/" + fn,
                          "csv": csv_fn, "csv_url": ("/out/" + csv_fn) if csv_fn else ""})
        zip_name = ""
        if files:
            zip_name = "批量_%s.zip" % stamp
            try:
                with zipfile.ZipFile(os.path.join(OUTDIR, zip_name), "w",
                                     zipfile.ZIP_DEFLATED) as z:
                    for f in files:
                        z.write(os.path.join(OUTDIR, f["name"]), f["name"])
                        if f["csv"]:
                            z.write(os.path.join(OUTDIR, f["csv"]), f["csv"])
            except Exception as ex:
                log.append("⚠ 打包 zip 失败: %s" % ex)
                zip_name = ""
        log.append("批量完成：共 %d 张，成功 %d 张" % (total, len(files)))
        set_progress(100, "批量完成", log[-8:])
        PROGRESS["running"] = False
        sw = stale_warning()
        if sw:
            log = [sw] + list(log)
        self._send(200, json.dumps({"files": files, "total": total,
                                    "zip_url": ("/out/" + zip_name) if zip_name else "",
                                    "zip_file": zip_name,
                                    "log": log}).encode("utf-8"), "application/json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--browser", action="store_true",
                    help="强制用浏览器打开（默认：能开桌面窗口就用桌面窗口）")
    ap.add_argument("--title", default="Single-CAD")
    args = ap.parse_args()

    httpd, port = make_server(args.port)
    url = "http://127.0.0.1:%d" % port
    print("Single-CAD 已启动:", url)
    print("代码版本:", code_version(), "(改过代码要重启本进程才生效)")
    print("块库:", BLOCKS_DIR)
    print("输出:", OUTDIR)

    # ---- 优先开一个**真正的桌面窗口**（pywebview + Edge WebView2）----
    # 打包成 exe 后用户要的是“双击出窗口”，不是“双击开浏览器”。
    # 拿不到窗口能力时（没装 WebView2 / 没装 pywebview）自动退回浏览器，不会开不起来。
    if not args.browser:
        try:
            import webview
        except Exception as ex:
            print("没装桌面窗口组件（%s），改用浏览器打开" % type(ex).__name__)
        else:
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            print("正在打开桌面窗口…（关掉窗口即退出程序）")
            try:
                webview.create_window(args.title, url, width=1280, height=860,
                                      min_size=(960, 640), text_select=True)
                webview.start()          # 阻塞到窗口关闭
            except Exception as ex:
                print("桌面窗口启动失败（%s: %s），改用浏览器打开" % (type(ex).__name__, ex))
            else:
                print("窗口已关闭，程序退出。")
                return

    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
