#!/usr/bin/env python3
"""
sld_generate.py -- 单线图(Single-Line Diagram)自动生成引擎 (Proof-of-Concept)

零第三方依赖：读取一份"规范化拓扑连接表"(JSON, 见 sld_schema.example.json)，
自动布点、画单线、加标注、生成设备表，输出 R12 DXF（AutoCAD 可直接打开，
另存为 .dwg 即可）。这是从"半自动"到"全自动"的落地引擎雏形。

用法:
    python sld_generate.py sld_schema.example.json -o out.dxf
    python sld_generate.py --help

进阶说明（从半自动到全自动）:
  - 本脚本用"形状"直接画图，便于验证。进入 L1 后，应改用"属性块"输出，
    DXF 的 BLOCKS/INSERT 段可定义块，块带 ATTDEF(属性)，用 DATAEXTRACTION 提取设备表。
  - 真实工程中，把 Excel(工程量) 导出成这个 JSON 即可一键出图；改数据即改图。

作者: Codex, 供 Voltage LLC 评审。
"""

import argparse
import json
import os
import sys


# --------------------------------------------------------------------------
# 图层与样式
# --------------------------------------------------------------------------
LAYERS = [
    ("0", 7, "CONTINUOUS"),
    ("WIRE", 1, "CONTINUOUS"),      # 连线
    ("EQUIP", 3, "CONTINUOUS"),     # 设备图形
    ("TEXT", 7, "CONTINUOUS"),      # 标注
    ("SCHEDULE", 5, "CONTINUOUS"),  # 表格
]
LTYPES = [("CONTINUOUS", "Solid line")]


def fmt(x):
    """数字格式：去掉多余的小数尾零。"""
    s = f"{float(x):.6f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


