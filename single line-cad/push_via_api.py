#!/usr/bin/env python3
"""push_via_api.py -- 用 GitHub API 把本地提交推上去（git 协议连不上时的备用通道）。

背景：有些网络环境能通 api.github.com，却连不上 github.com:443，`git push` 会
「Failed to connect to github.com port 443」。这个脚本改走 REST API：
逐文件上传（blob）→ 组一棵新树 → 建提交 → 更新分支。

凭据从 `git credential fill` 拿（就是系统里已经存的那份），不落盘、不回显。

用法：
    python push_via_api.py                  # 把本地 HEAD 推到 origin 同名分支
    python push_via_api.py --dry-run        # 只看要传哪些文件
"""

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(os.path.dirname(__file__)))
OWNER, REPO, BRANCH = "cszmw2k6dk-design", "single-cad", "main"
API = "https://api.github.com"


def git(*args):
    out = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    return (out.stdout or "").strip()


def token():
    try:
        out = subprocess.run(["git", "credential", "fill"], cwd=ROOT, input=
                             "protocol=https\nhost=github.com\n\n",
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
    except Exception as ex:
        print("取凭据失败:", ex)
        return None
    for line in (out.stdout or "").splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    return None


def api(method, path, tok, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": "Bearer " + tok,
        "Accept": "application/vnd.github+json",
        "User-Agent": "single-cad-push",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def blob_sha(path):
    """算本地文件的 git blob sha（判断远端是不是已经一致，省得重复上传）。"""
    with open(path, "rb") as f:
        data = f.read()
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def local_files():
    """仓库里被 git 跟踪的文件（相对路径 -> 绝对路径）。"""
    out = {}
    for rel in git("ls-files").splitlines():
        p = os.path.join(ROOT, rel.replace("/", os.sep))
        if os.path.isfile(p):
            out[rel] = p
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="用 API 推送到 GitHub")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--message", default="")
    args = ap.parse_args(argv)

    tok = token()
    if not tok:
        print("拿不到 GitHub 凭据，没法用 API 推送")
        return 1

    head = git("rev-parse", "HEAD")
    msg = args.message or git("log", "-1", "--format=%s")
    files = local_files()
    print("本地提交 %s，跟踪文件 %d 个" % (head[:7], len(files)))

    ref = api("GET", "/repos/%s/%s/git/ref/heads/%s" % (OWNER, REPO, BRANCH), tok)
    base = ref["object"]["sha"]
    print("远端分支当前提交: %s" % base[:7])
    if base == head:
        print("远端已经是最新，不用推")
        return 0

    base_tree = api("GET", "/repos/%s/%s/git/commits/%s" % (OWNER, REPO, base), tok)["tree"]["sha"]
    # 拉出远端现有文件清单（路径 -> blob sha），只提交**内容确实变了**的文件。
    # 这样即使远端有我这没有的提交，也不会被整棵树覆盖掉。
    remote_tree = api("GET", "/repos/%s/%s/git/trees/%s?recursive=1"
                      % (OWNER, REPO, base_tree), tok)
    remote_sha = {t["path"]: t["sha"] for t in remote_tree.get("tree", [])
                  if t.get("type") == "blob"}

    tree_items, changed = [], []
    for rel, abs_p in sorted(files.items()):
        sha = blob_sha(abs_p)
        if remote_sha.get(rel) == sha:
            continue                       # 远端内容一样，不用动
        with open(abs_p, "rb") as f:
            data = f.read()
        if args.dry_run:
            changed.append(rel)
            tree_items.append({"path": rel, "mode": "100644", "type": "blob", "sha": sha})
            continue
        # 内容寻址：同内容会返回同一个 sha，GitHub 侧自动去重
        blob = api("POST", "/repos/%s/%s/git/blobs" % (OWNER, REPO), tok,
                   {"content": base64.b64encode(data).decode("ascii"),
                    "encoding": "base64"})
        tree_items.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        changed.append(rel)

    print("要上传 %d 个文件（其余与远端一致，跳过）" % len(changed))
    for c in changed[:60]:
        print("   ", c)
    if len(changed) > 60:
        print("    ... 还有", len(changed) - 60, "个")
    if not changed:
        print("远端内容已经和本地一致，不需要推送")
        return 0
    if args.dry_run:
        return 0

    tree = api("POST", "/repos/%s/%s/git/trees" % (OWNER, REPO), tok,
               {"base_tree": base_tree, "tree": tree_items})
    commit = api("POST", "/repos/%s/%s/git/commits" % (OWNER, REPO), tok,
                 {"message": msg, "tree": tree["sha"], "parents": [base]})
    api("PATCH", "/repos/%s/%s/git/refs/heads/%s" % (OWNER, REPO, BRANCH), tok,
        {"sha": commit["sha"], "force": False})
    print("已推送: %s" % commit["sha"][:7])
    return 0


if __name__ == "__main__":
    sys.exit(main())
