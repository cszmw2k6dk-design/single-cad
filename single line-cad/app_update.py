#!/usr/bin/env python3
"""app_update.py -- 在线更新。

为什么要"外挂代码"而不是替换 exe：
  程序自己正在运行，Windows 下覆盖正在执行的 exe 会失败（文件被占用）。
  所以更新只下代码包（zip），解压到 exe 旁边的 `_update/` 里；
  下次启动时 run_app.py 把这个目录插到 sys.path 最前面，就盖住了 exe 里内置的旧代码
  —— 不用重装、不用换 exe，改完的排版/生成逻辑立刻生效。

版本从哪来：
  仓库根目录的 `version.json`（每次发版由 tools_make_version.py 生成）。
  程序启动时会去拉它，比对里面的 version 和本地记录的版本。

离线/私有仓库怎么办：
  所有网络错误都会被吞掉并原样返回失败原因，界面只显示一行提示，绝不影响正常生成。
  私有仓库需要在 exe 旁边放 `update_token.txt`（内容是一个能读仓库的 GitHub token）。
"""

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
import zipfile

import runtime_paths as rp

# ---- 更新源：改这里就能指向别的仓库 -----------------------------------------
GH_OWNER = "cszmw2k6dk-design"
GH_REPO = "single-cad"
GH_BRANCH = "main"

RAW_BASE = "https://raw.githubusercontent.com/%s/%s/%s" % (GH_OWNER, GH_REPO, GH_BRANCH)
VERSION_URL = RAW_BASE + "/version.json"
ARCHIVE_URL = "https://codeload.github.com/%s/%s/zip/refs/heads/%s" % (GH_OWNER, GH_REPO, GH_BRANCH)

TIMEOUT = 20                      # 拉 version.json 的超时（秒）
DL_TIMEOUT = 120                  # 下代码包的超时（秒）

# 打包成 exe 时，只有这些文件是我们需要的源码（其余是块库/模板/文档，不参与更新）
CODE_SUFFIX = ".py"


def _app_dir():
    return os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))


UPDATE_DIR = os.path.join(_app_dir(), "_update")
STATE_PATH = os.path.join(UPDATE_DIR, "state.json")
TOKEN_PATH = os.path.join(_app_dir(), "update_token.txt")


# --------------------------- 安装 / 读取状态 ---------------------------
def installed_dir():
    """已经下载好的代码放在哪（`_update/`）。里面还有文件才算装上了。"""
    if not os.path.isdir(UPDATE_DIR):
        return None
    for f in os.listdir(UPDATE_DIR):
        if f.endswith(CODE_SUFFIX):
            return UPDATE_DIR
    return None


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(d):
    os.makedirs(UPDATE_DIR, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def local_version():
    """本地代码版本：下载过就用下载的版本号，否则读内置的 version.json。"""
    st = load_state()
    if installed_dir() and st.get("version"):
        return st["version"]
    bases = [UPDATE_DIR, rp.APP_ROOT]
    _mp = getattr(sys, "_MEIPASS", "")
    if _mp:
        bases.append(_mp)
    for base in bases:
        if not base:
            continue
        p = os.path.join(base, "version.json")
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f).get("version", "0")
        except Exception:
            continue
    return "0"


def local_note():
    st = load_state()
    if installed_dir():
        return "已应用在线更新（%s）" % st.get("version", "?")
    return "内置版本"


# --------------------------- 网络 ---------------------------
def _headers():
    h = {"User-Agent": "barnett-single-line-updater"}
    try:
        with open(TOKEN_PATH, encoding="utf-8") as f:
            tok = f.read().strip()
        if tok:
            h["Authorization"] = "token " + tok
    except Exception:
        pass
    return h


def _get(url, timeout):
    req = urllib.request.Request(url, headers=_headers())
    ctx = ssl.create_default_context()
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def fetch_remote_version():
    """拉远端 version.json。返回 (dict 或 None, 错误说明)。"""
    try:
        with _get(VERSION_URL, TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8")), ""
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            return None, ("更新服务器上还没有 version.json（HTTP 404）："
                          "先用 make_release.py --push 发一次版")
        if ex.code in (401, 403):
            return None, ("读不到 version.json（HTTP %d）：仓库可能是私有的，"
                          "需要在程序旁边放 update_token.txt（GitHub token）" % ex.code)
        return None, "读不到 version.json（HTTP %d）" % ex.code
    except Exception as ex:
        return None, "连不上更新服务器（%s: %s）" % (type(ex).__name__, ex)


def check():
    """检查有没有新版本。返回 dict（给界面用），不会抛异常。"""
    local = local_version()
    remote, err = fetch_remote_version()
    if remote is None:
        return {"ok": False, "local": local, "error": err}
    rv = str(remote.get("version", "0"))
    newer = _is_newer(rv, local)
    return {"ok": True, "local": local, "remote": rv, "newer": newer,
            "time": remote.get("time", ""), "notes": remote.get("notes", ""),
            "url": remote.get("url") or ARCHIVE_URL,
            "applied": bool(installed_dir())}


def _is_newer(remote, local):
    """版本号比较：优先按 (年,月,日,时,分) 这种数字段比，比不出就按字符串。"""
    def segs(v):
        out = []
        for part in str(v).replace("-", ".").replace("_", ".").split("."):
            out.append(int(part) if part.isdigit() else -1)
        return out
    a, b = segs(remote), segs(local)
    if a != b:
        return a > b
    return str(remote) != str(local)


# --------------------------- 下载并安装 ---------------------------
def download_and_install(url=None, progress=None):
    """下载代码包 → 解压覆盖到 `_update/`。返回 (ok, 说明)。"""
    url = url or ARCHIVE_URL

    def pg(pct, stage):
        if progress:
            try:
                progress(pct, stage)
            except Exception:
                pass

    pg(5, "连接更新服务器")
    try:
        resp = _get(url, DL_TIMEOUT)
    except Exception as ex:
        return False, "下载代码包失败（%s: %s）" % (type(ex).__name__, ex)

    tmp = os.path.join(_app_dir(), "_update.zip")
    try:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    pg(5 + int(got * 70 / total), "下载代码包 %d%%" % int(got * 100 / total))
        resp.close()
    except Exception as ex:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, "写临时文件失败（%s: %s）" % (type(ex).__name__, ex)

    pg(80, "解压")
    try:
        os.makedirs(UPDATE_DIR, exist_ok=True)
        n = 0
        with zipfile.ZipFile(tmp) as z:
            for name in z.namelist():
                base = os.path.basename(name)
                # 只取 .py，并且只取顶层（GitHub 的包会多一层 仓库名-分支/ 目录）
                if not base.endswith(CODE_SUFFIX) or "/" not in name.strip("/"):
                    continue
                rel = name.split("/", 1)[1] if "/" in name else name
                if "/" in rel:
                    continue                      # 只要顶层文件，子目录不收
                with z.open(name) as src, open(os.path.join(UPDATE_DIR, rel), "wb") as dst:
                    dst.write(src.read())
                n += 1
        os.remove(tmp)
    except Exception as ex:
        return False, "解压失败（%s: %s）" % (type(ex).__name__, ex)

    if not n:
        return False, "代码包里没找到可用的 .py 文件"

    remote, _err = fetch_remote_version()
    save_state({"version": (remote or {}).get("version", "unknown"),
                "files": n,
                "time": (remote or {}).get("time", "")})
    pg(100, "更新已下载")
    return True, "更新已下载：%d 个文件（关掉窗口重新打开即生效）" % n
