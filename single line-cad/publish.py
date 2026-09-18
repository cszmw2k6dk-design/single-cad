#!/usr/bin/env python3
"""publish.py -- 一键发版：把当前改动提交并推到 GitHub。

做的事（按顺序）：
  1. 确认有改动（没改动就直接说，不硬造提交）
  2. 自动**升版本号**（写到 version.json）—— 版本号不变的话客户端不会认为有新版本
  3. git add -A + commit
  4. 推送：先试 `git push`；连不上 github.com（企业防火墙常见）就自动改走 GitHub API
  5. 回读远端，确认版本号真的上去了

用法：
    python publish.py                 # 提交说明取最近一次有意义的改动概要
    python publish.py -m "改了负极行对齐"
    python publish.py --dry-run       # 只显示要做什么，不真提交/推送

配套：双击 publish.cmd 即可（它负责找到 Python 再调这个脚本）。
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # git 仓库根（上一级）
VERSION_PATH = os.path.join(HERE, "version.json")
GH_OWNER, GH_REPO, GH_BRANCH = "cszmw2k6dk-design", "single-line-cad", "main"


def git(*args, check=False):
    out = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    if check and out.returncode != 0:
        raise RuntimeError("git %s 失败: %s" % (" ".join(args), (out.stderr or "").strip()))
    return (out.stdout or "").strip()


def read_version():
    try:
        with open(VERSION_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


VER_PREFIX = "v"          # 版本号统一 v 开头，从 v1.0 起算：v1.0 → v1.1 → v1.2 …


def bump_version(old):
    """给出下一个版本号：**最后一段数字 +1**（v1.0 → v1.1 → v1.2 …）。

    想发补丁号就自己手写 v1.0.1，下次自动变 v1.0.2。
    老式日期号（v20260916.1506，第一段是 8 位日期）一律归到新号起点 v1.0 ——
    已经装了老版本的客户端也能收到这次更新（app_update._is_newer 有同一条过渡规则）。
    """
    nums = []
    for part in re.split(r"[._\-+]+", str(old or "").strip().lstrip("vV")):
        if part.isdigit():
            nums.append(int(part))
        elif part:
            break                       # 遇到 beta 之类的后缀就停，只认前面的数字段
    if not nums or nums[0] >= 1000:     # 日期式老号 / 没号 -> 新号起点
        return VER_PREFIX + "1.0"
    nums[-1] += 1
    return VER_PREFIX + ".".join(str(n) for n in nums)


def recent_summary():
    """默认提交说明：按本次改动自动拼一句（比重复上一条说明有用）。"""
    lines = [l for l in git("status", "--short").splitlines() if l.strip()]
    if not lines:
        return "例行发版"
    add, mod, dele, names = [], [], [], []
    for l in lines:
        code = l[:2].strip()
        name = l[3:].strip().strip('"').replace("/", "\\")
        short = os.path.basename(name)
        if short not in names:
            names.append(short)
        if code == "??" or code.startswith("A"):
            add.append(short)
        elif code.startswith("D"):
            dele.append(short)
        else:
            mod.append(short)
    bits = []
    if mod:
        bits.append("改 %s" % ("、".join(mod[:2]) + ("等 %d 处" % len(mod) if len(mod) > 2 else "")))
    if add:
        bits.append("加 %s" % ("、".join(add[:2]) + ("等 %d 个" % len(add) if len(add) > 2 else "")))
    if dele:
        bits.append("删 %s" % "、".join(dele[:2]))
    head = "；".join(bits) if bits else "更新 %d 个文件" % len(names)
    return head[:70]


def main(argv=None):
    ap = argparse.ArgumentParser(description="一键发版到 GitHub")
    ap.add_argument("-m", "--message", default="", help="提交说明")
    ap.add_argument("--dry-run", action="store_true", help="只演示，不提交不推送")
    ap.add_argument("--force", action="store_true", help="即使没有改动也发一版")
    args = ap.parse_args(argv)

    changed = git("status", "--short")
    if not changed and not args.force:
        print("没有需要提交的改动（工作区是干净的）。")
        print("改了东西再来，或者加 --force 硬发一版。")
        return 0

    old = read_version()
    new_ver = bump_version(old.get("version", "0"))
    msg = args.message or recent_summary()

    print("=" * 56)
    print("提交说明 : %s" % msg)
    print("版本号   : %s  ->  %s" % (old.get("version", "（无）"), new_ver))
    print("改动文件 : %d 个" % len([l for l in changed.splitlines() if l.strip()]))
    for l in changed.splitlines()[:15]:
        print("    " + l)
    if len(changed.splitlines()) > 15:
        print("    ... 还有 %d 个" % (len(changed.splitlines()) - 15))
    print("=" * 56)

    if args.dry_run:
        print("（--dry-run：到此为止，没有提交也没有推送）")
        return 0

    # ---- 写版本号 ----
    data = {
        "version": new_ver,
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "notes": msg,
        "commit": git("rev-parse", "--short", "HEAD"),
        "url": "https://codeload.github.com/%s/%s/zip/refs/heads/%s" % (GH_OWNER, GH_REPO, GH_BRANCH),
    }
    with open(VERSION_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("[1/4] 已写入版本号", new_ver)

    # ---- 提交 ----
    git("add", "-A")
    commit = subprocess.run(["git", "commit", "-m", "%s（%s）" % (msg, new_ver)],
                            cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if commit.returncode != 0:
        print("提交失败：", (commit.stderr or commit.stdout).strip()[:400])
        return 1
    print("[2/4] 已提交", git("rev-parse", "--short", "HEAD"))

    # ---- 推送：先 git，不行再走 API ----
    print("[3/4] 推送到 GitHub …")
    push = subprocess.run(["git", "push", "origin", GH_BRANCH], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if push.returncode == 0:
        print("      git push 成功")
    else:
        why = (push.stderr or "").strip().splitlines()
        print("      git push 失败（%s）" % (why[-1] if why else "未知原因"))
        print("      改用 GitHub API 通道 …")
        api = subprocess.run([sys.executable, os.path.join(HERE, "push_via_api.py")],
                             cwd=HERE, capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        tail = (api.stdout or "").strip().splitlines()
        if api.returncode != 0:
            print("      API 推送也失败：")
            print("\n".join(((api.stdout or "") + (api.stderr or "")).splitlines()[-6:]))
            return 1
        print("      " + (tail[-1] if tail else "API 推送完成"))

    # ---- 回读确认 ----
    print("[4/4] 回读远端确认 …")
    try:
        import app_update as upd
        import time as _t
        got = None
        # GitHub 有几十秒缓存，刚推完可能还读到旧值 —— 重试几次再下结论
        for attempt in range(5):
            r = upd.check()
            if not r.get("ok"):
                print("      回读失败（不影响推送）：%s" % r.get("error"))
                break
            got = str(r.get("remote"))
            if got == new_ver:
                break
            print("      远端还是 %s（GitHub 缓存），%d 秒后重试…" % (got, 8))
            _t.sleep(8)
        if got == new_ver:
            print("      确认成功：客户端下次检查更新就能拿到 %s" % new_ver)
        elif got:
            print("      ⚠ 远端读到的是 %s，不是刚发的 %s（可能仍在缓存，稍后再看）"
                  % (got, new_ver))
    except Exception as ex:
        print("      回读异常（不影响推送）：%s" % ex)

    print("\n✅ 发版完成：%s" % new_ver)
    return 0


if __name__ == "__main__":
    sys.exit(main())
