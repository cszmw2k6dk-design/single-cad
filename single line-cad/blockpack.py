#!/usr/bin/env python3
"""
blockpack.py -- 把块库(blocklib/blocks/*.dxf)里的块定义，安全合并进外框图 DXF。

为什么单独写一个模块：
  以前试过“把整个 DXF 解析后重新拼一遍”，AutoCAD 打开就报
  “无效或不完整的 DXF 输入 -- 图形被放弃”。所以这里只做一件事：
  **在外框图的原始字节上做定点插入**，其它字节一个都不动。

合并规则：
  1. 新块名 = 块库文件名（POS.dxf -> 块名 POS）；
     块内容 = 该文件 model space(ENTITIES 段) 的全部实体，原样搬运、不展平。
     => “库里长什么样，插进去就长什么样”，SPLINE/HATCH/ELLIPSE 都不丢。
  2. 块库文件自己 BLOCKS 段里的块，改名成 `<新块名>$<原名>` 一起带进来，
     并把 INSERT 的引用同步改掉 —— 永远不会和外框图里的同名块打架。
  3. 句柄(5) 全部重新分配（从外框图最大句柄+1 开始），owner(330) 指向新的
     BLOCK_RECORD，最后同步 $HANDSEED。
  4. 丢掉 102 {ACAD_XDICTIONARY / ACAD_REACTORS ...} 组：跨文件后会悬空。
  5. 图层按需补进 TABLES，并修正表头 70 的数量。

对外主函数：
  pack_into(frame_bytes, block_paths) -> (new_bytes, info)
  verify(new_bytes, expect_names)     -> [问题字符串]（空 = 通过）
"""

import collections
import os
import re

CRLF = b"\r\n"
SKIP_DEFS = (b"*MODEL_SPACE", b"*PAPER_SPACE", b"*PAPER_SPACE0")


# ======================================================== 字节级 DXF 切分
def iter_groups(data):
    """把 DXF 字节流切成组: (start, value_start, value_end, end, code, value)。

    DXF 一组 = 代码行 + 值行。全程字节操作 → 中文(ANSI_936/UTF-8 原始字节)
    一模一样地搬运，不会因转码损坏。
    """
    i, n = 0, len(data)
    while i < n:
        j = data.find(b"\n", i)
        if j < 0:
            return
        k = data.find(b"\n", j + 1)
        if k < 0:
            return
        v_end = k - 1 if data[k - 1:k] == b"\r" else k
        yield (i, j + 1, v_end, k + 1, data[i:j].strip(), data[j + 1:v_end])
        i = k + 1


def split_records(gl, lo, hi):
    """把 gl[lo:hi] 按 0 组切成记录；每条记录 = 组的列表。"""
    recs, cur = [], None
    for g in gl[lo:hi]:
        if g[4] == b"0":
            if cur:
                recs.append(cur)
            cur = [g]
        elif cur is not None:
            cur.append(g)
    if cur:
        recs.append(cur)
    return recs


def section_range(gl, name):
    """返回段正文的 (起, 止) 索引（不含 SECTION/ENDSEC 本身）。"""
    for i in range(len(gl) - 1):
        if gl[i][4] == b"0" and gl[i][5] == b"SECTION" and gl[i + 1][5] == name:
            j = i + 2
            while j < len(gl) and not (gl[j][4] == b"0" and gl[j][5] == b"ENDSEC"):
                j += 1
            return i + 2, j
    return None


def table_range(gl, name):
    """返回 (header_first_idx, first_rec_idx, endtab_idx, 表句柄, 70计数组)。"""
    for i in range(len(gl) - 1):
        if (gl[i][4] == b"0" and gl[i][5] == b"TABLE"
                and gl[i + 1][4] == b"2" and gl[i + 1][5] == name):
            j = i + 2
            while j < len(gl) and not (gl[j][4] == b"0" and gl[j][5] == b"ENDTAB"):
                j += 1
            k = i + 2
            while k < j and gl[k][4] != b"0":
                k += 1
            thand, count_g = None, None
            for g in gl[i + 2:k]:
                if g[4] == b"5" and thand is None:
                    thand = g[5]
                if g[4] == b"70" and count_g is None:
                    count_g = g
            return (i + 2, k, j, thand, count_g)
    return None


def rec_get(rec, code, default=None):
    for g in rec:
        if g[4] == code:
            return g[5]
    return default


def emit(code, value):
    return code + CRLF + value + CRLF


def apply_edits(data, edits):
    """edits=[(start,end,new_bytes)]，按位置倒序套用（互不重叠）。"""
    out = data
    for start, end, new in sorted(edits, key=lambda e: e[0], reverse=True):
        out = out[:start] + new + out[end:]
    return out


