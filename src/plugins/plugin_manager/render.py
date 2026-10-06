"""把插件列表 / 插件详情渲染成图片。

使用 Pillow 直接绘制，不依赖 htmlrender、浏览器或网络，因此单机也能稳定出图。
中文字体会自动在系统字体里查找（Noto Sans CJK / 思源黑体 / 文泉驿等），
也可以用 `PLUGIN_MANAGER__FONT_PATH` 指定。
"""

from __future__ import annotations

import math
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Sequence

from nonebot.log import logger
from PIL import Image, ImageColor, ImageDraw, ImageFont

from .config import get_config
from .registry import PluginInfo

__all__ = [
    "render_plugin_detail",
    "render_plugin_detail_text",
    "render_plugin_list",
    "render_plugin_text",
]

# 画面尺寸
WIDTH = 920
MARGIN = 36
HEADER_HEIGHT = 104
TABLE_HEADER_HEIGHT = 46
ROW_HEIGHT = 78
FOOTER_HEIGHT = 58
RADIUS = 20

CONTENT_WIDTH = WIDTH - MARGIN * 2
COL_STATUS_WIDTH = 96
COL_SOURCE_WIDTH = 110
COL_TEXT_X = MARGIN + COL_STATUS_WIDTH + 18
COL_TEXT_WIDTH = CONTENT_WIDTH - COL_STATUS_WIDTH - COL_SOURCE_WIDTH - 36

# 配色
CARD = "#ffffff"
SURFACE = "#f7f8fc"
HEADER_FROM = "#4f6ef7"
HEADER_TO = "#8a5cf6"
TEXT_MAIN = "#1f2430"
TEXT_SUB = "#6b7280"
TEXT_MUTED = "#9aa0ae"
LINE = "#e9ebf3"

STATUS_STYLES = {
    "enabled": ("#e6f7ee", "#0f9d58"),
    "unloaded": ("#eef0f4", "#6b7280"),
}
SOURCE_STYLES = {
    "local": ("#eaefff", "#4f6ef7"),
    "pip": ("#fff2e0", "#c47b00"),
    "builtin": ("#eef0f4", "#5c636a"),
}

