"""端到端冒烟测试：核心处理 + GUI 三种模式。

运行方式：
    .venv\\Scripts\\python.exe tests\\smoke_test.py

测试会在系统临时目录创建图片，跑完后自动删除；
GUI 会被创建但不进入 mainloop（测试里手动泵事件），并在结束时销毁。
"""

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyvips


def make_images(root: str) -> None:
    os.makedirs(root, exist_ok=True)
    big = pyvips.Image.black(2400, 1600).new_from_image([40, 120, 200]).copy(
        interpretation="srgb")
    big.jpegsave(os.path.join(root, "big.jpg"), Q=92)

    small = pyvips.Image.black(900, 700).new_from_image([200, 90, 40]).copy(
        interpretation="srgb")
    small.bandjoin(150).pngsave(os.path.join(root, "alpha.png"))

    big.webpsave(os.path.join(root, "big.webp"), Q=90)

    sub = os.path.join(root, "sub")
    os.makedirs(sub, exist_ok=True)
    small.jpegsave(os.path.join(sub, "nested.jpg"), Q=90)


def run_gui() -> None:
    import tkinter as tk
    from tkinter import messagebox

    from gui.app import ImageToolApp

    # 避免测试被弹窗阻塞
    messagebox.askyesno = lambda *a, **k: True
    messagebox.showinfo = lambda *a, **k: None
    messagebox.showwarning = lambda *a, **k: None
    messagebox.showerror = lambda *a, **k: print("showerror:", a)

    work = tempfile.mkdtemp(prefix="imgtool_gui_")
    src = os.path.join(work, "src")
    make_images(src)

    root = tk.Tk()
    root.withdraw()  # 不显示窗口，仅创建控件
    app = ImageToolApp(root)
    root.update()

    failures = []

    def pump(label: str, timeout: float = 120.0) -> None:
        deadline = time.time() + timeout
        while app.is_processing and time.time() < deadline:
            root.update()
            time.sleep(0.03)
        root.update()
        if app.is_processing:
            failures.append(f"{label}: 超时未结束")

    def start_and_check(label: str, expected_output: str) -> None:
        app._start()
        root.update()
        pump(label)
        if not os.path.isdir(expected_output):
            failures.append(f"{label}: 输出目录不存在 {expected_output}")
            return
        produced = []
        for current_root, _dirs, names in os.walk(expected_output):
            for name in names:
                if not name.lower().endswith((".tmp",)) and ".tmp" not in name:
                    produced.append(os.path.join(current_root, name))
        print(f"[{label}] 输出 {len(produced)} 个文件，日志 {len(app.skipped_details)} 条跳过")
        if not produced:
            failures.append(f"{label}: 没有输出文件")

    app.input_dir_var.set(src)
    app.recursive_var.set(True)
    app.use_cache_var.set(False)
    app.batch_count_var.set(2)

    # ---- 压缩 ----
    out_compress = os.path.join(work, "out_compress")
    app.output_dir_var.set(out_compress)
    app.preserve_var.set(True)
    app.notebook.select(app.compress_panel)
    app.compress_panel.width_var.set(1920)
    app.compress_panel.height_var.set(1080)
    app.compress_panel.format_var.set("keep")
    app.compress_panel.adaptive_var.set(True)
    start_and_check("压缩", out_compress)

    # ---- 格式转换 PNG -> JPG ----
    out_convert = os.path.join(work, "out_convert")
    app.output_dir_var.set(out_convert)
    app.notebook.select(app.convert_panel)
    app.convert_panel.format_var.set("jpeg")
    app.convert_panel.bg_var.set("#FFFFFF")
    start_and_check("转换", out_convert)

    # ---- 高清放大 ----
    out_upscale = os.path.join(work, "out_upscale")
    app.output_dir_var.set(out_upscale)
    app.notebook.select(app.upscale_panel)
    app.upscale_panel.factor_var.set(1.5)
    app.upscale_panel.sharpen_var.set(0.8)
    start_and_check("放大", out_upscale)

    root.destroy()

    # 校验输出尺寸
    sample = os.path.join(out_compress, "big.jpg")
    if os.path.exists(sample):
        size = pyvips.Image.new_from_file(sample).width
        print("[压缩] big.jpg 宽度 =", size)
        if size > 1920:
            failures.append("压缩未生效：宽度超过 1920")
    sample = os.path.join(out_upscale, "big.jpg")
    if os.path.exists(sample):
        image = pyvips.Image.new_from_file(sample)
        print("[放大] big.jpg =", image.width, "x", image.height)
        if image.width != 3600:
            failures.append(f"放大尺寸错误：{image.width} != 3600")

    shutil.rmtree(work, ignore_errors=True)

    if failures:
        print("\n失败项:")
        for item in failures:
            print(" -", item)
        raise SystemExit(1)
    print("\nGUI 冒烟测试通过")


def main() -> None:
    run_gui()


if __name__ == "__main__":
    main()
