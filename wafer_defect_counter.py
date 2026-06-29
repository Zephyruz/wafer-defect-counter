"""
晶圆缺陷自动计数程序。

默认使用“人工四点校准”：依次点击上中、右中、下中、左中四颗参考芯片的中心，
程序会根据固定布局做透视校正，再按照固定 401 个芯片位置生成网格。

用法：
    python wafer_defect_counter.py input.jpg
    python wafer_defect_counter.py input.jpg --points calibration.json

第一版只使用传统图像处理：OpenCV、NumPy、Matplotlib。
不使用深度学习、YOLO、TensorFlow 或 PyTorch。
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import matplotlib.pyplot as plt
import numpy as np

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - Pillow is optional but recommended for Chinese labels.
    Image = None
    ImageDraw = None
    ImageFont = None


ROW_COUNTS = [5, 11, 13, 15, 17, 19, 21, 21, 21, 23, 23, 23, 23, 23, 21, 21, 21, 19, 17, 15, 13, 11, 5]
TOTAL_EXPECTED_CHIPS = sum(ROW_COUNTS)
REFERENCE_POINT_NAMES = ["上方中间芯片中心", "右侧中间芯片中心", "下方中间芯片中心", "左侧中间芯片中心"]


@dataclass
class Config:
    """晶圆计数相关阈值和几何参数。"""

    # Blue tape detection
    blue_hue_low: int = 85
    blue_hue_high: int = 140
    blue_sat_min: int = 45
    blue_value_min: int = 35

    # Grid geometry
    cell_width_fraction: float = 0.82
    cell_height_fraction: float = 0.78
    inner_cell_scale: float = 0.68

    # Marker detection
    dark_value_max: int = 70
    dark_saturation_max: int = 210
    marker_min_area_fraction: float = 0.018
    marker_min_pixels: int = 18
    marker_morph_kernel: int = 3

    # Missing-chip / blue-tape exposure detection
    blue_missing_fraction: float = 0.18
    low_texture_std_max: float = 18.0
    missing_value_min: int = 35

    # Visualization
    normal_color: tuple[int, int, int] = (40, 210, 40)
    defect_color: tuple[int, int, int] = (30, 30, 230)
    grid_color: tuple[int, int, int] = (255, 180, 0)
    line_thickness: int = 2
    font_path: str = r"C:\Windows\Fonts\msyh.ttc"


@dataclass
class WaferGeometry:
    center: np.ndarray
    width: float
    height: float
    angle_deg: float


@dataclass
class ChipCell:
    index: int
    row: int
    col: int
    center: np.ndarray
    width: float
    height: float
    angle_deg: float


@dataclass
class ChipResult:
    cell: ChipCell
    defective: bool
    marker_defect: bool
    missing_defect: bool
    marker_fraction: float
    blue_fraction: float
    texture_std: float


def load_image(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"无法读取图片：{path}")
    return image


def load_chinese_font(cfg: Config, size: int):
    if ImageFont is None:
        return None

    candidates = [
        cfg.font_path,
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return None


def draw_text(
    image: np.ndarray,
    text: str,
    origin: tuple[int, int],
    cfg: Config,
    size: int = 24,
    color: tuple[int, int, int] = (255, 255, 255),
) -> None:
    """Draw Chinese text when Pillow/font is available; otherwise fall back to OpenCV."""
    if Image is None or ImageDraw is None:
        draw_label(image, text, origin, scale=max(0.45, size / 36), color=color)
        return

    font = load_chinese_font(cfg, size)
    if font is None:
        draw_label(image, text, origin, scale=max(0.45, size / 36), color=color)
        return

    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil_image)
    x, y = origin
    bgr = color
    rgb_color = (bgr[2], bgr[1], bgr[0])
    draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0))
    draw.text((x, y), text, font=font, fill=rgb_color)
    image[:] = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)


def collect_manual_points(image: np.ndarray, cfg: Config) -> np.ndarray:
    display = image.copy()
    points: list[tuple[int, int]] = []
    names = REFERENCE_POINT_NAMES
    window = "人工校准：依次点击 上中、右中、下中、左中 芯片中心；R重选，Enter确认，Esc退出"

    max_side = 1200
    scale = min(1.0, max_side / max(image.shape[:2]))
    if scale < 1.0:
        display_base = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        display_base = image.copy()

    def redraw() -> None:
        nonlocal display
        display = display_base.copy()
        for i, pt in enumerate(points):
            shown = (int(pt[0] * scale), int(pt[1] * scale))
            cv2.circle(display, shown, 6, (0, 0, 255), -1, cv2.LINE_AA)
            cv2.putText(display, str(i + 1), (shown[0] + 8, shown[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        if len(points) >= 2:
            shown_points = np.array([(int(x * scale), int(y * scale)) for x, y in points], dtype=np.int32)
            cv2.polylines(display, [shown_points], len(points) == 4, (0, 255, 255), 2, cv2.LINE_AA)
        next_text = "完成后按 Enter 确认" if len(points) == 4 else f"请点击：{names[len(points)]}"
        draw_text(display, next_text, (20, 18), cfg, size=26, color=(0, 255, 255))

    def on_mouse(event, x, y, _flags, _param) -> None:
        if event != cv2.EVENT_LBUTTONDOWN or len(points) >= 4:
            return
        points.append((int(x / scale), int(y / scale)))
        redraw()

    print("请在弹出的图片窗口中依次点击 4 颗参考芯片的中心：")
    print("1. 上方中间芯片中心：第 1 行 5 颗里面最中间那颗")
    print("2. 右侧中间芯片中心：第 12 行最右边那颗")
    print("3. 下方中间芯片中心：第 23 行 5 颗里面最中间那颗")
    print("4. 左侧中间芯片中心：第 12 行最左边那颗")
    print("点错可以按 R 重新选择；四个点选完后按 Enter 确认；按 Esc 退出。")
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window, on_mouse)
    redraw()

    while True:
        cv2.imshow(window, display)
        key = cv2.waitKey(30) & 0xFF
        if key in (13, 10) and len(points) == 4:
            break
        if key in (27,):
            cv2.destroyWindow(window)
            raise RuntimeError("人工校准已取消。")
        if key in (ord("r"), ord("R")):
            points.clear()
            redraw()

    cv2.destroyWindow(window)
    return np.array(points, dtype=np.float32)


def load_points(path: Path) -> np.ndarray:
    data = json.loads(path.read_text(encoding="utf-8"))
    points = np.array(data["points"], dtype=np.float32)
    if points.shape != (4, 2):
        raise ValueError("校准点文件格式错误：需要 4 个参考芯片中心点，每个点包含 x、y。")
    return points


def save_points(path: Path, points: np.ndarray) -> None:
    data = {
        "说明": "四个点顺序为：上方中间芯片中心、右侧中间芯片中心、下方中间芯片中心、左侧中间芯片中心。",
        "points": [[float(x), float(y)] for x, y in points],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def perspective_from_points(image: np.ndarray, points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, WaferGeometry]:
    top, right, bottom, left = points.astype(np.float32)
    pitch_x = np.linalg.norm(right - left) / (max(ROW_COUNTS) - 1)
    pitch_y = np.linalg.norm(bottom - top) / (len(ROW_COUNTS) - 1)
    width = int(round(pitch_x * max(ROW_COUNTS)))
    height = int(round(pitch_y * len(ROW_COUNTS)))
    if width < 100 or height < 100:
        raise RuntimeError("四点校准范围太小，请重新点击上中、右中、下中、左中四颗参考芯片中心。")

    dst = np.array(
        [
            [width / 2.0, height / 2.0 - 11.0 * pitch_y],
            [width / 2.0 + 11.0 * pitch_x, height / 2.0],
            [width / 2.0, height / 2.0 + 11.0 * pitch_y],
            [width / 2.0 - 11.0 * pitch_x, height / 2.0],
        ],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(points.astype(np.float32), dst)
    inverse = cv2.getPerspectiveTransform(dst, points.astype(np.float32))
    warped = cv2.warpPerspective(image, matrix, (width, height), flags=cv2.INTER_LINEAR)
    geometry = WaferGeometry(
        center=np.array([width / 2.0, height / 2.0], dtype=np.float32),
        width=float(width),
        height=float(height),
        angle_deg=0.0,
    )
    return warped, matrix, inverse, geometry


def rotate_points(points: np.ndarray, angle_deg: float) -> np.ndarray:
    theta = math.radians(angle_deg)
    rot = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]], dtype=np.float32)
    return points @ rot.T


def generate_chip_cells(geometry: WaferGeometry, cfg: Config) -> list[ChipCell]:
    rows = len(ROW_COUNTS)
    max_cols = max(ROW_COUNTS)
    pitch_x = geometry.width / max_cols
    pitch_y = geometry.height / rows
    cell_w = pitch_x * cfg.cell_width_fraction
    cell_h = pitch_y * cfg.cell_height_fraction

    cells: list[ChipCell] = []
    idx = 1
    for r, cols in enumerate(ROW_COUNTS):
        y = (r - (rows - 1) / 2.0) * pitch_y
        for c in range(cols):
            x = (c - (cols - 1) / 2.0) * pitch_x
            offset = rotate_points(np.array([[x, y]], dtype=np.float32), geometry.angle_deg)[0]
            cells.append(
                ChipCell(
                    index=idx,
                    row=r + 1,
                    col=c + 1,
                    center=geometry.center + offset,
                    width=cell_w,
                    height=cell_h,
                    angle_deg=geometry.angle_deg,
                )
            )
            idx += 1

    return cells


def cell_polygon(cell: ChipCell, scale: float = 1.0) -> np.ndarray:
    rect = (tuple(cell.center), (cell.width * scale, cell.height * scale), cell.angle_deg)
    return cv2.boxPoints(rect).astype(np.int32)


def transform_polygon(points: np.ndarray, matrix: np.ndarray | None) -> np.ndarray:
    if matrix is None:
        return points.astype(np.int32)
    shaped = points.astype(np.float32).reshape(-1, 1, 2)
    transformed = cv2.perspectiveTransform(shaped, matrix).reshape(-1, 2)
    return np.round(transformed).astype(np.int32)


def transform_point(point: np.ndarray, matrix: np.ndarray | None) -> tuple[int, int]:
    if matrix is None:
        return tuple(np.round(point).astype(int))
    shaped = point.astype(np.float32).reshape(1, 1, 2)
    transformed = cv2.perspectiveTransform(shaped, matrix).reshape(2)
    return tuple(np.round(transformed).astype(int))


def cell_mask(shape: tuple[int, int], cell: ChipCell, scale: float) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.uint8)
    cv2.fillConvexPoly(mask, cell_polygon(cell, scale), 255)
    return mask


def largest_blob_fraction(binary: np.ndarray, sample_mask: np.ndarray) -> tuple[float, int]:
    masked = cv2.bitwise_and(binary, sample_mask)
    sample_area = max(1, int(cv2.countNonZero(sample_mask)))
    components, labels, stats, _ = cv2.connectedComponentsWithStats(masked, connectivity=8)
    if components <= 1:
        return 0.0, 0
    largest = int(np.max(stats[1:, cv2.CC_STAT_AREA]))
    return largest / sample_area, largest


def evaluate_chip(image: np.ndarray, cell: ChipCell, cfg: Config) -> ChipResult:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    sample = cell_mask(gray.shape, cell, cfg.inner_cell_scale)

    blue = (
        (h >= cfg.blue_hue_low)
        & (h <= cfg.blue_hue_high)
        & (s >= cfg.blue_sat_min)
        & (v >= cfg.blue_value_min)
    ).astype(np.uint8) * 255

    dark = ((v <= cfg.dark_value_max) & (s <= cfg.dark_saturation_max)).astype(np.uint8) * 255
    if cfg.marker_morph_kernel > 1:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.marker_morph_kernel, cfg.marker_morph_kernel))
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, kernel)
        dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, kernel)

    marker_fraction, marker_pixels = largest_blob_fraction(dark, sample)
    sample_area = max(1, int(cv2.countNonZero(sample)))
    blue_fraction = cv2.countNonZero(cv2.bitwise_and(blue, sample)) / sample_area

    values = gray[sample > 0]
    texture_std = float(np.std(values)) if values.size else 0.0

    marker_defect = marker_fraction >= cfg.marker_min_area_fraction and marker_pixels >= cfg.marker_min_pixels
    missing_defect = (
        blue_fraction >= cfg.blue_missing_fraction
        and texture_std <= cfg.low_texture_std_max
        and float(np.mean(v[sample > 0])) >= cfg.missing_value_min
    )

    return ChipResult(
        cell=cell,
        defective=marker_defect or missing_defect,
        marker_defect=marker_defect,
        missing_defect=missing_defect,
        marker_fraction=marker_fraction,
        blue_fraction=blue_fraction,
        texture_std=texture_std,
    )


def analyze_image_manual(
    image: np.ndarray,
    points: np.ndarray,
    cfg: Config,
) -> tuple[list[ChipResult], WaferGeometry, dict[str, np.ndarray], np.ndarray, np.ndarray]:
    warped, matrix, inverse, geometry = perspective_from_points(image, points)
    cells = generate_chip_cells(geometry, cfg)
    results = [evaluate_chip(warped, cell, cfg) for cell in cells]
    debug_masks = {
        "warped": warped,
        "manual_points": points,
        "perspective_matrix": matrix,
    }
    return results, geometry, debug_masks, warped, inverse


def draw_label(
    image: np.ndarray,
    text: str,
    origin: tuple[int, int],
    scale: float = 0.65,
    color: tuple[int, int, int] = (255, 255, 255),
) -> None:
    x, y = origin
    cv2.putText(image, text, (x + 1, y + 1), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(image, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def annotate_results(
    image: np.ndarray,
    results: Iterable[ChipResult],
    cfg: Config,
    cell_to_image: np.ndarray | None = None,
) -> np.ndarray:
    out = image.copy()
    results = list(results)
    for result in results:
        color = cfg.defect_color if result.defective else cfg.normal_color
        polygon = transform_polygon(cell_polygon(result.cell), cell_to_image)
        cv2.polylines(out, [polygon], True, color, cfg.line_thickness, cv2.LINE_AA)
        center = transform_point(result.cell.center, cell_to_image)
        draw_label(out, str(result.cell.index), (center[0] - 10, center[1] + 5), scale=0.38, color=color)

    defective = sum(r.defective for r in results)
    total = len(results)
    rate = defective / total * 100.0 if total else 0.0

    panel_h = 112
    overlay = out.copy()
    cv2.rectangle(overlay, (0, 0), (out.shape[1], panel_h), (0, 0, 0), -1)
    out = cv2.addWeighted(overlay, 0.55, out, 0.45, 0)
    draw_text(out, f"芯片总数：{total}", (18, 14), cfg, size=24)
    draw_text(out, f"缺陷芯片数：{defective}", (18, 46), cfg, size=24, color=(80, 80, 255))
    draw_text(out, f"缺陷率：{rate:.2f}%", (18, 78), cfg, size=24)
    return out


def draw_debug_grid(
    image: np.ndarray,
    results: Iterable[ChipResult],
    geometry: WaferGeometry,
    debug_masks: dict[str, np.ndarray],
    cfg: Config,
    cell_to_image: np.ndarray | None = None,
) -> np.ndarray:
    out = image.copy()
    rect = (tuple(geometry.center), (geometry.width, geometry.height), geometry.angle_deg)
    outer = transform_polygon(cv2.boxPoints(rect).astype(np.int32), cell_to_image)
    cv2.polylines(out, [outer], True, cfg.grid_color, 3, cv2.LINE_AA)

    for result in results:
        polygon = transform_polygon(cell_polygon(result.cell), cell_to_image)
        cv2.polylines(out, [polygon], True, cfg.grid_color, 1, cv2.LINE_AA)

    draw_text(out, f"网格角度：{geometry.angle_deg:.2f} 度", (18, 14), cfg, size=24, color=(255, 220, 100))
    draw_text(out, f"预期芯片数：{TOTAL_EXPECTED_CHIPS}", (18, 46), cfg, size=24, color=(255, 220, 100))
    return out


def save_matplotlib_debug(path: Path, image: np.ndarray, debug_masks: dict[str, np.ndarray]) -> None:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    warped_rgb = cv2.cvtColor(debug_masks["warped"], cv2.COLOR_BGR2RGB)
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    axes[0].imshow(rgb)
    axes[0].set_title("原图")
    axes[1].imshow(warped_rgb)
    axes[1].set_title("四点校正后")
    for ax in axes:
        ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def print_summary(results: list[ChipResult]) -> None:
    defective = sum(r.defective for r in results)
    total = len(results)
    rate = defective / total * 100.0 if total else 0.0
    marker = sum(r.marker_defect for r in results)
    missing = sum(r.missing_defect for r in results)

    print(f"芯片总数：{total}")
    print(f"缺陷芯片数：{defective}")
    print(f"缺陷率：{rate:.2f}%")
    print(f"标记缺陷：{marker}")
    print(f"缺片缺陷：{missing}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="晶圆缺陷自动计数程序。默认点击上中、右中、下中、左中四颗参考芯片中心进行校准。")
    parser.add_argument("image", type=Path, help="输入晶圆图片。")
    parser.add_argument("--out", type=Path, default=Path("result.png"), help="带缺陷标注的结果图。")
    parser.add_argument("--debug", type=Path, default=Path("debug_grid.png"), help="芯片网格调试图。")
    parser.add_argument("--debug-masks", type=Path, default=None, help="可选：保存额外调试图。")
    parser.add_argument("--points", type=Path, default=None, help="读取已保存的四个参考芯片中心点，跳过人工点击。")
    parser.add_argument("--save-points", type=Path, default=None, help="保存本次人工点击的四个参考芯片中心点。")

    parser.add_argument("--dark-value-max", type=int, default=None, help="黑色标记亮度阈值，调高可识别更淡笔迹。")
    parser.add_argument("--marker-min-area-fraction", type=float, default=None, help="标记最小面积比例，调高减少误报。")
    parser.add_argument("--blue-missing-fraction", type=float, default=None, help="蓝膜暴露比例阈值，调高减少缺片误报。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = Config()

    for arg_name, cfg_name in [
        ("dark_value_max", "dark_value_max"),
        ("marker_min_area_fraction", "marker_min_area_fraction"),
        ("blue_missing_fraction", "blue_missing_fraction"),
    ]:
        value = getattr(args, arg_name)
        if value is not None:
            setattr(cfg, cfg_name, value)

    image = load_image(args.image)
    print("当前模式：人工参考芯片中心校准。")
    if args.points:
        points = load_points(args.points)
        print(f"已读取校准点：{args.points}")
    else:
        points = collect_manual_points(image, cfg)
        if args.save_points:
            save_points(args.save_points, points)
            print(f"已保存校准点：{args.save_points}")

    results, geometry, debug_masks, warped, cell_to_image = analyze_image_manual(image, points, cfg)

    if len(results) != TOTAL_EXPECTED_CHIPS:
        raise RuntimeError(f"内部布局错误：预期 {TOTAL_EXPECTED_CHIPS} 个芯片，实际 {len(results)} 个。")

    annotated = annotate_results(image, results, cfg, cell_to_image=cell_to_image)
    debug_grid = draw_debug_grid(image, results, geometry, debug_masks, cfg, cell_to_image=cell_to_image)

    cv2.imwrite(str(args.out), annotated)
    cv2.imwrite(str(args.debug), debug_grid)
    if args.debug_masks:
        save_matplotlib_debug(args.debug_masks, image, debug_masks)

    print_summary(results)
    print(f"已保存结果图：{args.out}")
    print(f"已保存网格调试图：{args.debug}")
    if args.debug_masks:
        print(f"已保存额外调试图：{args.debug_masks}")


if __name__ == "__main__":
    main()
