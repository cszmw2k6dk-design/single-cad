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
import time
import urllib.error
import urllib.request
import zipfile

import runtime_paths as rp

# ---- 更新源：改这里就能指向别的仓库 -----------------------------------------
GH_OWNER = "cszmw2k6dk-design"
GH_REPO = "single-cad"
GH_BRANCH = "main"

RAW_BASE = "https://raw.githubusercontent.com/%s/%s/%s" % (GH_OWNER, GH_REPO, GH_BRANCH)
JSD_BASE = "https://cdn.jsdelivr.net/gh/%s/%s@%s" % (GH_OWNER, GH_REPO, GH_BRANCH)
API_BASE = "https://api.github.com/repos/%s/%s" % (GH_OWNER, GH_REPO)

# 代码里的 version.json 在仓库里是这个相对路径（仓库根下还有一层程序目录）
VERSION_REPO_PATH = "single%20line-cad/version.json"

# 拉 version.json 的候选地址，从上往下挨个试：有的网络能通 GitHub API 但连不上
# raw.githubusercontent.com（企业防火墙常见），所以必须留后手。
VERSION_SOURCES = [
    # 顺序很重要：以 **GitHub API 的 contents 接口为准** —— 它读的是"当前提交里的内容"，
    # 不会像 raw / jsDelivr 那样被 CDN 缓存住旧版本（实测 raw 会返回几分钟前的旧
    # version.json，害得刚发完版却提示"已是最新"）。
    ("api", API_BASE + "/contents/" + VERSION_REPO_PATH),
    ("jsdelivr", JSD_BASE + "/single%20line-cad/version.json"),
    ("raw", RAW_BASE + "/single%20line-cad/version.json"),
]

# 代码包（zip）的候选地址，同上
ARCHIVE_SOURCES = [
    ("codeload", "https://codeload.github.com/%s/%s/zip/refs/heads/%s" % (GH_OWNER, GH_REPO, GH_BRANCH)),
    ("api", API_BASE + "/zipball/" + GH_BRANCH),
]
ARCHIVE_URL = ARCHIVE_SOURCES[0][1]

TIMEOUT = 12                      # 拉 version.json 的单路超时（秒）
DL_TIMEOUT = 180                  # 下代码包的单路超时（秒）

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
    # no-cache：GitHub 的 raw/API 都有几十秒缓存，刚发完版本马上查会拿到旧值
    h = {"User-Agent": "barnett-single-line-updater",
         "Cache-Control": "no-cache", "Pragma": "no-cache"}
    try:
        with open(TOKEN_PATH, encoding="utf-8") as f:
            tok = f.read().strip()
        if tok:
            h["Authorization"] = "token " + tok
    except Exception:
        pass
    return h


def _get(url, timeout):
    # 给 URL 挂一个时间戳参数，绕开 CDN 的缓存（jsDelivr / raw 都有缓存）
    sep = "&" if "?" in url else "?"
    if "/contents/" in url:          # API 的 contents 接口用 ?ref= 形式，别塞时间戳
        pass
    else:
        url = url + sep + "_=%d" % int(time.time())
    req = urllib.request.Request(url, headers=_headers())
    ctx = ssl.create_default_context()
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def fetch_remote_version():
    """拉远端 version.json。返回 (dict 或 None, 错误说明)。

    依次试 raw / jsDelivr / GitHub API（API 那条会把 base64 内容解出来）。
    """
    errs = []
    for name, url in VERSION_SOURCES:
        if name == "api":
            # 绕开 contents 接口的响应缓存（~60 秒）
            url = url + "?ref=" + GH_BRANCH + "&_=%d" % int(time.time())
        try:
            with _get(url, TIMEOUT) as r:
                raw = r.read()
            if name == "api":
                # API 返回 {"content": "<base64>", "encoding": "base64", ...}
                info = json.loads(raw.decode("utf-8"))
                import base64
                raw = base64.b64decode(info.get("content", ""))
            return json.loads(raw.decode("utf-8")), ""
        except urllib.error.HTTPError as ex:
            if ex.code == 404 and name == "raw":
                errs.append("更新服务器上还没有 version.json（HTTP 404）："
                            "先用 make_release.py --push 发一次版")
            elif ex.code in (401, 403):
                errs.append("%s: HTTP %d（仓库可能是私有的，需要 update_token.txt）"
                            % (name, ex.code))
            else:
                errs.append("%s: HTTP %d" % (name, ex.code))
        except Exception as ex:
            errs.append("%s: %s" % (name, type(ex).__name__))
    return None, "连不上更新源（" + "；".join(errs) + "）"


def tip_commit():
    """远端分支当前的提交号（API 读，最权威）。拿不到就返回空串。"""
    try:
        with _get(API_BASE + "/commits/" + GH_BRANCH, TIMEOUT) as r:
            return (json.loads(r.read().decode("utf-8")) or {}).get("sha", "")
    except Exception:
        return ""


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
    def pg(pct, stage):
        if progress:
            try:
                progress(pct, stage)
            except Exception:
                pass

    # 先把版本号定下来（顺便证明更新源是通的），下载完直接写状态，不用再查一次
    remote, _verr = fetch_remote_version()
    remote_ver = str((remote or {}).get("version", "") or "")
    tip = tip_commit()

    # 多路回退：codeload 连不上就试 GitHub API 的 zipball
    cands = [("指定地址", url)] if url else list(ARCHIVE_SOURCES)
    if not url:      # API 那条给的就是当前提交，最即时，优先走
        cands = [c for c in cands if c[0] == "api"] + [c for c in cands if c[0] != "api"]
    resp, errs = None, []
    for name, u in cands:
        pg(5, "连接更新服务器（%s）" % name)
        try:
            resp = _get(u, DL_TIMEOUT)
            break
        except Exception as ex:
            errs.append("%s: %s" % (name, type(ex).__name__))
    if resp is None:
        return False, "下载代码包失败（" + "；".join(errs) + "）"

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
                if name.endswith("/"):
                    continue
                parts = name.split("/")
                if not parts[-1].endswith(CODE_SUFFIX):
                    continue
                # GitHub 的包外面多一层 仓库名-分支/；本仓库的代码又在 single line-cad/ 下
                rel = "/".join(parts[1:]) if len(parts) > 1 else parts[-1]
                if rel.startswith("single line-cad/"):
                    rel = rel[len("single line-cad/"):]
                if "/" in rel:
                    continue                      # 只要代码目录下的顶层 .py
                with z.open(name) as src, open(os.path.join(UPDATE_DIR, rel), "wb") as dst:
                    dst.write(src.read())
                n += 1
        os.remove(tmp)
    except Exception as ex:
        return False, "解压失败（%s: %s）" % (type(ex).__name__, ex)

    if not n:
        return False, "代码包里没找到可用的 .py 文件"

    if not remote_ver:
        remote, _err = fetch_remote_version()
        remote_ver = str((remote or {}).get("version", "") or "")
        remote = remote or {}
    save_state({"version": remote_ver or "unknown",
                "files": n,
                "tip": tip,
                "time": (remote or {}).get("time", ""),
                "time_local": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    pg(100, "更新已下载")
    return True, "更新已下载：%d 个文件（关掉窗口重新打开即生效）" % n