FONT_FILES: dict[bool, tuple[str, ...]] = {
    False: (
        "/usr/share/fonts/google-noto-sans-cjk-fonts/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/adobe-source-han-sans-fonts/SourceHanSans-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/wqy-microhei-fonts/wqy-microhei.ttc",
        "/usr/share/fonts/wqy-zenhei-fonts/wqy-zenhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ),
    True: (
        "/usr/share/fonts/google-noto-sans-cjk-fonts/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/adobe-source-han-sans-fonts/SourceHanSans-Bold.ttc",
        "/usr/share/fonts/wqy-zenhei-fonts/wqy-zenhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
}

_MEASURE = ImageDraw.Draw(Image.new("RGB", (8, 8)))
_font_warned = False


def _rgb(color: str) -> tuple[int, int, int]:
    return ImageColor.getrgb(color)


def _text_width(text: str, font: ImageFont.FreeTypeFont) -> float:
    return _MEASURE.textlength(text, font=font)


@lru_cache(maxsize=64)
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """按需加载字体，找不到中文字体时回退到 Pillow 默认字体。"""

    global _font_warned

    config = get_config()
    custom = config.font_bold_path if bold else config.font_path
    candidates: list[str] = []
    if custom:
        candidates.append(custom)
    candidates.extend(FONT_FILES[bold])

    for path in candidates:
        font = _load_font_file(path, size)
        if font is not None:
            return font

    if not _font_warned:
        _font_warned = True
        logger.warning(
            "plugin_manager: 没有找到可用的中文字体，图片中的中文可能显示为方块；"
            "可通过 PLUGIN_MANAGER__FONT_PATH 指定字体文件"
        )
    return ImageFont.load_default(size=size)


def _load_font_file(path: str, size: int) -> ImageFont.FreeTypeFont | None:
    file = Path(path)
    if not file.is_file():
        return None

    fallback: ImageFont.FreeTypeFont | None = None
    # .ttc 字体集合中包含多个字面，优先挑选简体中文字面
    for index in range(12):
        try:
            font = ImageFont.truetype(str(file), size, index=index)
        except Exception:
            break
        if fallback is None:
            fallback = font
        name = " ".join(part for part in font.getname() if part)
        if "SC" in name or "Simplified" in name or "Micro Hei" in name:
            return font

    return fallback


def _ellipsize(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: float,
    suffix: str = "…",
    *,
    force: bool = False,
) -> str:
    """把文本裁剪到指定宽度内，必要时补省略号。"""

    if not force and _text_width(text, font) <= max_width:
        return text

    trimmed = text
    while trimmed and _text_width(trimmed + suffix, font) > max_width:
        trimmed = trimmed[:-1]
    return f"{trimmed}{suffix}" if trimmed else suffix


def _wrap_text(
    text: str, font: ImageFont.FreeTypeFont, max_width: float, max_lines: int = 4
) -> list[str]:
    """按字符宽度折行，超出 `max_lines` 时在最后一行截断。"""

    lines: list[str] = []
    for paragraph in (text or "").splitlines() or [""]:
        current = ""
        for char in paragraph:
            if _text_width(current + char, font) <= max_width:
                current += char
                continue
            lines.append(current)
            current = char
        lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _ellipsize(lines[-1], font, max_width, force=True)
    return lines


def _new_canvas(height: int) -> Image.Image:
    return Image.new("RGB", (WIDTH, height), _rgb(CARD))


def _draw_header(canvas: Image.Image, title: str, subtitle: str, right: str) -> None:
    band = Image.new("RGB", (WIDTH, HEADER_HEIGHT))
    band_draw = ImageDraw.Draw(band)
    start, end = _rgb(HEADER_FROM), _rgb(HEADER_TO)
    for y in range(HEADER_HEIGHT):
        ratio = y / max(HEADER_HEIGHT - 1, 1)
        band_draw.line(
            [(0, y), (WIDTH, y)],
            fill=tuple(
                round(begin + (finish - begin) * ratio)
                for begin, finish in zip(start, end)
            ),
        )
    canvas.paste(band, (0, 0))

    draw = ImageDraw.Draw(canvas)
    draw.text(
        (MARGIN, HEADER_HEIGHT / 2 - 15),
        _ellipsize(title, _font(32, bold=True), CONTENT_WIDTH - 240),
        font=_font(32, bold=True),
        fill="white",
        anchor="lm",
    )
    draw.text(
        (MARGIN, HEADER_HEIGHT / 2 + 21),
        _ellipsize(subtitle, _font(18), CONTENT_WIDTH - 160),
        font=_font(18),
        fill="#dfe4ff",
        anchor="lm",
    )
    if right:
        draw.text(
            (WIDTH - MARGIN, HEADER_HEIGHT / 2),
            right,
            font=_font(17),
            fill="#e7eaff",
            anchor="rm",
        )


def _draw_pill(
    draw: ImageDraw.ImageDraw,
    x: float,
    center_y: float,
    text: str,
    font: ImageFont.FreeTypeFont,
    background: str,
    foreground: str,
    anchor: str = "lm",
) -> float:
    """画一个圆角标签，返回标签宽度。"""

    padding_x, padding_y = 15, 8
    width = _text_width(text, font)
    bbox = font.getbbox(text)
    box_width = width + padding_x * 2
    box_height = (bbox[3] - bbox[1]) + padding_y * 2

    if anchor == "rm":
        left = x - box_width
    elif anchor == "mm":
        left = x - box_width / 2
    else:
        left = x

    top = center_y - box_height / 2
    draw.rounded_rectangle(
        [left, top, left + box_width, top + box_height],
        radius=box_height / 2,
        fill=background,
    )
    draw.text((left + padding_x, center_y), text, font=font, fill=foreground, anchor="lm")
    return box_width


def _save_png(canvas: Image.Image) -> bytes:
    """把画布裁成圆角并输出 PNG 字节。"""

    width, height = canvas.size
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, width - 1, height - 1], radius=RADIUS, fill=255
    )
    rounded = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    rounded.paste(canvas, (0, 0), mask)
    ImageDraw.Draw(rounded).rounded_rectangle(
        [0, 0, width - 1, height - 1],
        radius=RADIUS,
        outline=(*_rgb("#e4e7f0"), 255),
        width=1,
    )

    buffer = BytesIO()
    rounded.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _compose_pages(
    plugins: Sequence[PluginInfo], page: int, pagesize: int
) -> tuple[list[PluginInfo], int, int]:
    total_pages = max(1, math.ceil(len(plugins) / pagesize))
    page = min(max(page, 1), total_pages)
    rows = list(plugins[(page - 1) * pagesize : page * pagesize])
    return rows, page, total_pages


def _plugin_description(info: PluginInfo) -> str:
    if info.description:
        return info.description
    if not info.loaded:
        return "尚未加载的插件"
    return "该插件未提供简介"


