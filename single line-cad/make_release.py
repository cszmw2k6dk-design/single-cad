#!/usr/bin/env python3
"""make_release.py -- 发版：生成/刷新 version.json，可选地提交并推到 GitHub。

在线更新靠程序目录里的 `version.json`：
    { "version": "v1.0", "time": "...", "notes": "...", "url": "..." }

用法：
    python make_release.py                 # 只写 version.json（看看内容）
    python make_release.py --notes "改了负极行对齐"
    python make_release.py --commit        # 顺便 git add/commit
    python make_release.py --commit --push # 再推到 GitHub（在线更新真正生效）

版本号规则：从 v1.0 起算，默认在现有 version.json 上把**最后一段数字 +1**
（v1.0 → v1.1 → v1.2；想发补丁号就 --version v1.0.1）。
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)              # git 仓库根（上一级）
VERSION_PATH = os.path.join(HERE, "version.json")   # 和程序代码同目录（客户端就找这）

GH_OWNER, GH_REPO, GH_BRANCH = "cszmw2k6dk-design", "single-line-cad", "main"
ARCHIVE = "https://codeload.github.com/%s/%s/zip/refs/heads/%s" % (GH_OWNER, GH_REPO, GH_BRANCH)


def git(*args, default=""):
    try:
        out = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                             text=True, encoding="utf-8", errors="replace")
        return (out.stdout or "").strip() if out.returncode == 0 else default
    except Exception:
        return default


def auto_version():
    """下一个版本号：在现有 version.json 上 +1（v1.0 → v1.1）。

    老式日期号（v20260916.1506，第一段是 8 位日期）和空值一律归到新号起点 v1.0。
    """
    try:
        with open(VERSION_PATH, encoding="utf-8") as f:
            cur = json.load(f).get("version", "")
    except Exception:
        cur = ""
    nums = []
    for part in re.split(r"[._\-+]+", str(cur or "").strip().lstrip("vV")):
        if part.isdigit():
            nums.append(int(part))
        elif part:
            break
    if not nums or nums[0] >= 1000:
        return "v1.0"
    nums[-1] += 1
    return "v" + ".".join(str(n) for n in nums)


def main(argv=None):
    ap = argparse.ArgumentParser(description="刷新 version.json / 发版")
    ap.add_argument("--version", default="", help="手动指定版本号（默认在现有版本上把最后一段 +1）")
    ap.add_argument("--notes", default="", help="这次更新说明（会显示在更新提示里）")
    ap.add_argument("--commit", action="store_true", help="生成后 git commit")
    ap.add_argument("--push", action="store_true", help="提交后 git push")
    args = ap.parse_args(argv)

    ver = args.version or auto_version()
    ver = ver if str(ver).strip()[:1] in ("v", "V") else "v" + str(ver).strip()
    notes = args.notes or git("log", "-1", "--format=%s")
    data = {
        "version": ver,
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "notes": notes or "代码更新",
        "commit": git("rev-parse", "--short", "HEAD"),
        "url": ARCHIVE,
    }
    with open(VERSION_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("已写:", VERSION_PATH)
    print(json.dumps(data, ensure_ascii=False, indent=2))

    if args.commit:
        git("add", os.path.join("single line-cad", "version.json"))
        git("commit", "-m", "发版 %s：%s" % (ver, data["notes"]))
        print("已提交")
    if args.push:
        out = subprocess.run(["git", "push", "origin", "main"], cwd=ROOT,
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
        print(out.stdout.strip() or out.stderr.strip())
        if out.returncode != 0:
            return 1
        print("已推送 —— 客户端下次检查更新就能拿到", ver)
    return 0


if __name__ == "__main__":
    sys.exit(main())
