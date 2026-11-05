"""图片工具箱（libvips）入口。

合并自 D:\\Python\\图片 下的多个脚本：
    压缩 / 缩放  —— DS_libvips.py、优化文件.py、压缩图片_2508.py、自适应压缩.py、自适应高速.py
    格式转换    —— png转换.py、PNG 批量转 JPG .py、deepseek_图片.py（PNG→JPG）
    高清放大    —— DS放大.py、放大.py
"""

import multiprocessing
import tkinter as tk


def _enable_dpi_awareness() -> None:
    """Windows 高分屏下避免界面模糊。"""
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main() -> None:
    multiprocessing.freeze_support()
    _enable_dpi_awareness()

    try:
        from gui.app import ImageToolApp
    except Exception as exc:  # 依赖缺失时给出可读提示（pythonw 下没有控制台）
        root = tk.Tk()
        root.withdraw()
        from tkinter import messagebox

        messagebox.showerror(
            "启动失败",
            f"无法加载程序依赖：\n{exc}\n\n"
            "请确认已安装 pyvips（本目录 .venv 已自带），"
            "或在命令行运行 .venv\\Scripts\\python.exe main.py 查看详细错误。",
        )
        root.destroy()
        return

    root = tk.Tk()
    ImageToolApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
