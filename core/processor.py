# core/processor.py
"""图片处理核心（libvips）。

合并了原项目中三个工具的处理逻辑：
  * 压缩 / 缩放（DS_libvips.py、优化文件.py、压缩图片_2508.py、自适应压缩/高速.py）
  * 格式转换（png转换.py、PNG 批量转 JPG .py）
  * 高清放大（DS放大.py、放大.py）

统一采用“两阶段提交”：先写入同目录临时文件，全部成功后再由
:func:`commit_results` 原子替换目标文件，因此取消或崩溃都不会破坏原图。
"""

import os
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from typing import Optional, Tuple

# 进程池里每个进程若再开满 libvips 线程会严重超订（N 进程 × CPU 线程）。
# 默认给每进程留一半核数，主窗口在创建进程池前会按实际进程数再收窄。
os.environ.setdefault(
    "VIPS_CONCURRENCY", str(max(1, (os.cpu_count() or 4) // 2))
)
# 屏蔽 libpng 对 sRGB/iCCP 配置不一致的警告（旧脚本同样处理）
os.environ.setdefault("PNG_SKIP_sRGB_CHECK", "1")

import pyvips

# 批量处理时关闭 libvips 操作缓存：减少内存占用，并避免缓存持有源文件句柄
# 导致 Windows 上无法删除/替换源文件。
try:
    pyvips.cache_set_max(0)
except Exception:
    pass

from . import backup as backup_module
from .constants import (
    ADAPTIVE_BASE_QUALITY,
    ADAPTIVE_LARGE_PIXELS,
    ADAPTIVE_QUALITY_MAX,
    ADAPTIVE_QUALITY_MIN,
    ADAPTIVE_SMALL_PIXELS,
    FORMAT_TO_EXT,
    HEIC_EXTENSIONS,
    SAVEABLE_EXTENSIONS,
    TMP_MARK,
)
from .enums import OperationMode, OutputFormat
from .models import ImageResult, TaskSettings
from .utils import human_size, parse_color


# =====================================================================
# 图像读取
# =====================================================================
def _read_image(path: str) -> pyvips.Image:
    """读取图片，遇到占用/瞬时错误时重试三次。"""
    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            image = pyvips.Image.new_from_file(path, access="sequential")
            # 依据 EXIF 方向自动旋转（保存时会 strip 掉元数据，必须提前旋转）
            autorot = getattr(image, "autorot", None)
            if autorot is not None and image.get_typeof("orientation") != 0:
                try:
                    image = autorot()
                except Exception:
                    pass
            return image
        except Exception as exc:  # noqa: BLE001 - 需要覆盖 libvips 的各类错误
            last_error = exc
            if attempt < 2:
                time.sleep(0.25)
    raise last_error if last_error else RuntimeError("无法读取图片")


def _heic_to_temp(heic_path: str) -> str:
    """用 FFmpeg 把 HEIC 解码为临时 PNG（libvips 缺少 HEVC 解码器时的兜底）。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("无法解码 HEIC：当前 libvips 缺少 HEVC 支持，且系统中未找到 ffmpeg")

    fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="heic_")
    os.close(fd)
    try:
        os.remove(tmp_path)
    except OSError:
        pass

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", heic_path,
        "-frames:v", "1",
        tmp_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=120, creationflags=creation_flags
        )
    except subprocess.TimeoutExpired:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise RuntimeError("FFmpeg 解码 HEIC 超时（>120s）")

    if result.returncode != 0:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        detail = result.stderr.decode("utf-8", "ignore")[-300:].strip()
        raise RuntimeError(f"FFmpeg 解码 HEIC 失败: {detail}")

    if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
        raise RuntimeError("FFmpeg 未生成有效的 HEIC 解码结果")
    return tmp_path


def load_image(image_path: str) -> Tuple[pyvips.Image, Optional[str]]:
    """读取图片，返回 (图像, 需要清理的临时文件路径或 None)。"""
    src_ext = os.path.splitext(image_path)[1].lower()
    if src_ext in HEIC_EXTENSIONS:
        try:
            return _read_image(image_path), None
        except Exception:
            decoded = _heic_to_temp(image_path)
            try:
                return _read_image(decoded), decoded
            except Exception:
                try:
                    os.remove(decoded)
                except OSError:
                    pass
                raise
    return _read_image(image_path), None


def read_image_size(image_path: str) -> Optional[Tuple[int, int]]:
    """只读图片头获取尺寸（不解码像素），失败返回 None。

    供“跳过尺寸过小的图片”策略使用；HEIC 等无法直接读头时返回 None，
    不因此跳过文件，交给真正的处理流程去判断。
    """
    try:
        image = pyvips.Image.new_from_file(image_path, access="sequential")
        return int(image.width), int(image.height)
    except Exception:
        return None


# =====================================================================
# 尺寸与格式决策
# =====================================================================
def calculate_target_size(
    original_w: int, original_h: int, max_w: int, max_h: int
) -> Tuple[int, int]:
    """按“等比缩放、不放大”的规则计算目标尺寸；0 表示该方向不限制。"""
    max_w = int(max_w or 0)
    max_h = int(max_h or 0)
    if max_w <= 0 and max_h <= 0:
        return original_w, original_h

    ratios = []
    if max_w > 0 and original_w > 0:
        ratios.append(max_w / original_w)
    if max_h > 0 and original_h > 0:
        ratios.append(max_h / original_h)
    if not ratios:
        return original_w, original_h

    ratio = min(ratios)
    if ratio >= 1.0:
        return original_w, original_h
    return max(1, int(round(original_w * ratio))), max(1, int(round(original_h * ratio)))


def choose_output_format(
    image: pyvips.Image, src_ext: str, settings: TaskSettings
) -> OutputFormat:
    """结合用户设置与图片特征决定最终输出格式。"""
    if src_ext in HEIC_EXTENSIONS:
        return OutputFormat.JPEG

    requested = settings.resolve_output_format(src_ext)
    if requested is not OutputFormat.SMART:
        return requested

    # 智能选择：透明图/小图标用 PNG，其余用 WebP
    if image.hasalpha():
        return OutputFormat.PNG
    if image.width < 200 and image.height < 200:
        return OutputFormat.PNG
    return OutputFormat.WEBP


def _ext_for_format(src_ext: str, fmt: OutputFormat) -> str:
    if fmt is OutputFormat.KEEP:
        ext = src_ext
    else:
        ext = FORMAT_TO_EXT.get(fmt, src_ext)
    if ext not in SAVEABLE_EXTENSIONS:
        # libvips 无法直接写出该格式（如 BMP），退化为 PNG 以免失败
        ext = ".png"
    return ext


def resolve_target_path(image_path: str, settings: TaskSettings, out_ext: str) -> str:
    """计算输出文件路径。"""
    in_dir = settings.input_dir or os.path.dirname(image_path)
    root = settings.target_root(image_path)

    if root:
        if settings.preserve_structure:
            try:
                rel = os.path.relpath(image_path, in_dir)
            except ValueError:
                rel = os.path.basename(image_path)
            if rel.startswith(".."):
                rel = os.path.basename(image_path)
        else:
            rel = os.path.basename(image_path)
        return os.path.join(root, os.path.splitext(rel)[0] + out_ext)

    return os.path.join(
        os.path.dirname(image_path),
        os.path.splitext(os.path.basename(image_path))[0] + out_ext,
    )


def predict_target_path(image_path: str, settings: TaskSettings) -> str:
    """不读取图片内容时的预测目标路径；SMART 模式下无法预测，返回空串。

    供主窗口提前检测“同一输出名冲突”（例如 a.png 与 a.webp 都转 a.jpg）。
    """
    src_ext = os.path.splitext(image_path)[1].lower()
    fmt = settings.resolve_output_format(src_ext)
    if fmt is OutputFormat.SMART:
        return ""
    return resolve_target_path(image_path, settings, _ext_for_format(src_ext, fmt))


# =====================================================================
# 质量
# =====================================================================
def adaptive_quality(image: pyvips.Image, src_ext: str, base_quality: int) -> int:
    """自适应质量（移植自“自适应高速.py”）。

    依据源格式与像素数微调质量，结果限制在 [50, 90]，且不超过用户设定值。
    """
    base = max(1, min(100, int(base_quality)))
    fmt_factor = 0.95 if src_ext in (".jpg", ".jpeg") else 1.05 if src_ext == ".png" else 1.0

    pixels = image.width * image.height
    if pixels > ADAPTIVE_LARGE_PIXELS:
        res_factor = 0.85
    elif pixels < ADAPTIVE_SMALL_PIXELS:
        res_factor = 1.10
    else:
        res_factor = 1.0

    quality = base * fmt_factor * res_factor
    ceiling = min(base, ADAPTIVE_QUALITY_MAX)
    quality = max(ADAPTIVE_QUALITY_MIN, min(ceiling, int(round(quality))))
    return int(max(1, min(100, quality)))


def _quality_for(out_ext: str, image: pyvips.Image, src_ext: str,
                 settings: TaskSettings) -> int:
    """按输出格式取质量；JPEG/WebP 支持自适应。"""
    if out_ext in (".jpg", ".jpeg"):
        base = settings.jpeg_quality
    elif out_ext == ".webp":
        base = settings.webp_quality
    else:
        return 0

    if settings.mode is OperationMode.COMPRESS and settings.adaptive_quality:
        return adaptive_quality(image, src_ext, base)
    return int(max(1, min(100, base)))


# =====================================================================
# 保存
# =====================================================================
def _to_8bit(image: pyvips.Image) -> pyvips.Image:
    if image.format == "uchar":
        return image
    try:
        return image.scale()
    except Exception:
        return image.cast("uchar")


def save_image(image: pyvips.Image, tmp_path: str, out_ext: str,
               settings: TaskSettings, quality: int) -> None:
    """按输出扩展名写出图片到临时文件。"""
    strip = bool(settings.strip_metadata)

    if out_ext in (".jpg", ".jpeg"):
        if image.interpretation not in (
            pyvips.Interpretation.SRGB,
            pyvips.Interpretation.B_W,
        ):
            try:
                image = image.colourspace("srgb")
            except Exception:
                pass
        if image.hasalpha():
            image = image.flatten(background=parse_color(settings.background_color))
        if image.format != "uchar":
            image = _to_8bit(image)
        image.jpegsave(
            tmp_path,
            Q=int(max(1, min(100, quality or settings.jpeg_quality))),
            optimize_coding=True,
            strip=strip,
        )

    elif out_ext == ".png":
        image.pngsave(
            tmp_path,
            compression=int(max(0, min(9, settings.png_compression))),
            filter=pyvips.ForeignPngFilter.ALL,
            strip=strip,
        )

    elif out_ext == ".webp":
        image.webpsave(
            tmp_path,
            Q=int(max(1, min(100, quality or settings.webp_quality))),
            strip=strip,
        )

    else:
        # tif / gif 等：由 libvips 根据扩展名选择保存器
        image.write_to_file(tmp_path, strip=strip)


# =====================================================================
# 单张处理
# =====================================================================
def _result_failure(image_path: str, original_size: int, message: str,
                    elapsed_ms: float, new_format: str = "") -> ImageResult:
    return ImageResult(
        file_path=image_path,
        original_size=original_size,
        output_size=0,
        original_resolution=(0, 0),
        output_resolution=(0, 0),
        format_changed=False,
        new_format=new_format,
        success=False,
        error_message=message,
        elapsed_ms=elapsed_ms,
    )


def process_single_image(image_path: str, settings: TaskSettings,
                         target_override: Optional[str] = None) -> ImageResult:
    """处理单张图片，返回处理结果（此时文件仍写在临时路径）。

    target_override: 主窗口检测到输出名冲突时传入的修正目标路径。
    """
    started = time.perf_counter()
    original_size = 0
    src_ext = os.path.splitext(image_path)[1].lower()

    if not os.path.isfile(image_path):
        return _result_failure(
            image_path, 0, "文件不存在，可能已被移动或删除",
            (time.perf_counter() - started) * 1000, src_ext,
        )

    try:
        original_size = os.path.getsize(image_path)
    except OSError as exc:
        return _result_failure(
            image_path, 0, f"无法读取文件大小: {exc}",
            (time.perf_counter() - started) * 1000, src_ext,
        )

    decoded_temp: Optional[str] = None
    try:
        image, decoded_temp = load_image(image_path)
        original_w, original_h = image.width, image.height

        # ---------- 1. 输出格式 ----------
        output_format = choose_output_format(image, src_ext, settings)
        out_ext = _ext_for_format(src_ext, output_format)
        format_changed = out_ext != src_ext

        # ---------- 2. 尺寸 ----------
        target_w, target_h = original_w, original_h
        if settings.mode is OperationMode.COMPRESS:
            target_w, target_h = calculate_target_size(
                original_w, original_h, settings.max_width, settings.max_height
            )
        elif settings.mode is OperationMode.UPSCALE:
            factor = max(1.0, min(4.0, float(settings.upscale_factor)))
            target_w = max(1, int(round(original_w * factor)))
            target_h = max(1, int(round(original_h * factor)))

        needs_resize = (target_w, target_h) != (original_w, original_h)

        # ---------- 3. 目标路径 ----------
        target_path = target_override or resolve_target_path(image_path, settings, out_ext)

        same_path = os.path.normcase(os.path.abspath(target_path)) == os.path.normcase(
            os.path.abspath(image_path)
        )

        # 尺寸、格式都无变化且不要求重新编码 → 直接跳过
        if (
            settings.mode is OperationMode.COMPRESS
            and same_path
            and not format_changed
            and not needs_resize
            and not settings.reencode_always
        ):
            return ImageResult(
                file_path=image_path,
                original_size=original_size,
                output_size=original_size,
                original_resolution=(original_w, original_h),
                output_resolution=(original_w, original_h),
                format_changed=False,
                new_format=src_ext.lstrip("."),
                success=True,
                skipped=True,
                skip_reason="尺寸与格式均无需处理",
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )

        if (
            settings.mode is OperationMode.UPSCALE
            and not needs_resize
            and settings.sharpen_sigma <= 0
        ):
            return ImageResult(
                file_path=image_path,
                original_size=original_size,
                output_size=original_size,
                original_resolution=(original_w, original_h),
                output_resolution=(original_w, original_h),
                format_changed=False,
                new_format=src_ext.lstrip("."),
                success=True,
                skipped=True,
                skip_reason="放大倍数为 1 且未启用锐化",
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )

        # ---------- 4. 缩放 / 放大 ----------
        if needs_resize:
            ratio_x = target_w / original_w
            ratio_y = target_h / original_h
            if abs(ratio_x - ratio_y) < 1e-6:
                image = image.resize(ratio_x, kernel="lanczos3")
            else:
                image = image.resize(ratio_x, vscale=ratio_y, kernel="lanczos3")

        # 放大后可选锐化
        if settings.mode is OperationMode.UPSCALE and settings.sharpen_sigma > 0:
            image = image.sharpen(sigma=float(settings.sharpen_sigma))

        # ---------- 5. 质量 ----------
        quality = _quality_for(out_ext, image, src_ext, settings)

        # ---------- 6. 写入临时文件 ----------
        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
        tmp_path = f"{target_path}{TMP_MARK}{os.getpid()}_{uuid.uuid4().hex[:6]}{out_ext}"

        try:
            save_image(image, tmp_path, out_ext, settings, quality)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise

        produced_size = os.path.getsize(tmp_path)

        # 压缩后反而变大（同格式重编码时常见）→ 丢弃结果，保留原图
        if (
            settings.keep_original_if_larger
            and out_ext == src_ext
            and produced_size >= original_size
        ):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return ImageResult(
                file_path=image_path,
                original_size=original_size,
                output_size=original_size,
                original_resolution=(original_w, original_h),
                output_resolution=(original_w, original_h),
                format_changed=False,
                new_format=src_ext.lstrip("."),
                success=True,
                skipped=True,
                skip_reason=(
                    f"结果更大（{human_size(original_size)} → "
                    f"{human_size(produced_size)}），保留原图"
                ),
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )

        need_delete = bool(settings.delete_original and not same_path)

        return ImageResult(
            file_path=image_path,
            original_size=original_size,
            output_size=produced_size,
            original_resolution=(original_w, original_h),
            output_resolution=(target_w, target_h),
            format_changed=format_changed,
            new_format=out_ext.lstrip("."),
            success=True,
            need_delete=need_delete,
            tmp_path=tmp_path,
            target_path=target_path,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            quality_used=quality,
        )

    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - started) * 1000
        message = _friendly_error(exc)
        return _result_failure(image_path, original_size, message, elapsed_ms, src_ext)

    finally:
        if decoded_temp and os.path.exists(decoded_temp):
            try:
                os.remove(decoded_temp)
            except OSError:
                pass


def _friendly_error(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    lower = text.lower()
    if "access is denied" in lower or "拒绝访问" in text or "permission denied" in lower:
        return "文件被占用或没有写入权限，请关闭图片查看器等程序后重试"
    if "not a known image format" in lower or "unable to load" in lower:
        return f"无法识别或解码该图片: {text}"
    return text


# =====================================================================
# 提交阶段（删除原文件 + 原子替换）
# =====================================================================
def _remove_with_retry(path: str, attempts: int = 3) -> bool:
    for index in range(attempts):
        try:
            if not os.path.exists(path):
                return True
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            os.remove(path)
            return True
        except PermissionError:
            time.sleep(0.2 * (index + 1))
        except OSError:
            return False
    return False


def commit_results(results, log=None, backup: bool = False) -> Tuple[int, int]:
    """提交处理结果：按需删除/备份原文件，并把临时文件原子替换到目标路径。

    backup=True 时，在覆盖原图或删除源文件之前，先把原图移入回收站
    （网络盘/回收站不可用时复制到同目录 .originals）；备份失败则放弃该文件，
    避免原图在没有备份的情况下消失。

    返回 (成功数, 失败数)。失败时临时文件会保留，便于手动恢复。
    """
    committed = 0
    failed = 0

    for result in results:
        if not result.success or not result.tmp_path:
            continue

        tmp_path = result.tmp_path
        target_path = result.target_path
        source_path = result.file_path

        if not os.path.exists(tmp_path):
            failed += 1
            if log:
                log(f"⚠️ 临时文件已丢失: {os.path.basename(tmp_path)}")
            continue

        # 之前已提交过（目标与临时文件一致）→ 只清理临时文件
        try:
            if (
                os.path.exists(target_path)
                and os.path.getsize(target_path) == os.path.getsize(tmp_path)
                and abs(os.path.getmtime(target_path) - os.path.getmtime(tmp_path)) < 1
            ):
                os.remove(tmp_path)
                committed += 1
                continue
        except OSError:
            pass

        same_path = os.path.normcase(os.path.abspath(target_path)) == os.path.normcase(
            os.path.abspath(source_path)
        )

        # ---------- 备份原图 ----------
        if backup and (result.need_delete or same_path) and os.path.exists(source_path):
            if not backup_module.backup_original(source_path, log=log):
                failed += 1
                continue

        # ---------- 需要删除源文件（格式变化 / 输出到其他目录）----------
        if result.need_delete and os.path.exists(source_path):
            if not _remove_with_retry(source_path):
                failed += 1
                if log:
                    log(f"⚠️ {os.path.basename(source_path)}: 原文件删除失败，临时文件已保留")
                continue

        moved = False
        for attempt in range(3):
            try:
                if os.path.exists(target_path):
                    try:
                        os.remove(target_path)
                    except PermissionError:
                        try:
                            os.chmod(target_path, stat.S_IWRITE | stat.S_IREAD)
                        except OSError:
                            pass
                os.replace(tmp_path, target_path)
                moved = True
                break
            except PermissionError:
                time.sleep(0.2 * (attempt + 1))
            except OSError as exc:
                if log:
                    log(f"⚠️ 移动失败: {target_path} ({exc})")
                break

        if moved:
            committed += 1
        else:
            failed += 1
            if log:
                log(f"⚠️ {os.path.basename(result.file_path)}: 写入目标文件失败，临时文件已保留")

    if log:
        if committed:
            log(f"提交成功: {committed} 个文件")
        if failed:
            log(f"提交失败: {failed} 个文件，临时文件保留在原位置")

    return committed, failed
