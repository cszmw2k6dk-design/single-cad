#!/usr/bin/env python3
"""runtime_paths.py -- 源码运行 / 打包成 exe 两用的路径解析。

为什么需要它：打包成 exe（PyInstaller）以后，代码被塞进 exe，模块里的
`__file__` 指向临时解包目录，块库/模板/输出再按它算就会找不到（或者写进临时目录，
退出就没了，用户在 CAD 里改的块也会丢）。

所以三个目录统一走这里：
  源码运行   -> 跟现在完全一样，就在 .py 旁边
  打包成 exe -> 在 exe 所在的文件夹里（块库/模板留在 exe 外面，随时能改）

额外兜底：exe 旁边没有 blocklib/templates 时，退回 PyInstaller 的解包目录
（sys._MEIPASS）。这样就算有人只拷了 exe 过去，也能跑起来（只是块库是只读的副本）。
"""

import os
import sys

frozen = bool(getattr(sys, "frozen", False))


def _dir(*parts):
    """在几个候选根目录里挑第一个真实存在的，拼出目标目录。"""
    for cand in _roots():
        p = os.path.join(cand, *parts)
        if os.path.isdir(p):
            return p
    # 一个都没有：返回首选根下的路径（让调用方自己 makedirs / 报错）
    return os.path.join(_roots()[0], *parts)


def _roots():
    """候选根目录，按优先级排：
       1. exe 所在目录（打包后用户能改的那份）
       2. PyInstaller 的解包目录（只有 exe 时的只读兜底）
       3. 模块所在目录（源码运行）
    """
    out = []
    if frozen:
        out.append(os.path.dirname(os.path.abspath(sys.executable)))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            out.append(meipass)
    out.append(os.path.dirname(os.path.abspath(__file__)))
    seen, uniq = set(), []
    for d in out:
        if d and d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


APP_ROOT = _roots()[0]
BLOCKS_DIR = _dir("blocklib", "blocks")
BLOCKLIB_DIR = _dir("blocklib")
MANIFEST = os.path.join(BLOCKLIB_DIR, "_manifest.csv")
FRAMES_DIR = _dir("templates")
OUTDIR = os.path.join(APP_ROOT, "out")
