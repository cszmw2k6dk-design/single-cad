#!/usr/bin/env python3
"""make_release.py -- 发版：生成/刷新 version.json，可选地提交并推到 GitHub。

在线更新靠仓库根目录的 `version.json`：
    { "version": "20260914.2030", "time": "...", "notes": "...", "url": "..." }

用法：
    python make_release.py                 # 只写 version.json（看看内容）
    python make_release.py --notes "改了负极行对齐"
    python make_release.py --commit        # 顺便 git add/commit
    python make_release.py --commit --push # 再推到 GitHub（在线更新真正生效）

版本号规则：默认用「最近一次提交的时间」——UTC 的 YYYYMMDD.HHMM。
也可以 --version 手动指定。
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

GH_OWNER, GH_REPO, GH_BRANCH = "cszmw2k6dk-design", "single-cad", "main"
ARCHIVE = "https://codeload.github.com/%s/%s/zip/refs/heads/%s" % (GH_OWNER, GH_REPO, GH_BRANCH)


def git(*args, default=""):
    try:
        out = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                             text=True, encoding="utf-8", errors="replace")
        return (out.stdout or "").strip() if out.returncode == 0 else default
    except Exception:
        return default


def auto_version():
    """取最近一次提交的时间做版本号（UTC，vYYYYMMDD.HHMM）。"""
    ts = git("log", "-1", "--format=%cd", "--date=format-local:%Y%m%d.%H%M",
             "--date=utc")
    if re.fullmatch(r"\d{8}\.\d{4}", ts or ""):
        return "v" + ts
    now = datetime.datetime.now(datetime.timezone.utc)
    return "v" + now.strftime("%Y%m%d.%H%M")


def main(argv=None):
    ap = argparse.ArgumentParser(description="刷新 version.json / 发版")
    ap.add_argument("--version", default="", help="手动指定版本号（默认取最近提交时间）")
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