def render_plugin_list(
    plugins: Sequence[PluginInfo], page: int = 1, pagesize: int | None = None
) -> bytes:
    """渲染插件列表图片。"""

    config = get_config()
    pagesize = max(1, pagesize or config.pagesize)
    rows, page, total_pages = _compose_pages(plugins, page, pagesize)

    total = len(plugins)
    loaded = sum(1 for info in plugins if info.loaded)
    unloaded = total - loaded

    height = (
        HEADER_HEIGHT
        + TABLE_HEADER_HEIGHT
        + ROW_HEIGHT * max(len(rows), 1)
        + FOOTER_HEIGHT
    )
    canvas = _new_canvas(height)
    _draw_header(
        canvas,
        "插件管理",
        f"共 {total} 个插件 · 已加载 {loaded} · 未加载 {unloaded}",
        f"第 {page}/{total_pages} 页",
    )
    draw = ImageDraw.Draw(canvas)

    # 表头
    draw.rectangle([0, HEADER_HEIGHT, WIDTH, HEADER_HEIGHT + TABLE_HEADER_HEIGHT], fill=SURFACE)
    head_center = HEADER_HEIGHT + TABLE_HEADER_HEIGHT / 2
    head_font = _font(16, bold=True)
    draw.text((MARGIN, head_center), "状态", font=head_font, fill=TEXT_MUTED, anchor="lm")
    draw.text((COL_TEXT_X, head_center), "插件", font=head_font, fill=TEXT_MUTED, anchor="lm")
    draw.text(
        (WIDTH - MARGIN, head_center), "来源", font=head_font, fill=TEXT_MUTED, anchor="rm"
    )

    if rows:
        for index, info in enumerate(rows):
            _draw_plugin_row(canvas, info, HEADER_HEIGHT + TABLE_HEADER_HEIGHT + index * ROW_HEIGHT)
    else:
        draw.text(
            (WIDTH / 2, HEADER_HEIGHT + TABLE_HEADER_HEIGHT + ROW_HEIGHT / 2),
            "还没有安装任何插件",
            font=_font(20),
            fill=TEXT_MUTED,
            anchor="mm",
        )

    _draw_footer(
        canvas,
        height,
        f"第 {page}/{total_pages} 页 · 每页 {pagesize} 个 · 更新于 {datetime.now():%H:%M}",
        "详情 /插件信息 <名称>",
    )
    return _save_png(canvas)


def _draw_plugin_row(canvas: Image.Image, info: PluginInfo, top: float) -> None:
    draw = ImageDraw.Draw(canvas)
    bottom = top + ROW_HEIGHT

    draw.line([(MARGIN, bottom), (WIDTH - MARGIN, bottom)], fill=LINE)

    center_y = top + ROW_HEIGHT / 2

    # 状态标签
    status_key = "enabled" if info.loaded else "unloaded"
    status_bg, status_fg = STATUS_STYLES[status_key]
    _draw_pill(
        draw,
        MARGIN,
        center_y,
        info.status_label,
        _font(15, bold=True),
        status_bg,
        status_fg,
    )

    # 插件名称 + 版本
    name_font = _font(21, bold=True)
    name = _ellipsize(info.id, name_font, COL_TEXT_WIDTH - 96)
    draw.text(
        (COL_TEXT_X, top + 27),
        name,
        font=name_font,
        fill=TEXT_MAIN,
        anchor="lm",
    )
    if info.version:
        version_x = COL_TEXT_X + _text_width(name, name_font) + 10
        draw.text(
            (version_x, top + 29),
            f"v{info.version}",
            font=_font(15),
            fill=TEXT_MUTED,
            anchor="lm",
        )

    # 简介
    draw.text(
        (COL_TEXT_X, top + 56),
        _ellipsize(_plugin_description(info), _font(16), COL_TEXT_WIDTH),
        font=_font(16),
        fill=TEXT_SUB,
        anchor="lm",
    )

    # 来源标签
    source_bg, source_fg = SOURCE_STYLES.get(info.source, SOURCE_STYLES["builtin"])
    _draw_pill(
        draw,
        WIDTH - MARGIN,
        center_y,
        info.source_label,
        _font(15),
        source_bg,
        source_fg,
        anchor="rm",
    )


def _draw_footer(canvas: Image.Image, height: int, left: str, right: str) -> None:
    draw = ImageDraw.Draw(canvas)
    top = height - FOOTER_HEIGHT
    draw.rectangle([0, top, WIDTH, height], fill=SURFACE)
    draw.line([(0, top), (WIDTH, top)], fill=LINE)
    center_y = top + FOOTER_HEIGHT / 2
    draw.text((MARGIN, center_y), left, font=_font(15), fill=TEXT_MUTED, anchor="lm")
    draw.text(
        (WIDTH - MARGIN, center_y), right, font=_font(15), fill=TEXT_MUTED, anchor="rm"
    )


def _section_height(lines: int) -> float:
    return 44 + lines * 28 + 12


