#!/usr/bin/env python3
"""run_app.py -- 打包成 exe 的入口（源码直接跑也一样）。

给用户的体验：**双击 exe，弹出一个真正的桌面窗口**（pywebview + Edge WebView2），
不是打开浏览器标签页。拿不到窗口能力时自动退回浏览器，绝不至于双击没反应。

还做了两件事：
  1. 打包（PyInstaller）后把解包目录加进 sys.path；
  2. 因为 exe 是无控制台的（console=False），把 stdout/stderr 和未捕获异常
     统统写进 exe 旁边的「启动日志.txt」，出问题能查。

用法：
    Single line-CAD.exe                 正常用（桌面窗口）
    Single line-CAD.exe --browser       用浏览器打开
    Single line-CAD.exe --port 8790     指定端口
    Single line-CAD.exe --selftest      自检：起服务、跑一次生成、打印结果并退出
"""

import io
import importlib.abc
import importlib.util
import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, getattr(sys, "_MEIPASS", "")):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

_FROZEN = bool(getattr(sys, "frozen", False))


def _install_update_hook():
    """让「下载到 _update/ 在线更新代码」优先于 exe 里内置的旧代码。

    为什么不用 sys.path：打包成 exe 后模块是从 exe 内部的归档里找的，
    往 sys.path 前面插目录不一定能盖住内置模块。这里直接挂一个 meta_path 查找器，
    凡是 `_update/` 里有同名 .py 的模块，就从那里加载 —— 稳。
    """
    updir = os.path.join(_HERE, "_update")
    if not os.path.isdir(updir):
        return
    names = {f[:-3] for f in os.listdir(updir) if f.endswith(".py")}
    if not names:
        return

    class _FromUpdateDir(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname not in names:          # 只接管顶层那几个模块
                return None
            src = os.path.join(updir, fullname + ".py")
            if not os.path.exists(src):
                return None
            return importlib.util.spec_from_file_location(fullname, src)

    sys.meta_path.insert(0, _FromUpdateDir())
    print("已启用在线更新代码：%s（%d 个模块）" % (updir, len(names)))


_install_update_hook()

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(sys.executable if _FROZEN else __file__)),
                        "启动日志.txt")


class _Tee(io.TextIOBase):
    """把 print 的内容同时写到控制台（有的话）和日志文件。"""

    def __init__(self, stream, path):
        self.stream = stream
        self.path = path

    def write(self, s):
        try:
            if self.stream is not None:
                self.stream.write(s)
        except Exception:
            pass
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(s)
        except Exception:
            pass
        return len(s)

    def flush(self):
        try:
            if self.stream is not None:
                self.stream.flush()
        except Exception:
            pass


def _setup_logging():
    """无控制台运行时，日志文件是唯一的排错入口。"""
    try:
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write("Single line-CAD 启动日志\n")
            f.write("exe: %s\n" % (sys.executable if _FROZEN else __file__))
            f.write("frozen(打包运行): %s\n\n" % _FROZEN)
    except Exception:
        return
    sys.stdout = _Tee(sys.stdout, LOG_PATH)
    sys.stderr = _Tee(sys.stderr, LOG_PATH)

    def _hook(tp, val, tb):
        sys.stderr.write("".join(traceback.format_exception(tp, val, tb)))

    sys.excepthook = _hook


def _log_version():
    """启动时把「本地版本 / 是否用了在线更新代码」写进日志，排错时一眼可见。"""
    try:
        import json
        import app_update as upd
        print("本地版本:", upd.local_version(), "（%s）" % upd.local_note())
        ins = upd.installed_dir()
        if ins:
            print("在线更新代码目录:", ins)
    except Exception as ex:
        print("读取版本信息失败:", type(ex).__name__, ex)