class Dxf:
    """极简 R12 DXF 写入器（只用到 LINE / CIRCLE / TEXT）。"""

    def __init__(self):
        self.ents = []
        self.minx = self.miny = 1e18
        self.maxx = self.maxy = -1e18

    # ---- 几何跟踪 ----
    def _track(self, x, y):
        self.minx = min(self.minx, x)
        self.maxx = max(self.maxx, x)
        self.miny = min(self.miny, y)
        self.maxy = max(self.maxy, y)

    # ---- 实体生成 ----
    def line(self, x1, y1, x2, y2, layer="WIRE"):
        e = ["0", "LINE", "8", layer]
        e += ["10", fmt(x1), "20", fmt(y1), "30", "0"]
        e += ["11", fmt(x2), "21", fmt(y2), "31", "0"]
        self.ents.append(e)
        self._track(x1, y1)
        self._track(x2, y2)

    def rect(self, x, y, w, h, layer="EQUIP"):
        """左上角 (x,y)，宽 w，高 h（向上）。"""
        x2, y2 = x + w, y - h
        self.line(x, y, x2, y, layer)
        self.line(x2, y, x2, y2, layer)
        self.line(x2, y2, x, y2, layer)
        self.line(x, y2, x, y, layer)

    def circle(self, cx, cy, r, layer="EQUIP"):
        e = ["0", "CIRCLE", "8", layer,
             "10", fmt(cx), "20", fmt(cy), "30", "0",
             "40", fmt(r)]
        self.ents.append(e)
        self._track(cx - r, cy - r)
        self._track(cx + r, cy + r)

    def text(self, x, y, s, h=3.5, layer="TEXT", center=True):
        """标注文字，(x,y) 为锚点；center=True 时水平/垂直居中。"""
        e = ["0", "TEXT", "8", layer,
             "10", fmt(x), "20", fmt(y), "30", "0",
             "40", fmt(h), "1", str(s), "50", "0"]
        if center:
            e += ["72", "1", "73", "2", "11", fmt(x), "21", fmt(y), "31", "0"]
        self.ents.append(e)
        self._track(x - (len(s) * h * 0.5), y - h * 0.5)
        self._track(x + (len(s) * h * 0.5), y + h * 0.5)

    # ---- 组装 DXF ----
    def _table(self, name, rows):
        """rows: 每一项是“一行”的组码/值列表。计数必须是条目数(行数)。"""
        body = ["0", "TABLE", "2", name, "70", str(len(rows))]
        for r in rows:
            body += r
        body += ["0", "ENDTAB"]
        return body

    def _used_layers(self):
        used = set()
        for e in self.ents:
            for i in range(len(e) - 1):
                if e[i] == "8":
                    used.add(e[i + 1])
                    break
        return used

    def build(self, meta):
        # 保险：给边界留点边距
        pad = 8
        minx = fmt(self.minx - pad)
        miny = fmt(self.miny - pad)
        maxx = fmt(self.maxx + pad)
        maxy = fmt(self.maxy + pad)

        out = ["0", "SECTION", "2", "HEADER"]
        out += ["9", "$ACADVER", "1", "AC1009"]
        out += ["9", "$INSBASE", "10", "0", "20", "0", "30", "0"]
        out += ["9", "$EXTMIN", "10", minx, "20", miny, "30", "0"]
        out += ["9", "$EXTMAX", "10", maxx, "20", maxy, "30", "0"]
        out += ["0", "ENDSEC"]

        # 层：内置 + 实体实际用到的(避免引用未定义层)
        layer_defs = list(LAYERS)
        known = {n for (n, _c, _lt) in LAYERS}
        for n in sorted(self._used_layers()):
            if n not in known:
                layer_defs.append((n, 7, "CONTINUOUS"))

        # TABLES：每项是一行(不是散开的字符串)
        ltype_rows = [["0", "LTYPE", "2", name, "70", "0", "3", desc,
                       "72", "65", "73", "0", "40", "0.0"]
                      for name, desc in LTYPES]
        layer_rows = [["0", "LAYER", "2", name, "70", "0", "62", str(color),
                       "6", lt] for name, color, lt in layer_defs]
        style_rows = [["0", "STYLE", "2", "STANDARD", "70", "0", "40", "0.0",
                       "41", "1.0", "50", "0.0", "71", "0", "42", "0.2",
                       "3", "txt", "4", ""]]

        out += ["0", "SECTION", "2", "TABLES"]
        out += self._table("LTYPE", ltype_rows)
        out += self._table("LAYER", layer_rows)
        out += self._table("STYLE", style_rows)
        out += ["0", "ENDSEC"]

        # BLOCKS（R12 必需段）：放标准空间块
        out += ["0", "SECTION", "2", "BLOCKS"]
        for sp in ("*MODEL_SPACE", "*PAPER_SPACE"):
            out += ["0", "BLOCK", "8", "0", "2", sp, "70", "0",
                    "10", "0", "20", "0", "30", "0", "3", sp, "1", "",
                    "0", "ENDBLK", "8", "0"]
        out += ["0", "ENDSEC"]

        # ENTITIES
        out += ["0", "SECTION", "2", "ENTITIES"]
        for e in self.ents:
            out += e
        out += ["0", "ENDSEC", "0", "EOF"]
        return "\n".join(out)


# --------------------------------------------------------------------------
# 布局与渲染
# --------------------------------------------------------------------------
def group_of(node):
    return node.get("group", "G")