def _handle_values(gl):
    """所有“真句柄”(5 组)，排除 HEADER 里 $HANDSEED 那个 5 组。"""
    out = []
    for i, g in enumerate(gl):
        if g[4] != b"5":
            continue
        if i > 0 and gl[i - 1][4] == b"9" and gl[i - 1][5] == b"$HANDSEED":
            continue
        out.append(g[5])
    return out


def _max_handle(gl):
    mx = 0
    for v in _handle_values(gl):
        try:
            mx = max(mx, int(v, 16))
        except ValueError:
            pass
    return mx


# ======================================================== 记录复制
def copy_record(rec, new_handle, handle_map, owner, name_map):
    """原样复制一条记录，只改：句柄 5、owner 330、块名引用 2、XDATA 句柄 1005。"""
    out = bytearray()
    has5, depth = False, 0
    for g in rec:
        code, val = g[4], g[5]
        if code == b"102" and val.startswith(b"{"):
            depth += 1
            continue
        if code == b"102" and val == b"}" and depth:
            depth -= 1
            continue
        if depth:
            continue                      # 102 {...} 组整块丢掉
        if code == b"5" and not has5:
            has5 = True
            out += emit(b"5", new_handle)
            continue
        if code == b"330":
            out += emit(b"330", handle_map.get(val, owner))
            continue
        if code == b"2" and name_map and val in name_map:
            out += emit(b"2", name_map[val])
            continue
        if code == b"1005" and val in handle_map:
            out += emit(b"1005", handle_map[val])
            continue
        out += emit(code, val)
    if not has5:                          # 极少数记录没有 5，补一个
        head = emit(b"0", rec[0][5])
        if bytes(out).startswith(head):
            out = bytearray(head) + emit(b"5", new_handle) + out[len(head):]
        else:
            out = bytearray(emit(b"5", new_handle)) + out
    return bytes(out)


def make_block_header(name, handle, owner):
    return (emit(b"0", b"BLOCK") + emit(b"5", handle) + emit(b"330", owner) +
            emit(b"100", b"AcDbEntity") + emit(b"8", b"0") +
            emit(b"100", b"AcDbBlockBegin") + emit(b"2", name) +
            emit(b"70", b"0") + emit(b"10", b"0.0") + emit(b"20", b"0.0") +
            emit(b"30", b"0.0") + emit(b"3", name) + emit(b"1", b""))


def make_block_end(handle, owner):
    return (emit(b"0", b"ENDBLK") + emit(b"5", handle) + emit(b"330", owner) +
            emit(b"100", b"AcDbEntity") + emit(b"8", b"0") +
            emit(b"100", b"AcDbBlockEnd"))


def make_block_record(name, handle, table_handle):
    return (emit(b"0", b"BLOCK_RECORD") + emit(b"5", handle) +
            emit(b"330", table_handle) +
            emit(b"100", b"AcDbSymbolTableRecord") +
            emit(b"100", b"AcDbBlockTableRecord") +
            emit(b"2", name) + emit(b"70", b"0"))


# ======================================================== 读块库文件
def read_block_file(path):
    """读一个块库 .dxf，返回 dict(ents, defs, layers, names)。"""
    data = open(path, "rb").read()
    gl = list(iter_groups(data))
    ents, defs, layers = [], [], []
    for i in range(len(gl) - 1):
        if not (gl[i][4] == b"0" and gl[i][5] == b"SECTION"):
            continue
        nm = gl[i + 1][5]
        rng = section_range(gl, nm)
        if rng is None:
            continue
        lo, hi = rng
        if nm == b"ENTITIES":
            ents = split_records(gl, lo, hi)
        elif nm == b"BLOCKS":
            recs = split_records(gl, lo, hi)
            k = 0
            while k < len(recs):
                if recs[k][0][5] == b"BLOCK":
                    bname = rec_get(recs[k], b"2")
                    mem, t = [], k + 1
                    while t < len(recs) and recs[t][0][5] != b"ENDBLK":
                        mem.append(recs[t]); t += 1
                    tail = recs[t] if t < len(recs) else None
                    if bname:
                        defs.append((bname, [recs[k]] + mem + ([tail] if tail else [])))
                    k = t + 1
                else:
                    k += 1
        elif nm == b"TABLES":
            for r in split_records(gl, lo, hi):
                if r[0][5] == b"LAYER":
                    layers.append(r)
    return {"path": path, "ents": ents, "defs": defs, "layers": layers}


