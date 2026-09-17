#!/usr/bin/env python3
"""assemble_app.py -- 把 PyInstaller 的产物整理成一个可以直接拷走的文件夹。

用法：python assemble_app.py [--dest 目标目录]
默认目标：项目上一级的「Single-CAD」文件夹。

摆出来的结构：
    Single-CAD/
        Single-CAD.exe     双击运行
        blocklib/                  块库（可以随时改/加块）
        templates/                 外框模板（放新的 DXF 进来就能用）
        out/                       生成结果、线长表
        使用说明.txt

重复执行是安全的：blocklib 用「缺什么补什么」合并，不会覆盖你在里面改过的块；
exe 每次覆盖成最新的。
"""

import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXE_NAME = "Single-CAD.exe"

README = """Single-CAD
=====================

【怎么用】
1. 双击 Single-CAD.exe
2. 会弹出一个**桌面窗口**（不是浏览器），界面就在窗口里
3. 左边选块 → 右边排成线束链（可以不填）→ 填好串数/板数 → 点「生成」
4. 两种模式：
   光伏阵列 + 线束   —— 出一张图
   批量（一行一张）  —— 一行一张图，全部拼进**同一张图纸**：每张各自调一份新的
                        外框模板独立生成，在 CAD 里每张是一个整块（块名 = 图号），
                        想挪就整块挪；图与图的间距/排法在上面那行填
5. 出图方式就两种：
   不勾「直接画到 CAD(COM)」 —— 只出 .dxf 文件（在 out/ 里，旁边还有线长清单 .csv）
   勾上「直接画到 CAD(COM)」 —— 先出上面那份 DXF，再用 COM 画进 ZWCAD；
                               画在副本上（不动你的外框原文件），逐张报用时
6. 界面右上角有「中文 / English」下拉，点一下整个界面（连生成日志一起）就换语言；
   选完会记住，下次打开还是这个语言。

【电机 / BHA 桩：插在两块板之间】
阵列参数下面有一张「电机 / BHA 桩位置」小表，一行 = 一处插入：
    串号        留空 = 所有串；也可以写 1,3 或 2-4
    插在第几块之后  0 = 第 1 块之前，1 = 第 1 块之后……填得比每串板数大就排到最后
    BHA 桩块     从块库里选（还没做桩块的话可以先用 MOTOR 顶）
    电机块       挂在桩的同一个插入点上，按「电机旋转」转（0/90/180/270）
    桩左/右净空   留空 = 跟随上面的「板间净空」
插进去以后：桩**右边的板整体右移**（位移 = 桩宽 + 左净空 + 右净空 − 板间净空），
阵列变宽、缩放 k 自动重算，线束的正/负极支线跟着所在那一串走，不用手工调。
想让串内连线接在桩上，就在桩块里左右各标一个 CONN 层的 POINT；
没标也能出图，只是线从板直接连到板、穿过桩（日志里会提示）。
批量模式每行有一列「BHA位置」可以单独填，留空就沿用上面那张表；
写法：串号:第几块之后:桩块:电机块:旋转:左净空:右净空，多处用 ; 隔开，
例 全部:10:BHA:MOTOR:0 或 2:5:BHA:MOTOR:90;4:12:BHA。

【生成完怎么拿图纸】
生成完图纸下面有一排按钮（窗口里点它们，不用去翻文件夹）：
    用默认程序打开(DXF)  —— 直接用 ZWCAD 打开这张图（.dxf 的默认程序就是 ZWCAD）
    保存到桌面          —— 复制一份到桌面，重名会自动加序号，不会覆盖
    打开输出文件夹      —— 在资源管理器里定位到刚生成的文件
    线长清单存到桌面    —— 把 .csv 线长表也复制到桌面
（窗口里的「下载 DXF」链接是给浏览器模式用的，桌面窗口里点它没反应是正常的，
  用上面那几个按钮就行。）

【三个文件夹是干什么的】
blocklib/   块库。所有块（POS/NEG/Male/Fmale/CBX/FUSE 等）都在 blocklib/blocks/ 里，
             每个块一个 .dxf。想加块/改块，直接在这里换文件，重启程序就生效。
templates/  外框模板。把你新的外框 .dxf 放进来，界面上「外框图」下拉里就会出现。
             注意：想「画到 CAD」的话，同名的 .dwg 也要放在这里（程序会打开它来画）。
out/        生成的东西都写在这里，可以随时清空。

【关掉程序】
直接关掉窗口就行（关窗即退出，没有黑窗）。

【出问题怎么查】
exe 旁边会自动生成「启动日志.txt」，里面记着启动过程、块库/模板路径、
以及报错信息。双击没反应、或者窗口没出来时，先看这个文件。

想改用浏览器打开：命令行运行
    Single-CAD.exe --browser

想指定端口（默认 8770，被占用会自动往后试）：
    Single-CAD.exe --port 8790

【常见问题】
Q: 双击没反应 / 窗口没弹出来？
A: 先看 exe 旁边的「启动日志.txt」。窗口用的是系统的 Edge WebView2 组件，
   极老的 Win10 可能没装（去微软官网搜「WebView2 Runtime」装一次即可）；
   实在不行用 --browser 参数走浏览器。

Q: 生成的图里块没显示、只有线？
A: 那是外框图里没有这个块的块定义。块库里有、但选的外框图模板里没有的情况，
   程序会自动把块定义并进外框图，日志里会提示「已把块库定义并入外框图」。

Q: 想画到 ZWCAD，提示连不上？
A: 先手动打开 ZWCAD 2026，再在界面上勾「画到 CAD」。程序是通过 COM 调 ZWCAD 的，
   ZWCAD 没开就会失败。

Q: 改了 blocklib 里的块，界面没变化？
A: 需要重启本程序（关掉窗口再双击 exe）。块库是启动时读的。

【重新打包（改了 .py 以后）】
在项目根目录执行：
    python -m PyInstaller build_app.spec --noconfirm
    python assemble_app.py
第一条生成 exe，第二条把它和块库/模板整理成本文件夹。

【在线更新】
程序启动时会自动去 GitHub 仓库查一次版本，界面右上角也有「检查更新」按钮。
发现新版会问你要不要下载；下载完成后**关掉窗口重新打开**即生效
（更新下来的代码放在 _update/ 里，不会动这个 exe）。
更新源：https://github.com/cszmw2k6dk-design/single-cad

【发版（把改动推给用户的程序）】
双击项目根目录的 publish.cmd，或者命令行：
    python "single line-cad/publish.py" -m "这次改了什么"
它会自动升版本号、提交、推到 GitHub；推完你的程序点「检查更新」就能收到。
"""