def render(schema, dxf):
    meta = schema.get("meta", {})
    nodes = schema["nodes"]
    edges = {e["from"]: e for e in schema["edges"]}
    col_gap = schema.get("grid", {}).get("col_gap", 40)
    row_gap = schema.get("grid", {}).get("row_gap", 22)

    # 分类型
    pv_inverters = [n for n in nodes if n["type"] == "INVERTER"
                    and group_of(n) != "BESS"]
    transformers = [n for n in nodes if n["type"] == "TRANSFORMER"]
    mv_swgr = [n for n in nodes if n["type"] == "MV_SWITCHGEAR"]
    main_trs = [n for n in nodes if n["type"] == "MAIN_TRANSFORMER"]
    pois = [n for n in nodes if n["type"] == "POINT_OF_INTERCONNECTION"]
    bess_units = [n for n in nodes if n["type"] == "BESS_UNIT"]
    bess_pcs = [n for n in nodes if n["type"] == "INVERTER"
                and group_of(n) == "BESS"]

    # --- PV 布点：按分组分列 ---
    groups = sorted({group_of(n) for n in pv_inverters})
    if not groups:
        groups = ["G1"]
    gx = {g: i * col_gap for i, g in enumerate(groups)}

    inv_w, inv_h = 14, 6
    tr_r = 8
    top_y = 10.0
    inv_rows = {}
    for g in groups:
        invs = [n for n in pv_inverters if group_of(n) == g]
        for j, inv in enumerate(invs):
            x = gx[g] - inv_w / 2
            y = top_y - j * row_gap
            dxf.rect(x, y, inv_w, inv_h)
            dxf.text(gx[g], y - inv_h / 2, inv["id"], 2.2)
            if inv.get("ac_kw"):
                dxf.text(gx[g], y - inv_h / 2 - 3.2, f"{inv['ac_kw']}kW", 1.8)
            if inv.get("strings"):
                dxf.text(gx[g], y - inv_h / 2 - 5.4,
                         f"{inv['strings']} str {inv.get('fuse','')}", 1.6)
            inv_rows.setdefault(g, []).append((inv, y))

    # LV 汇流母线 + 变压器
    lv_y = top_y - (max(len(inv_rows[g]) for g in groups) * row_gap) - 4
    minx = min(gx.values()) - 14
    maxx = max(gx.values()) + 14
    tr_y = lv_y - tr_r - 6
    # 每台逆变器向下引线至 LV 母线
    for g in groups:
        dxf.line(gx[g], top_y + 4, gx[g], lv_y)  # 组内竖向母线
        for inv, y in inv_rows[g]:
            dxf.line(gx[g], y - inv_h, gx[g], lv_y)
    dxf.line(minx, lv_y, maxx, lv_y)  # LV 母线

    for g in groups:
        tr = next((n for n in transformers if group_of(n) == g), None)
        if tr is None:
            continue
        dxf.line(gx[g], lv_y, gx[g], tr_y + tr_r)
        dxf.circle(gx[g], tr_y, tr_r)
        dxf.text(gx[g], tr_y, tr["id"], 2.4)
        dxf.text(gx[g], tr_y - tr_r - 3,
                 f"{tr.get('kva',0)}kVA  {tr.get('hv','')}", 1.8)

    # MV 母线 + 开关柜 + 主变 + 并网点
    mv_y = tr_y - tr_r - 8
    center = (min(gx.values()) + max(gx.values())) / 2
    dxf.line(minx, mv_y, maxx, mv_y)  # MV 母线
    for g in groups:
        if any(group_of(n) == g for n in transformers):
            dxf.line(gx[g], tr_y - tr_r, gx[g], mv_y)

    if mv_swgr:
        sw_gap = 10
        for i, sw in enumerate(mv_swgr):
            swx = center + i * 30 - 20
            dxf.rect(swx, mv_y, 40, 12)
            dxf.text(swx + 20, mv_y - 6, "MV SWITCHGEAR", 2.2)
            dxf.text(swx + 20, mv_y - 9, sw.get("hv", ""), 1.8)

    if main_trs:
        mt = main_trs[0]
        mt_y = mv_y - 22
        dxf.line(center, mv_y, center, mt_y + tr_r)
        dxf.circle(center, mt_y, tr_r)
        dxf.text(center, mt_y, "MAIN TR", 2.4)
        dxf.text(center, mt_y - tr_r - 3,
                 f"{mt.get('kva',0)}kVA  {mt.get('hv','')}/{mt.get('lv','')}", 1.8)

    if pois:
        p = pois[0]
        poi_y = mt_y - tr_r - 14
        dxf.line(center, mt_y - tr_r, center, poi_y + 6)
        dxf.rect(center - 12, poi_y, 24, 8)
        dxf.text(center, poi_y - 4, "POI", 2.4)
        dxf.text(center, poi_y - 7, p.get("hv", ""), 1.8)

    # --- BESS 单元（右侧独立一带，接入 MV）---
    bess_x = maxx + col_gap + 6
    by = top_y + 10
    for i, bu in enumerate(bess_units):
        bx = bess_x + (i % 2) * 30
        if i > 0 and i % 2 == 0:
            by -= 40
        dxf.rect(bx, by, 26, 10)
        dxf.text(bx + 13, by - 5, bu["id"], 2.2)
        dxf.text(bx + 13, by - 8, f"{bu.get('mw',0)}MW/{bu.get('mwh',0)}MWh", 1.8)
    if bess_pcs:
        pcs_y = by - 20
        for i, pc in enumerate(bess_pcs):
            bx = bess_x + (i % 2) * 30
            dxf.rect(bx, pcs_y, 26, 8)
            dxf.text(bx + 13, pcs_y - 4, pc["id"], 2.2)
        # BESS 汇入 MV 母线
        dxf.line(bess_x, pcs_y, bess_x, mv_y)
        dxf.line(bess_x, mv_y, maxx, mv_y)

    # --- 设备表 (Schedule) ---
    sch_x = min_gx = (gx[groups[0]] if groups else 0)
    if pois:
        sch_y = poi_y
    elif main_trs:
        sch_y = mt_y - 34
    else:
        sch_y = mv_y - 40
    sched_y = sch_y - 14
    dxf.text(sch_x, sched_y, "EQUIPMENT SCHEDULE", 3.0, center=False)
    headers = ("ID", "TYPE", "GROUP", "RATING")
    dxf.text(sch_x, sched_y - 6, "  ".join(headers), 2.2, center=False)
    ry = sched_y - 11
    for n in nodes:
        if n["type"] in ("MV_SWITCHGEAR", "POINT_OF_INTERCONNECTION",
                         "MAIN_TRANSFORMER"):
            continue
        rating = ""
        if n["type"] == "INVERTER":
            rating = f"{n.get('ac_kw','')}kW"
            if group_of(n) != "BESS" and n.get("strings"):
                rating += f" {n['strings']}str"
        elif n["type"] == "TRANSFORMER":
            rating = f"{n.get('kva','')}kVA"
        elif n["type"] == "BESS_UNIT":
            rating = f"{n.get('mw','')}MW/{n.get('mwh','')}MWh"
        dxf.text(sch_x, ry, f"{n['id']}  {n['type']}  {group_of(n)}  {rating}",
                 2.0, center=False)
        ry -= 4.2

    # --- 图签 (Title Block) ---
    tb_x = sch_x
    tb_y = ry - 6
    dxf.rect(tb_x, tb_y, 120, 18)
    dxf.text(tb_x + 60, tb_y - 5, meta.get("title", "SINGLE LINE DIAGRAM"), 3.0)
    dxf.text(tb_x + 60, tb_y - 10,
             f"{meta.get('project','')}  Rev {meta.get('revision','')}  "
             f"{meta.get('date','')}  Sheet {meta.get('sheet_no','')}", 1.8)


