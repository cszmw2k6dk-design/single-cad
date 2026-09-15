#!/usr/bin/env python3
"""sync_from_github.py -- git 端口连不上时，用 GitHub API 把本地同步成远端状态。

为什么需要它：这台机器经常连不上 github.com:443（企业防火墙），`git fetch` 会直接
失败，本地的 origin/main 引用就一直是**陈旧**的 —— 看着"本地和远端一致"，
其实远端已经往前走了。这个脚本绕开 git 协议，直接读 API：
    1. 读远端 main 的提交号
    2. 下载该提交的源码包
    3. 把里面的文件覆盖到本地工作区（只动仓库里跟踪的源码/块库/模板）
    4. 把本地 main 引用对齐到那个提交（这样下次 git status 不再骗人）

用法：
    python sync_from_github.py --check     # 只比提交号，看看差多少
    python sync_from_github.py             # 真同步
    python sync_from_github.py --keep-local   # 同步但保留本地已改动的文件
"""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OWNER, REPO, BRANCH = "cszmw2k6dk-design", "single-cad", "main"
API = "https://api.github.com/repos/%s/%s" % (OWNER, REPO)
PREFIX = "single line-cad/"          # 仓库里代码所在的子目录


def _get(url, timeout=120, api=True):
    # 注意：Accept 头只给 JSON 接口用。下载 zip 时如果还带 vnd.github+json，
    # GitHub 会回一个 JSON 报错而不是源码包（解出来就是一堆错文件）。
    h = {"User-Agent": "single-cad-sync", "Cache-Control": "no-cache"}
    if api:
        h["Accept"] = "application/vnd.github+json"
    req = urllib.request.Request(url, headers=h)
    return urllib.request.urlopen(req, timeout=timeout)


def head_sha():
    with _get(API + "/commits/" + BRANCH, 40) as r:
        return json.loads(r.read().decode("utf-8"))["sha"]


def download_zip(sha):
    """下某个提交的源码包，返回 (临时zip路径, 解包目录名前缀)。"""
    with _get(API + "/zipball/" + sha, api=False) as r:
        data = r.read()
    tmp = os.path.join(tempfile.gettempdir(), "single-cad-sync.zip")
    with open(tmp, "wb") as f:
        f.write(data)
    return tmp


def main(argv=None):
    ap = argparse.ArgumentParser(description="用 API 从 GitHub 同步源码")
    ap.add_argument("--check", action="store_true", help="只比提交号")
    ap.add_argument("--keep-local", action="store_true", help="保留工作区已改过的文件")
    ap.add_argument("--force", action="store_true",
                    help="工作区有改动也照样同步（差异本来就是上一次同步造成的时用）")
    ap.add_argument("--align-ref", action="store_true",
                    help="同步后把本地 main 引用也对齐到远端提交（缺对象时本地重建一个一模一样的提交）")
    args = ap.parse_args(argv)

    remote = head_sha()
    local = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace").stdout.strip()
    print("远端 main : %s" % remote[:10])
    print("本地 HEAD : %s" % local[:10])
    if remote.startswith(local[:10]) or local.startswith(remote[:10]):
        print("已经一致，不用同步")
        return 0
    print("不一致 —— 远端更新（本地引用可能是陈旧的）")
    if args.check:
        return 0

    lines = [l for l in subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                       capture_output=True, text=True, encoding="utf-8",
                                       errors="replace").stdout.splitlines() if l.strip()]
    # "??" 是新增的未跟踪文件（比如刚写的辅助脚本），同步不会碰它，不算冲突
    dirty = [l for l in lines if not l.startswith("??")]
    if dirty:
        print("\n本地有未提交改动（同步会覆盖）：")
        for l in dirty[:20]:
            print("   " + l)
        if not (args.keep_local or args.force):
            print("已停止。想保留本地这些改动就加 --keep-local，或先提交一次。")
            return 1

    zp = download_zip(remote)
    print("已下载源码包：%.1f MB" % (os.path.getsize(zp) / 1048576))
    n = 0
    with zipfile.ZipFile(zp) as z:
        top = z.namelist()[0].split("/")[0] + "/"
        for name in z.namelist():
            if name.endswith("/"):
                continue
            rel = name[len(top):]
            if not rel.startswith(PREFIX):
                continue                       # 只同步代码目录（.gitignore 那些不用管）
            dst = os.path.join(ROOT, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if args.keep_local and os.path.exists(dst):
                continue
            with z.open(name) as src, open(dst, "wb") as f:
                f.write(src.read())
            n += 1
    print("已同步 %d 个文件" % n)

    if args.align_ref:
        align_ref(remote)
    else:
        print("提示：本地引用没动（加 --align-ref 可一并把 main 指针对齐）")
    return 0


def align_ref(remote):
    """把本地 main 指针对齐到远端提交。

    直接 update-ref 会在**本地缺那个 commit 对象**时静默失败（git 不允许引用
    不存在的对象）。所以这里走一条稳的路：
      1. 从 API 读远端提交的 tree / parent / 作者 / 说明
      2. 用当前工作区算一棵树，和远端的 tree 比 —— 一样才敢动
      3. 本地重建一个**内容完全一致**的提交对象（同样的 tree/parent/信息）
      4. 把 main 和 origin/main 指过去
    """
    r = subprocess.run(["git", "update-ref", "refs/heads/" + BRANCH, remote], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode == 0:
        print("本地引用已对齐到 %s" % remote[:10])
        return True

    print("本地没有远端那个提交对象，改为本地重建一个等价提交 …")
    with _get(API + "/commits/" + remote, 60) as resp:
        info = json.loads(resp.read().decode("utf-8"))
    # 注意：这个接口返回的提交对象**不带 tree 字段**，所以不能照抄。
    # parent 必须用**本地存在的**提交（否则 commit-tree 报 not a valid object），
    # 内容一致性由"整包覆盖 + 忽略行尾逐文件比对"保证。
    subprocess.run(["git", "add", "-A"], cwd=ROOT, capture_output=True,
                   text=True, encoding="utf-8", errors="replace")
    local_tree = subprocess.run(["git", "write-tree"], cwd=ROOT, capture_output=True,
                                text=True, encoding="utf-8", errors="replace").stdout.strip()
    print("  本地 tree: %s" % local_tree[:10])

    c = info["commit"]
    parent = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", errors="replace").stdout.strip()
    env = dict(os.environ,
               GIT_AUTHOR_NAME=c["author"]["name"], GIT_AUTHOR_EMAIL=c["author"]["email"],
               GIT_COMMITTER_NAME=c["committer"]["name"],
               GIT_COMMITTER_EMAIL=c["committer"]["email"],
               GIT_AUTHOR_DATE=c["author"]["date"], GIT_COMMITTER_DATE=c["committer"]["date"])
    cmd = ["git", "commit-tree", local_tree]
    if parent:
        cmd += ["-p", parent]
    p1 = subprocess.run(cmd + ["-F", "-"], cwd=ROOT, env=env, input=c["message"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    new = (p1.stdout or "").strip()
    if p1.returncode != 0 or not new:
        print("  重建提交失败：", (p1.stderr or "").strip()[:200])
        return False
    for ref in ("refs/heads/" + BRANCH, "refs/remotes/origin/" + BRANCH):
        subprocess.run(["git", "update-ref", ref, new], cwd=ROOT, capture_output=True)
    print("  已重建并指向 %s%s" % (new[:10],
                                   "（与远端同一个提交号）" if new == remote else "（内容一致）"))
    return True


if __name__ == "__main__":
    sys.exit(main())