def copy_missing(src, dst, log):
    """把 src 里、dst 里没有的文件补过去（已有的不动，保护用户改过的内容）。"""
    n = 0
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target, exist_ok=True)
        for f in files:
            sp, dp = os.path.join(root, f), os.path.join(target, f)
            if not os.path.exists(dp):
                shutil.copy2(sp, dp)
                log.append("  + %s" % os.path.join(rel, f) if rel != "." else "  + " + f)
                n += 1
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description="整理打包产物")
    ap.add_argument("--dest", default=os.path.join(os.path.dirname(HERE),
                                                   "Single-CAD"))
    ap.add_argument("--dist", default=os.path.join(HERE, "dist"))
    args = ap.parse_args(argv)

    exe_src = os.path.join(args.dist, EXE_NAME)
    if not os.path.exists(exe_src):
        print("找不到 exe：%s" % exe_src)
        print("先跑：python -m PyInstaller build_app.spec --noconfirm")
        return 1

    dest = os.path.abspath(args.dest)
    os.makedirs(dest, exist_ok=True)
    log = []

    # exe：每次都覆盖成最新的
    shutil.copy2(exe_src, os.path.join(dest, EXE_NAME))
    print("已放好 exe:", os.path.join(dest, EXE_NAME))

    # 块库/模板：缺什么补什么，绝不覆盖用户改过的
    for sub in ("blocklib", "templates"):
        src = os.path.join(HERE, sub)
        if os.path.isdir(src):
            added = copy_missing(src, os.path.join(dest, sub), log)
            print("%s：新增 %d 个文件" % (sub, added))

    # 输出目录 + 使用说明
    os.makedirs(os.path.join(dest, "out"), exist_ok=True)
    with open(os.path.join(dest, "使用说明.txt"), "w", encoding="utf-8-sig",
              newline="\r\n") as f:
        f.write(README)

    print("完成 ->", dest)
    if log:
        print("本次补进来的文件：")
        for l in log[:40]:
            print(l)
        if len(log) > 40:
            print("  ... 还有 %d 个" % (len(log) - 40))
    return 0


if __name__ == "__main__":
    sys.exit(main())