def validate(schema):
    """基础回归校验：编号唯一、边两端存在、无自环。"""
    ids = [n["id"] for n in schema["nodes"]]
    issues = []
    if len(ids) != len(set(ids)):
        issues.append("存在重复的节点 id")
    id_set = set(ids)
    for e in schema["edges"]:
        if e["from"] not in id_set:
            issues.append(f"边起点缺失: {e['from']}")
        if e["to"] not in id_set:
            issues.append(f"边终点缺失: {e['to']}")
        if e["from"] == e["to"]:
            issues.append(f"存在自环: {e['from']}")
    return issues


def main(argv=None):
    ap = argparse.ArgumentParser(description="把拓扑连接表(JSON)渲染成单线图 DXF")
    ap.add_argument("schema", help="拓扑连接表 JSON 路径")
    ap.add_argument("-o", "--out", default="out.dxf", help="输出 DXF 路径")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="整体缩放（可选，默认 1.0）")
    args = ap.parse_args(argv)

    with open(args.schema, "r", encoding="utf-8") as f:
        schema = json.load(f)

    issues = validate(schema)
    if issues:
        print("校验发现：")
        for i in issues:
            print("  - " + i)

    dxf = Dxf()
    render(schema, dxf)
    content = dxf.build(schema.get("meta", {}))

    with open(args.out, "w", encoding="utf-8", newline="") as f:
        f.write(content)

    n_nodes = len(schema["nodes"])
    n_edges = len(schema["edges"])
    print(f"已生成: {args.out}")
    print(f"  节点: {n_nodes}, 边: {n_edges}, 实体: {len(dxf.ents)}")
    print(f"  范围: X[{dxf.minx:.1f},{dxf.maxx:.1f}] Y[{dxf.miny:.1f},{dxf.maxy:.1f}]")
    print("用 AutoCAD 打开该 DXF 即可查看；如需 DWG，另存为即可。")


if __name__ == "__main__":
    main()