def _selftest():
    """不开窗口的自检：起服务 → 查块库 → 跑一次生成 → 打印结果。"""
    import json
    import threading
    import urllib.request

    import wiring_ui as m

    httpd, port = m.make_server(8799)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % port
    print("自检服务:", base)
    with urllib.request.urlopen(base + "/api/blocks", timeout=20) as r:
        d = json.loads(r.read().decode("utf-8"))
    print("块库 %d 个块 / 外框 %d 个模板" % (len(d.get("blocks") or []), len(d.get("frames") or [])))
    print("外框图目录:", m.FRAMES_DIR)
    print("输出目录  :", m.OUTDIR)
    body = {
        "frame": (d.get("frames") or [""])[0], "n_per": 20, "n_strings": 3,
        "gap_x": 1, "harness": ["CBX", "FUSE", "POS", "Male", "Male", "Male", "FUSE"],
        "module_first": "PV-POS", "module_mid": "MIDDLE-PV", "module_last": "END-NEG",
        "head_block": "CBX", "pos_feeder": "POS", "neg_feeder": "NEG",
        "pos_plug": "Male", "neg_plug": "Fmale", "awg_main": "2/0 AWG", "neg_head": "Male",
    }
    req = urllib.request.Request(base + "/api/generate_array",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        g = json.loads(r.read().decode("utf-8"))
    print("生成结果:", g.get("dxf_file"))
    print("日志末行:", (g.get("log") or [""])[-1])
    ok = bool(g.get("dxf_file"))
    print("自检:", "通过" if ok else "失败")
    return 0 if ok else 1


def _selftest_ui():
    """窗口模式自检：真开一次桌面窗口，加载界面，再关掉。

    验证的是"打包成 exe 后窗口还能不能起来、界面能不能加载"——
    这一步出问题（比如 WebView2 缺失）程序会退回浏览器，肉眼不容易发现。
    """
    import json
    import threading
    import time
    import urllib.request

    import wiring_ui as m

    httpd, port = m.make_server(8791)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % port
    print("窗口自检服务:", url)

    try:
        import webview
    except Exception as ex:
        print("窗口自检: 失败（没有 pywebview：%s）" % ex)
        return 1

    win = webview.create_window("自检窗口", url, width=900, height=600)
    result = {"ok": False, "msg": "没跑起来"}

    def probe():
        time.sleep(6)                     # 等页面和 API 注入完成
        try:
            body = json.dumps({"n_per": 4, "n_strings": 2, "gap_x": 1,
                               "frame": (m.list_frames() or [""])[0],
                               "harness": ["CBX", "FUSE", "POS", "Male", "FUSE"],
                               "pos_feeder": "POS", "neg_feeder": "NEG",
                               "module_first": "PV-POS", "module_mid": "MIDDLE-PV",
                               "module_last": "END-NEG", "head_block": "CBX",
                               "pos_plug": "Male", "neg_plug": "Fmale",
                               "awg_main": "2/0 AWG", "neg_head": "Male"}).encode()
            req = urllib.request.Request(url + "/api/generate_array", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                g = json.loads(r.read().decode("utf-8"))
            name = (g.get("dxf_file") or "").replace("\\", "/").split("/")[-1]
            print("  生成:", name or "（失败）")
            # 界面里那三个按钮打的就是这些接口 —— 逐个真调一遍
            for act in ("export", "reveal", "open"):
                try:
                    u = url + "/api/file/%s?name=%s" % (act, name)
                    with urllib.request.urlopen(u, timeout=30) as r:
                        d = json.loads(r.read().decode("utf-8"))
                    print("  %-7s -> ok=%s %s" % (act, d.get("ok"), str(d.get("msg"))[:70]))
                    if act == "export" and d.get("ok"):
                        result["saved"] = d.get("msg")
                except Exception as ex:
                    print("  %-7s -> 异常 %s" % (act, ex))
            title = win.evaluate_js("document.title")
            print("  页面标题:", title)
            result["ok"] = bool(name) and bool(title)
            result["msg"] = "页面 %s / 文件 %s" % (title, name)
        except Exception as ex:
            result["msg"] = "%s: %s" % (type(ex).__name__, ex)
            print("  自检出错:", result["msg"])
        finally:
            try:
                win.destroy()
            except Exception:
                pass

    threading.Thread(target=probe, daemon=True).start()
    webview.start()
    print("窗口自检:", "通过" if result["ok"] else "失败", "-", result.get("msg"))
    return 0 if result["ok"] else 1


def main():
    _setup_logging()
    _log_version()
    if "--selftest" in sys.argv:
        return _selftest()
    if "--selftest-ui" in sys.argv:
        return _selftest_ui()
    import wiring_ui
    wiring_ui.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
