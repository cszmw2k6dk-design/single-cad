#!/usr/bin/env python3
"""
extract_blocks.py -- 从“主库 DXF”里把每个用户块抽取成独立块文件

对每个用户块 B：生成一个独立 .dxf（放进块库）：
  HEADER + CLASSES + TABLES(原样) + BLOCKS(仅 B 及其递归引用的子块) +
  ENTITIES(一个 INSERT B) + EOF

这样每个文件都能被现有连线程序当普通块读取（模型空间=该块）。

用法:
  python extract_blocks.py "<主库.dxf>" [--out blocklib/blocks] [--limit N] [--dry]
"""

import argparse
import os
import re
import sys


def scan(path):
    """流式扫描，返回 (sections, blocks, mspace_handle, max_handle)。"""
    sections = {}
    blocks = []            # (name, start, end)
    mspace = None
    maxh = 0
    offset = 0
    code = None
    code_pos = 0
    sec = None
    sec_start = 0
    pending_start = 0
    pending = False
    cur = None
    # BLOCK_RECORD 名字上下文（找 *MODEL_SPACE 的 handle）
    brec_name = None
    brec_handle = None
    with open(path, "rb") as fh:
        for raw in fh:
            off = offset
            offset += len(raw)
            v = raw.decode("utf-8", "replace").strip()
            if code is None:
                code = v; code_pos = off
                continue
            c, val = code, v
            code = None
            if c == "5":
                try:
                    maxh = max(maxh, int(val, 16))
                except (TypeError, ValueError):
                    pass
                if brec_handle is None:
                    brec_handle = val
            if c == "0" and val == "SECTION":
                pending = True; pending_start = code_pos
            elif c == "2" and pending:
                sec = val; sec_start = pending_start; pending = False
            elif c == "0" and val == "ENDSEC":
                if sec:
                    sections[sec] = (sec_start, offset)
                sec = None
            elif sec == "BLOCKS" and c == "0" and val == "BLOCK":
                cur = [None, code_pos, None]
            elif sec == "BLOCKS" and c == "2" and cur is not None and cur[0] is None:
                cur[0] = val
            elif sec == "BLOCKS" and c == "0" and val == "ENDBLK":
                if cur is not None:
                    cur[2] = offset
                    blocks.append(tuple(cur))
                    cur = None
            elif sec == "TABLES" and c == "0" and val == "BLOCK_RECORD":
                brec_name = None; brec_handle = None
            elif sec == "TABLES" and c == "2" and val == "*Model_Space" and brec_handle:
                mspace = brec_handle
            elif sec == "TABLES" and c == "0" and val == "TABLE":
                pass
    return sections, blocks, mspace, maxh


def insert_names(blob):
    """从字节块里找 INSERT 引用的块名(代码2)。"""
    names = set()
    # 必须按“单行”切：DXF 里空值行就是连续换行，用 [\r\n]+ 会把两行并成一行，
    # 后面“代码行/值行”的配对整体错位，INSERT 就找不到了（子块会被漏掉）。
    toks = blob.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
    i = 0
    cur_ins = False
    while i + 1 < len(toks):
        c = toks[i].strip().decode("utf-8", "replace")
        v = toks[i + 1].strip()
        i += 2
        if c == "0":
            cur_ins = (v == b"INSERT")
        elif cur_ins and c == "2":
            names.add(v.decode("utf-8", "replace"))
            cur_ins = False
    return names


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("master")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "blocklib", "blocks"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--only", default="", help="只抽这些块（逗号分隔，例如 --only 负极支线）")
    args = ap.parse_args()

    sections, blocks, mspace, maxh = scan(args.master)
    print("sections:", {k: "%.2fMB" % ((v[1]-v[0])/1048576) for k, v in sections.items()})
    print("BLOCKs:", len(blocks), "| *Model_Space handle:", mspace, "| max handle:", "%X" % maxh)

    data = open(args.master, "rb").read()

    def sec_bytes(n):
        s, e = sections[n]
        return data[s:e]

    header0 = sec_bytes("HEADER")

    def header_with_handseed(seed_val):
        """把 HEADER 里的 $HANDSEED 改成 seed_val(十六进制)。"""
        m = re.search(rb"\$HANDSEED\r?\n[ \t]*5\r?\n[ \t]*([0-9A-Fa-f]+)",
                      header0)
        if not m:
            return header0
        return header0[:m.start(1)] + ("%X" % seed_val).encode() + header0[m.end(1):]

    blk_bytes = {b[0]: data[b[1]:b[2]] for b in blocks if b[0]}
    user = [b[0] for b in blocks if b[0] and not b[0].startswith("*") and not b[0].startswith("A$")]
    if args.only:
        want = [x.strip() for x in args.only.replace("，", ",").split(",") if x.strip()]
        have = set(user)
        for w in want:
            if w not in have:
                print("⚠ 主库里没有块: %s" % w)
        user = [n for n in user if n in want]

    os.makedirs(args.out, exist_ok=True)
    handle = maxh + 1
    done = 0
    for name in user:
        # 递归收集依赖子块
        needed = []
        stack = [name]
        seen = set()
        for sp in ("*Model_Space", "*Paper_Space"):
            if sp in blk_bytes:
                seen.add(sp)
        while stack:
            nm = stack.pop()
            if nm in seen or nm not in blk_bytes:
                continue
            seen.add(nm); needed.append(nm)
            for dep in insert_names(blk_bytes[nm]):
                if dep not in seen:
                    stack.append(dep)
        needed = ["*Model_Space", "*Paper_Space"] + [n for n in needed if n not in ("*Model_Space", "*Paper_Space")]
        if args.dry:
            print("  %-30s 依赖块=%d" % (name, len(needed)))
            continue
        h = "%X" % handle; handle += 1
        seed = int(h, 16) + 1        # $HANDSEED 必须大于所有句柄
        out = bytearray()
        out += header_with_handseed(seed)
        if "CLASSES" in sections:
            out += sec_bytes("CLASSES")
        out += sec_bytes("TABLES")
        out += b"0\nSECTION\n2\nBLOCKS\n"
        for nm in needed:
            out += blk_bytes[nm]
        out += b"0\nENDSEC\n"
        out += b"0\nSECTION\n2\nENTITIES\n"
        out += ("0\nINSERT\n5\n%s\n330\n%s\n100\nAcDbEntity\n8\n0\n"
                "100\nAcDbBlockReference\n2\n%s\n10\n0.0\n20\n0.0\n30\n0.0\n"
                "41\n1.0\n42\n1.0\n43\n1.0\n50\n0.0\n"
                % (h, mspace or "0", name)).encode("utf-8")
        out += b"0\nENDSEC\n0\nEOF\n"
        fn = os.path.join(args.out, safe_name(name) + ".dxf")
        if os.path.exists(fn):   # 避免覆盖已有(你已标点的)同名块
            fn = os.path.join(args.out, safe_name(name) + "_tpl.dxf")
        with open(fn, "wb") as f:
            f.write(out)
        done += 1
        print("  写出 %-24s -> %-28s %.0f KB" % (name, os.path.basename(fn), len(out)/1024))
    if not args.dry:
        print("完成，共 %d 个块 -> %s" % (done, args.out))


if __name__ == "__main__":
    main()