# ======================================================== 主流程
def pack_into(frame_bytes, block_paths, log=None):
    """把 block_paths 里的块合并进 frame_bytes。返回 (新字节, info)。"""
    log = log if log is not None else []
    data = frame_bytes
    gl = list(iter_groups(data))

    brt = table_range(gl, b"BLOCK_RECORD")
    if brt is None:
        raise ValueError("外框图里没有 BLOCK_RECORD 表")
    lay = table_range(gl, b"LAYER")
    blocks_rng = section_range(gl, b"BLOCKS")
    if blocks_rng is None:
        raise ValueError("外框图里没有 BLOCKS 段")

    def table_names(name):
        tr = table_range(gl, name)
        if tr is None:
            return set()
        return {rec_get(r, b"2") for r in split_records(gl, tr[1], tr[2])
                if rec_get(r, b"2")}

    have_br = table_names(b"BLOCK_RECORD")
    have_la = table_names(b"LAYER")
    have_lt = table_names(b"LTYPE")

    counter = [_max_handle(gl) + 1]

    def nh():
        v = b"%X" % counter[0]
        counter[0] += 1
        return v

    br_out, la_out, blk_out = bytearray(), bytearray(), bytearray()
    n_br = n_la = 0
    added, skipped, warnings = [], [], []

    for path in block_paths:
        stem_b = os.path.splitext(os.path.basename(path))[0].encode("utf-8")
        if stem_b in have_br:
            skipped.append(stem_b.decode("utf-8", "replace"))
            continue
        try:
            src = read_block_file(path)
        except Exception as ex:
            warnings.append("跳过 %s（读取失败: %s）" % (path, ex))
            continue
        if not src["ents"]:
            warnings.append("跳过 %s（model space 没有实体）" % path)
            continue

        # 1) 子块改名：<块名>$<原子块名>（匿名块 *D14 -> <块名>$D14）
        name_map, sub_defs = {}, []
        for nm, recs in src["defs"]:
            if nm.upper() in SKIP_DEFS:
                continue
            bare = nm[1:] if nm.startswith(b"*") else nm
            new_nm = stem_b + b"$" + bare
            name_map[nm] = new_nm
            sub_defs.append((new_nm, recs))

        # 2) 统一分配句柄（先全部分配，再统一发射，保证前向引用也能对上）
        all_recs = [r for _nm, recs in sub_defs for r in recs] + list(src["ents"])
        handle_map, new_handles = {}, []
        for rec in all_recs:
            h = rec_get(rec, b"5")
            hh = nh()
            new_handles.append(hh)
            if h and h not in handle_map:
                handle_map[h] = hh
        it = iter(new_handles)

        # 3) 主块自己的 BLOCK_RECORD
        top_br = nh()
        br_out += make_block_record(stem_b, top_br, brt[3])
        have_br.add(stem_b); n_br += 1

        # 4) 子块定义（保留原基点，只换名/句柄/owner）
        for new_nm, recs in sub_defs:
            sub_br = nh()
            br_out += make_block_record(new_nm, sub_br, brt[3])
            have_br.add(new_nm); n_br += 1
            blk_out += copy_record(recs[0], next(it), handle_map, sub_br, name_map)
            for r in recs[1:-1]:
                blk_out += copy_record(r, next(it), handle_map, sub_br, name_map)
            if len(recs) > 1:
                blk_out += copy_record(recs[-1], next(it), handle_map, sub_br, name_map)
            else:
                blk_out += make_block_end(nh(), sub_br)

        # 5) 主块定义：model space 原样 = 块内容（基点 0,0）
        blk_out += make_block_header(stem_b, nh(), top_br)
        for r in src["ents"]:
            blk_out += copy_record(r, next(it), handle_map, top_br, name_map)
        blk_out += make_block_end(nh(), top_br)

        # 6) 图层按需补（缺线型就退回 Continuous）
        for rec in src["layers"]:
            nm = rec_get(rec, b"2")
            if not nm or nm in have_la:
                continue
            rec2 = list(rec)
            lt = rec_get(rec, b"6")
            if lt and lt not in have_lt:
                rec2 = [g for g in rec2 if g[4] != b"6"]
                rec2.append((0, 0, 0, 0, b"6", b"Continuous"))
                have_lt.add(b"Continuous")
            if lay is not None:
                la_out += copy_record(rec2, nh(), handle_map, lay[3], {})
            have_la.add(nm); n_la += 1

        log.append("合并块 %s（%d 个子块, %d 个实体）"
                   % (stem_b.decode("utf-8", "replace"), len(sub_defs), len(src["ents"])))
        added.append(stem_b.decode("utf-8", "replace"))

    if skipped:
        log.append("外框图里已有，跳过: " + ", ".join(skipped))
    for w in warnings:
        log.append("⚠ " + w)

    if not added:
        return data, {"added": [], "skipped": skipped, "next_handle": counter[0],
                      "log": log}

    edits = [(gl[brt[2]][0], gl[brt[2]][0], bytes(br_out))]
    if la_out and lay is not None:
        edits.append((gl[lay[2]][0], gl[lay[2]][0], bytes(la_out)))
    edits.append((gl[blocks_rng[1]][0], gl[blocks_rng[1]][0], bytes(blk_out)))

    def set_count(tbl, kind, extra, count_g):
        """把表头 70 写成“插入后的真实条目数”。

        （外框图自己的 70 就和实际条数不符：BLOCK_RECORD 写 28 实际 30。
         写真实值最稳：不管 AutoCAD 是数到 ENDTAB 还是信 70，都能读到全部。）
        """
        if count_g is None:
            return None
        n = sum(1 for r in split_records(gl, tbl[1], tbl[2])
                if r[0][5] == kind) + extra
        return (count_g[1], count_g[2], b"%d" % n)

    e = set_count(brt, b"BLOCK_RECORD", n_br, brt[4])
    if e:
        edits.append(e)
    if n_la and lay is not None:
        e = set_count(lay, b"LAYER", n_la, lay[4])
        if e:
            edits.append(e)

    out = apply_edits(data, edits)
    seed = b"%X" % (counter[0] + 1)
    out = re.sub(rb"(\$HANDSEED\r?\n[ \t]*5\r?\n[ \t]*)([0-9A-Fa-f]+)",
                 lambda m: m.group(1) + seed, out, count=1)
    return out, {"added": added, "skipped": skipped, "next_handle": counter[0],
                 "log": log}


