#!/usr/bin/env python3
"""
wiring_ui.py -- 连线生成器 UI（本地网站）

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
from http.server import BaseHTTPRequestHandler

from sld_generate import Dxf
import blockui_server as ui
import connect_library as cl
import wiring_raw as wr
import array_gen as ag
import cad_draw as cd


HERE = os.path.dirname(os.path.abspath(__file__))
BLOCKS_DIR = ui.BLOCKS_DIR
OUTDIR = os.path.join(HERE, "out")
FRAMES_DIR = os.path.join(HERE, "templates")
DEFAULT_PORT = 8770


def list_frames():
    if not os.path.isdir(FRAMES_DIR):
        return []
    return [f for f in sorted(os.listdir(FRAMES_DIR)) if f.lower().endswith(".dxf")]


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
PROGRESS = {"pct": 0, "stage": "", "running": False, "seq": 0}


def set_progress(pct, stage=""):
    PROGRESS["pct"] = int(max(0, min(100, pct)))
    PROGRESS["stage"] = stage
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


# ----------------------------- HTTP -----------------------------
HTML = r"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>连线生成器</title>
<style>
 :root{--bg:#f5f6f8;--card:#fff;--line:#e6e8ee;--ink:#1b1f27;--brand:#1466c8;--muted:#7b8494}
 *{box-sizing:border-box}
 body{margin:0;font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;background:var(--bg);color:var(--ink)}
 header{background:linear-gradient(90deg,#0f3a75,#1466c8);color:#fff;padding:16px 26px}
 header h1{margin:0;font-size:19px}
 header .sub{font-size:13px;opacity:.85;margin-top:4px}
 .wrap{display:grid;grid-template-columns:1fr 1fr;gap:18px;padding:18px 26px}
 .panel{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}
 .panel h2{margin:0 0 10px;font-size:15px}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;max-height:320px;overflow:auto}
 .bcard{border:1px solid var(--line);border-radius:10px;padding:8px;text-align:center;cursor:pointer}
 .bcard:hover{border-color:var(--brand);box-shadow:0 4px 14px rgba(20,60,120,.12)}
 .bcard .nm{font-size:12px;margin-top:4px}
 .bcard .badge{display:inline-block;margin-left:5px;padding:0 5px;border-radius:8px;
               font-size:10px;background:#e8f6ec;color:#1e7a41;vertical-align:1px}
 .bcard svg{max-width:100%;height:70px}
 .chain{display:flex;flex-wrap:wrap;gap:8px;min-height:44px;padding:8px;border:1px dashed var(--line);border-radius:10px}
 .chip{background:#eef2fb;color:#2b5ba8;border-radius:20px;padding:5px 12px;font-size:13px;display:flex;gap:8px;align-items:center}
 .chip b{cursor:pointer;color:#c0392b}
 .row{display:flex;gap:12px;align-items:center;margin-top:12px;flex-wrap:wrap}
 .row label{font-size:13px;color:var(--muted)}
 input[type=number]{width:90px;padding:6px 8px;border:1px solid var(--line);border-radius:8px}
 input[type=text],select{padding:6px 8px;border:1px solid var(--line);border-radius:8px;font-size:13px}
 button{background:var(--brand);color:#fff;border:0;border-radius:9px;padding:9px 18px;font-size:14px;cursor:pointer;font-weight:600}
 button.ghost{background:#eef0f4;color:var(--ink);font-weight:500}
 .out{margin-top:12px}
 a.dl{display:inline-block;margin-top:8px;color:var(--brand)}
 #log{white-space:pre-wrap;font-size:12px;color:var(--muted);margin-top:8px}
</style></head>
<body>
<header><h1>连线生成器</h1>
<div class="sub">选块（可重复）→ 组成链 → 生成连完线的产品
  · 代码版本 {{VER}}（换过代码要重启这个窗口，否则跑的还是旧代码）</div></header>
<div class="wrap">
  <div class="panel"><h2>① 块库（点击加入链）</h2>
    <div class="grid" id="blocks"></div></div>
  <div class="panel"><h2>② 链（按顺序摆放并连线）</h2>
    <div class="chain" id="chain"><span style="color:#aab">点左边块加入…</span></div>
    <div class="row">
      <label>模式</label>
      <label><input type="radio" name="mode" value="chain" checked onchange="setMode('chain')"> 单个链</label>
      <label><input type="radio" name="mode" value="array" onchange="setMode('array')"> 光伏阵列 + 线束</label>
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
      <label>线束缩放</label><input type="number" id="hscale" value="1" step="0.1" min="0.05">
      <label>FUSE间距</label><input type="number" id="fixgap" value="30" step="5">
      <label>起始块</label><input type="text" id="headblk" value="CBX" style="width:70px" title="摆在阵列最左边、与板子固定距离的块">
      <label>起始块间距</label><input type="number" id="headgap" value="60" step="5">
      <label>负极行旋转</label><input type="number" id="negrot" value="180" step="90"
             title="负极那一行整体转多少度（180 是把接点朝向翻过来；不对就填 0 或 90）">
      <label>负极行间距</label><input type="number" id="neggap" value="30" step="5">
      <label>末端公头块</label><input type="text" id="posplug" value="Male" style="width:80px"
             title="填了就自动补到链尾、顶最后一串的正极；想让它排在头部就把这里清空、自己放进链里">
      <label>末端母头块</label><input type="text" id="negplug" value="Fmale" style="width:80px"
             title="填了才会自动生成负极那一行（头部公头 + 中间负极支线 + 末端母头，整体旋转 180°）">
      <label><input type="checkbox" id="enlarge"> 允许放大到占满</label>
      <label>保留手工层</label><select id="keepfrom"><option value="">不保留</option></select>
      <label><input type="checkbox" id="tocad"> 直接画到 CAD(COM)</label>
      <label><input type="checkbox" id="cadorig"> 画在原外框文件上（默认画副本）</label>
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
    <div id="progWrap" style="display:none;margin-top:10px">
      <div style="height:10px;background:#eef0f4;border-radius:6px;overflow:hidden">
        <div id="progBar" style="height:100%;width:0%;background:linear-gradient(90deg,#0f3a75,#1466c8);transition:width .25s"></div>
      </div>
      <div id="progTxt" style="font-size:12px;color:#7b8494;margin-top:5px"></div>
    </div>
  </div>
</div>
<script>
let chain=[];
let framesReady=false;
let lastBlocks=[];      // 块库里所有块名（给“正极/负极支线块”下拉用）
let mode='chain';
function setMode(m){
  mode=m;
  document.getElementById('arrayRow').style.display=(m==='array')?'flex':'none';
  document.querySelectorAll('.chainOnly').forEach(e=>{e.style.display=(m==='array')?'none':''});
  document.querySelectorAll('h2')[1].textContent=(m==='array')
    ? '② 线束（点左边块组成线束链，一根正极支线对一串）'
    : '② 链（按顺序摆放并连线）';
}
async function loadBlocks(){
  const fs=document.getElementById('frame');
  const fr=fs.value;
  const r=await fetch('/api/blocks'+(fr?('?frame='+encodeURIComponent(fr)):'')); const d=await r.json();
  if(!framesReady){
    fs.innerHTML='<option value="">不用</option>';
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
    c.innerHTML=(b.svg||'')+'<div class="nm">'+b.name+badge+'</div>';
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
  if(progTimer) clearInterval(progTimer);
  progTimer=setInterval(async ()=>{
    try{
      const r=await fetch('/api/progress'); const d=await r.json();
      document.getElementById('progBar').style.width=(d.pct||0)+'%';
      document.getElementById('progTxt').textContent=(d.pct||0)+'%  '+(d.stage||'');
    }catch(e){}
  },200);
}
function progStop(finalText){
  if(progTimer){clearInterval(progTimer);progTimer=null;}
  document.getElementById('progBar').style.width='100%';
  document.getElementById('progTxt').textContent=finalText||'完成';
  setTimeout(()=>{document.getElementById('progWrap').style.display='none';},1500);
}
async function gen(){
  if(mode==='array' && !document.getElementById('frame').value){alert('阵列模式必须先选外框图');return;}
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
    body={harness:chain, gap:gap, frame:document.getElementById('frame').value,
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
                      (parseFloat(document.getElementById('negrot').value)||0):180),
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
          allow_enlarge:document.getElementById('enlarge').checked};
    body.keep_from=document.getElementById('keepfrom').value;
    body.to_cad=document.getElementById('tocad').checked;
    body.cad_original=document.getElementById('cadorig').checked;
  }
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json();
  progStop('完成');
  document.getElementById('out').innerHTML =
    (d.svg||'') + '<div><a class="dl" href="'+d.dxf_url+'" download>下载 DXF</a>' +
    (d.csv_url?('  <a class="dl" href="'+d.csv_url+'" download>下载线长清单 CSV</a>'):'') + '</div>' +
    '<div id="log">'+(d.log||[]).join('\n')+'</div>';
}
loadBlocks();
setMode('chain');
</script>
</body></html>
"""


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

    def do_GET(self):
        if self.path.startswith("/api/progress"):
            self._send(200, json.dumps(PROGRESS).encode("utf-8"), "application/json")
            return
        if self.path.startswith("/api/blocks"):
            from urllib.parse import unquote
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            frame_name = ""
            for kv in q.split("&"):
                if kv.startswith("frame="):
                    frame_name = unquote(kv[6:])
            out, in_frame = [], set()
            if frame_name:
                fp = os.path.join(FRAMES_DIR, frame_name)
                for nm in wr.frame_block_names(fp):
                    in_frame.add(nm)
                    out.append({"name": nm, "svg": wr.frame_block_svg(fp, nm),
                                "src": "frame"})
            # 块库里的块也列出来：生成时会自动把定义补进外框图
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
                      else "application/dxf")
                self._send(200, open(p, "rb").read(), ct)
            else:
                self._send(404, b"not found")
        else:
            self._send(200, HTML.replace("{{VER}}", code_version()).encode("utf-8"))

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
        ln = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(ln).decode("utf-8"))
        frame_name = req.get("frame", "") or ""
        if not frame_name:
            self._send(200, json.dumps({"svg": "", "log": ["阵列模式必须先选外框图"]}
                                       ).encode("utf-8"), "application/json"); return
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
                    allow_enlarge=bool(req.get("allow_enlarge")),
                    keep_from=(os.path.join(OUTDIR, os.path.basename(req["keep_from"]))
                               if req.get("keep_from") else ""))
        text, log, wires = wr.build_array_frame(os.path.join(FRAMES_DIR, frame_name), spec,
                                                progress=progress_cb)
        if not text:
            PROGRESS["running"] = False
            self._send(200, json.dumps({"svg": "", "log": log}).encode("utf-8"),
                       "application/json"); return
        set_progress(85, "写 DXF 文件")
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
                                     progress=progress_cb)
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
        set_progress(100, "完成")
        PROGRESS["running"] = False
        sw = stale_warning()
        if sw:                       # 代码在本进程启动之后改过：成功也要提醒，不然会对不上
            log = [sw] + list(log)
        self._send(200, json.dumps(resp).encode("utf-8"), "application/json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    class S(socketserver.ThreadingTCPServer):
        # 多线程：生成请求跑着的时候，进度轮询还能进来（单线程会被堵住 → 进度条不动）
        allow_reuse_address = True
        daemon_threads = True

    port = args.port
    httpd = None
    for _ in range(20):
        try:
            httpd = S(("127.0.0.1", port), Handler); break
        except OSError:
            port += 1
    url = "http://127.0.0.1:%d" % port
    print("连线生成器已启动:", url)
    print("代码版本:", code_version(), "(改过代码要重启本进程才生效)")
    print("块库:", BLOCKS_DIR)
    print("输出:", OUTDIR)
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
