## 图片工具箱 v2.3.0

基于 libvips 的批量图片处理工具（Windows / Python 3.12 / Tkinter）。
把原本分散的压缩、格式转换、放大脚本合并为一个图形程序。

### 三种模式

- **压缩 / 缩放** —— 按最大宽高等比缩放（不放大），JPEG / PNG / WebP 分格式质量，可选自适应质量
- **格式转换** —— JPEG / PNG / WebP 互转，透明区域自定义背景色
- **高清放大** —— 1.0~4.0 倍 Lanczos3 放大，可选锐化强度

### 四套内置预设

| 预设 | 参数 | 用途 |
|---|---|---|
| 归档 4K | 3840×2160 / JPEG / Q85 | 长期保存，兼容性最好 |
| 分享 2K | 2560×1440 / JPEG / Q85 / 自适应 | 清晰度与体积平衡 |
| 社交 1080p | 1920×1080 / JPEG / Q85 / 自适应 | 聊天、社交 |
| 省空间 4K（SMART） | 3840×2160 / 智能选择 | 网盘、网页，体积最小 |

### 主要特性

- **安全写入**：同目录临时文件 + 原子替换；覆盖前可将原图移入回收站（可还原），网络盘退化为 `.originals` 副本
- **批量能力**：进程池并行（自动控制 libvips 线程数）、按张数/大小分批、暂停/取消、JSON 统计报告
- **断点缓存**：未变化的文件自动跳过，二次运行仅约 3 ms/张
- **格式支持**：JPEG、PNG、WebP、BMP、TIFF、GIF、HEIC/HEIF（pillow-heif 完整解码）
- **EXIF 方向自动纠正**，可选清除 EXIF/ICC 元数据
- **输出名冲突自动改名**，避免不同格式同源文件互相覆盖

### 实测数据

- 362 张 JPEG / 3.24 GB，默认智能选择：**73.1 s** 完成，输出 308 MB（降幅 **90.7%**），0 失败、0 临时文件残留，峰值内存约 697 MB
- 12 张真实图片（含 3 张 HEIC）：12/12 成功，6.13 MB（降幅 88.8%），二次运行缓存命中 0.15 s

### 环境与安装

- Windows 10/11，Python 3.10+
- `pip install -r requirements.txt`（pyvips、pillow-heif）
- 需要本机安装 [libvips](https://github.com/libvips/libvips/releases) 并将 `bin` 加入 PATH
- 启动：双击 `启动.bat`，或 `python main.py`

### 说明

- 输入/输出文件夹不会被记忆，每次启动需重新选择
- 界面与文档为中文
- 许可证：MIT

完整说明见 [README.md](https://github.com/Ray920501/image-compressor/blob/main/README.md)，
测试报告见 [docs/](https://github.com/Ray920501/image-compressor/tree/main/docs)。