# ======================================================== 体检
def verify(data, expect_names=()):
    """把生成的字节再解析一遍做结构检查；返回问题列表（空 = 通过）。"""
    problems = []
    gl = list(iter_groups(data))
    if not data.endswith(b"\n"):
        problems.append("结尾没有换行")
    if not data.rstrip().endswith(b"EOF"):
        problems.append("结尾没有 EOF")
    if sum(1 for g in gl if g[4] == b"0" and g[5] == b"SECTION") != \
       sum(1 for g in gl if g[4] == b"0" and g[5] == b"ENDSEC"):
        problems.append("SECTION / ENDSEC 数量不匹配")
    if sum(1 for g in gl if g[4] == b"0" and g[5] == b"BLOCK") != \
       sum(1 for g in gl if g[4] == b"0" and g[5] == b"ENDBLK"):
        problems.append("BLOCK / ENDBLK 数量不匹配")

    handles = collections.Counter(_handle_values(gl))
    dup = [h for h, c in handles.items() if c > 1]
    if dup:
        problems.append("句柄重复 %d 个（例: %s）"
                        % (len(dup), [d.decode() for d in dup[:3]]))
    mx = 0
    for h in handles:
        try:
            mx = max(mx, int(h, 16))
        except ValueError:
            pass
    for i in range(len(gl) - 1):
        if gl[i][4] == b"9" and gl[i][5] == b"$HANDSEED":
            try:
                if int(gl[i + 1][5], 16) <= mx:
                    problems.append("$HANDSEED(%s) 不大于最大句柄(%X)"
                                    % (gl[i + 1][5].decode(), mx))
            except ValueError:
                problems.append("$HANDSEED 不是十六进制")

    br, blk = set(), set()
    tr = table_range(gl, b"BLOCK_RECORD")
    if tr is None:
        problems.append("没有 BLOCK_RECORD 表")
    else:
        br = {rec_get(r, b"2") for r in split_records(gl, tr[1], tr[2])
              if rec_get(r, b"2")}
    rng = section_range(gl, b"BLOCKS")
    if rng is None:
        problems.append("没有 BLOCKS 段")
    else:
        blk = {rec_get(r, b"2") for r in split_records(gl, *rng)
               if r[0][5] == b"BLOCK" and rec_get(r, b"2")}
    for nm in blk - br:
        problems.append("块 %r 有定义但没有 BLOCK_RECORD" % nm)
    for nm in br - blk:
        problems.append("块 %r 有 BLOCK_RECORD 但没有定义" % nm)
    for n in expect_names:
        nb = n.encode("utf-8")
        if nb not in br:
            problems.append("缺少块记录: " + n)
        if nb not in blk:
            problems.append("缺少块定义: " + n)

    # 主段齐全
    secs = {gl[i + 1][5] for i in range(len(gl) - 1)
            if gl[i][4] == b"0" and gl[i][5] == b"SECTION"}
    for need in (b"HEADER", b"TABLES", b"BLOCKS", b"ENTITIES"):
        if need not in secs:
            problems.append("缺少段: " + need.decode())
    return problems