def _draw_section(
    draw: ImageDraw.ImageDraw, top: float, title: str, lines: Sequence[str]
) -> float:
    draw.rounded_rectangle(
        [MARGIN, top + 4, MARGIN + 5, top + 24], radius=2.5, fill=_rgb(HEADER_FROM)
    )
    draw.text(
        (MARGIN + 16, top + 14),
        title,
        font=_font(19, bold=True),
        fill=TEXT_MAIN,
        anchor="lm",
    )

    text_y = top + 44
    for line in lines:
        draw.text((MARGIN, text_y), line, font=_font(17), fill=TEXT_SUB, anchor="lm")
        text_y += 28
    return text_y + 12


def render_plugin_detail(info: PluginInfo) -> bytes:
    """渲染单个插件的信息卡片。"""

    facts: list[tuple[str, str]] = [
        ("状态", info.status_label),
        ("来源", info.source_label),
        ("版本", f"v{info.version}" if info.version else "未知"),
        ("事件响应器", f"{info.matcher_count} 个" if info.loaded else "未加载"),
    ]
    if info.category:
        facts.append(("插件类型", info.category))
    if info.module_name:
        facts.append(("模块名", info.module_name))
    if info.homepage:
        facts.append(("主页", info.homepage))

    desc_lines = _wrap_text(
        info.description or "该插件未提供插件简介", _font(17), CONTENT_WIDTH, 4
    )
    usage_lines = _wrap_text(
        info.usage or "该插件未提供使用说明", _font(17), CONTENT_WIDTH, 9
    )

    grid_rows = math.ceil(len(facts) / 2)
    height = (
        HEADER_HEIGHT
        + 24
        + grid_rows * 58
        + 14
        + _section_height(len(desc_lines))
        + _section_height(len(usage_lines))
        + FOOTER_HEIGHT
    )

    canvas = _new_canvas(height)
    _draw_header(
        canvas,
        info.id,
        f"{info.source_label}插件 · {info.module_name or info.id}",
        "插件详情",
    )
    draw = ImageDraw.Draw(canvas)

    top = HEADER_HEIGHT + 24
    cell_width = CONTENT_WIDTH / 2
    for index, (label, value) in enumerate(facts):
        column, row = index % 2, index // 2
        x = MARGIN + column * cell_width
        cell_top = top + row * 58
        draw.text((x, cell_top), label, font=_font(15), fill=TEXT_MUTED, anchor="lm")
        draw.text(
            (x, cell_top + 25),
            _ellipsize(value, _font(18), cell_width - 28),
            font=_font(18),
            fill=TEXT_MAIN,
            anchor="lm",
        )

    cursor = top + grid_rows * 58 + 14
    cursor = _draw_section(draw, cursor, "插件简介", desc_lines)
    _draw_section(draw, cursor, "使用说明", usage_lines)

    _draw_footer(
        canvas,
        height,
        f"插件标识符：{info.id}",
        "",
    )
    return _save_png(canvas)


def render_plugin_text(
    plugins: Sequence[PluginInfo], page: int = 1, pagesize: int | None = None
) -> str:
    """纯文本版插件列表（适配器不支持图片时使用）。"""

    config = get_config()
    pagesize = max(1, pagesize or config.pagesize)
    rows, page, total_pages = _compose_pages(plugins, page, pagesize)

    loaded = sum(1 for info in plugins if info.loaded)
    unloaded = len(plugins) - loaded

    lines = [
        f"📦 插件列表（{page}/{total_pages} 页）",
        f"共 {len(plugins)} 个 · 已加载 {loaded} · 未加载 {unloaded}",
        "",
    ]
    if not rows:
        lines.append("还没有安装任何插件")
    for info in rows:
        mark = "✅" if info.loaded else "💤"
        version = f" v{info.version}" if info.version else ""
        lines.append(f"{mark} {info.id}{version} [{info.source_label}]")
        lines.append(f"    {_plugin_description(info)}")

    lines.append("")
    lines.append("详情 /插件信息 <名称>")
    if total_pages > 1:
        lines.append("翻页：/插件列表 <页码>")
    return "\n".join(lines)


def render_plugin_detail_text(info: PluginInfo) -> str:
    """纯文本版插件详情。"""

    lines = [
        f"📦 {info.id}",
        f"状态：{info.status_label}",
        f"来源：{info.source_label}",
    ]
    if info.version:
        lines.append(f"版本：v{info.version}")
    if info.loaded:
        lines.append(f"事件响应器：{info.matcher_count} 个")
    if info.category:
        lines.append(f"插件类型：{info.category}")
    if info.module_name:
        lines.append(f"模块名：{info.module_name}")
    if info.homepage:
        lines.append(f"主页：{info.homepage}")
    lines.append("")
    lines.append(f"简介：{info.description or '该插件未提供插件简介'}")
    lines.append(f"使用：{info.usage or '该插件未提供使用说明'}")
    return "\n".join(lines)
