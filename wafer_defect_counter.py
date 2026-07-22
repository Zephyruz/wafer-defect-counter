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
import csv
import json
import math
import re
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


class AutoDetectFailed(RuntimeError):
    """Raised when automatic wafer geometry detection cannot locate the grid with enough confidence."""


class RegistrationFailed(RuntimeError):
    """Raised when a fixture image cannot be aligned safely to its calibration image."""


def _with_grid_refine_enabled(cfg: Config) -> Config:
    """Return a copy of cfg with grid refinement turned on for auto calibration."""
    refined = Config()
    for field in refined.__dataclass_fields__:
        setattr(refined, field, getattr(cfg, field))
    refined.refine_grid = True
    return refined


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
    marker_cell_scale: float = 0.82
    refine_grid: bool = False
    grid_refine_offset_steps: int = 3
    grid_refine_pitch_steps: int = 2
    grid_refine_offset_fraction: float = 0.18
    grid_refine_pitch_fraction: float = 0.018
    grid_refine_angle_range_deg: float = 0.0
    grid_refine_angle_steps: int = 0

    # Marker detection
    dark_value_max: int = 70
    local_dark_value_max: int = 135
    local_dark_delta_min: int = 45
    dark_saturation_max: int = 210
    marker_min_area_fraction: float = 0.018
    marker_total_area_fraction: float = 0.024
    marker_min_pixels: int = 18
    marker_line_min_pixels: int = 10
    marker_line_min_length_fraction: float = 0.16
    marker_component_contrast_min: float = 42.0
    marker_grid_line_reject_fraction: float = 0.78
    marker_grid_line_thin_max: int = 5
    marker_morph_kernel: int = 3
    review_margin: float = 0.72

    # Missing-chip / blue-tape exposure detection
    blue_missing_fraction: float = 0.18
    low_texture_std_max: float = 18.0
    missing_value_min: int = 35

    # Auto geometry detection (replaces manual 4-point calibration)
    auto_detect_min_score: float = 6.0
    auto_detect_min_region_score: float = 0.0
    auto_detect_min_margin: float = 1.0
    auto_detect_angle_range_deg: float = 15.0
    auto_detect_angle_step_deg: float = 1.0
    auto_detect_scale_min: float = 0.78
    auto_detect_scale_max: float = 1.02
    auto_detect_scale_steps: int = 13
    auto_detect_min_radius_fraction: float = 0.25
    auto_detect_disk_min_area_fraction: float = 0.15

    # Fixed-fixture registration (first image manual, later images aligned to it)
    registration_width: int = 540
    registration_height: int = 960
    registration_min_correlation: float = 0.70
    registration_max_translation_px: float = 60.0
    registration_high_confidence: float = 0.85
    registration_high_confidence_max_translation_px: float = 80.0
    registration_max_angle_deg: float = 4.0

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
    marker_total_fraction: float
    marker_largest_pixels: int
    marker_total_pixels: int
    marker_line_pixels: int
    marker_line_length: float
    marker_contrast: float
    blue_fraction: float
    texture_std: float
    review_candidate: bool
    review_reason: str


@dataclass
class ImageFeatures:
    hsv: np.ndarray
    gray: np.ndarray
    h: np.ndarray
    s: np.ndarray
    v: np.ndarray
    blue: np.ndarray


@dataclass
class ImageAnalysisSummary:
    total_chips: int
    defective_chips: int
    defect_rate: float
    marker_defects: int
    missing_defects: int
    result_image: str
    review_image: str
    debug_image: str
    chip_report: str
    chips: list[dict[str, int | float | bool]]


def load_image(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"无法读取图片：{path}")
    return image


def register_fixture_points(
    reference_image: np.ndarray,
    target_image: np.ndarray,
    reference_points: list[list[float]] | np.ndarray,
    cfg: Config | None = None,
) -> tuple[np.ndarray, dict[str, float]]:
    """Move calibration points from a fixture reference photo onto a later photo."""
    cfg = cfg or Config()
    points = np.asarray(reference_points, dtype=np.float32)
    if points.shape != (4, 2):
        raise ValueError("reference_points must contain four [x, y] coordinates")
    if reference_image.shape[:2] != target_image.shape[:2]:
        raise RegistrationFailed("照片尺寸与首张校准照片不同，请人工重新选四点。")

    size = (cfg.registration_width, cfg.registration_height)

    def registration_view(image: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
        gray = cv2.GaussianBlur(gray, (0, 0), 2.0)
        return gray.astype(np.float32) / 255.0

    reference_small = registration_view(reference_image)
    target_small = registration_view(target_image)
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-6)
    try:
        correlation, warp = cv2.findTransformECC(
            reference_small,
            target_small,
            warp,
            cv2.MOTION_EUCLIDEAN,
            criteria,
            None,
            5,
        )
    except cv2.error as exc:
        raise RegistrationFailed("照片与首张校准图无法稳定对齐，请人工重新选四点。") from exc

    sx = size[0] / reference_image.shape[1]
    sy = size[1] / reference_image.shape[0]
    warp_full = warp.copy()
    warp_full[0, 2] /= sx
    warp_full[1, 2] /= sy
    dx = float(warp_full[0, 2])
    dy = float(warp_full[1, 2])
    translation = math.hypot(dx, dy)
    angle = math.degrees(math.atan2(float(warp_full[1, 0]), float(warp_full[0, 0])))
    if correlation < cfg.registration_min_correlation:
        raise RegistrationFailed(f"照片对齐置信度不足（{correlation:.3f}），请人工重新选四点。")
    max_translation = (
        cfg.registration_high_confidence_max_translation_px
        if correlation >= cfg.registration_high_confidence
        else cfg.registration_max_translation_px
    )
    if translation > max_translation or abs(angle) > cfg.registration_max_angle_deg:
        raise RegistrationFailed(
            f"照片移动超出支架允许范围（位移 {translation:.1f}px，旋转 {angle:.2f}°），请人工重新选四点。"
        )

    adjusted = cv2.transform(points.reshape(1, -1, 2), warp_full).reshape(-1, 2)
    metrics = {
        "correlation": float(correlation),
        "dx": dx,
        "dy": dy,
        "translation": translation,
        "angle_deg": angle,
    }
    return adjusted, metrics


def save_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix or ".png"
    params = [cv2.IMWRITE_JPEG_QUALITY, 88] if suffix.lower() in {".jpg", ".jpeg"} else []
    ok, encoded = cv2.imencode(suffix, image, params)
    if not ok:
        raise RuntimeError(f"无法保存图片：{path}")
    encoded.tofile(str(path))


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

        if len(points) == 4:
            try:
                _warped, _matrix, inverse, geometry = perspective_from_points(image, np.array(points, dtype=np.float32))
                preview_cells = generate_chip_cells(geometry, cfg)
                for cell in preview_cells:
                    polygon = transform_polygon(cell_polygon(cell), inverse)
                    shown_polygon = np.round(polygon.astype(np.float32) * scale).astype(np.int32)
                    cv2.polylines(display, [shown_polygon], True, (255, 255, 0), 1, cv2.LINE_AA)
            except Exception:
                pass

        next_text = "确认网格对齐后按 Enter；不对按 R 重选" if len(points) == 4 else f"请点击：{names[len(points)]}"
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


def _detect_wafer_disk(image: np.ndarray, cfg: Config) -> tuple[tuple[float, float], float, np.ndarray]:
    """Locate the wafer disk via the surrounding blue tape ring.

    Returns (center_xy, radius, disk_mask). The disk mask covers the chip-filled
    wafer interior; the blue tape ring is excluded so it does not skew the fit.
    """
    features = build_image_features(image, cfg)
    blue = features.blue
    image_area = float(image.shape[0] * image.shape[1])

    # Dilate blue tape so the chip-filled interior becomes a solid hole we can fill.
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    filled = cv2.morphologyEx(cv2.bitwise_not(blue), cv2.MORPH_CLOSE, kernel)
    # Flood-fill from the image corners (background) to label outside-tape regions.
    flood = filled.copy()
    h, w = filled.shape[:2]
    mask = np.zeros((h + 2, w + 2), dtype=np.uint8)
    for seed in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        cv2.floodFill(flood, mask, seed, 0)
    disk_mask = (flood > 0).astype(np.uint8) * 255
    disk_mask = cv2.morphologyEx(disk_mask, cv2.MORPH_OPEN, kernel)

    disk_area = float(cv2.countNonZero(disk_mask))
    if disk_area < image_area * cfg.auto_detect_disk_min_area_fraction:
        # Fallback: largest bright/gray contour when blue tape ring is weak.
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (0, 0), 6)
        _, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        disk_mask = binary
        disk_area = float(cv2.countNonZero(disk_mask))
        if disk_area < image_area * cfg.auto_detect_disk_min_area_fraction:
            raise AutoDetectFailed(f"未找到晶圆圆盘，蓝膜/圆盘面积占比过小（{disk_area / image_area:.2%}）。")

    contours, _ = cv2.findContours(disk_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise AutoDetectFailed("圆盘掩膜没有轮廓。")
    largest = max(contours, key=cv2.contourArea)
    (cx, cy), radius = cv2.minEnclosingCircle(largest)

    min_radius = min(image.shape[0], image.shape[1]) * cfg.auto_detect_min_radius_fraction
    if radius < min_radius:
        raise AutoDetectFailed(f"圆盘半径过小（{radius:.0f}px < {min_radius:.0f}px）。")

    return (float(cx), float(cy)), float(radius), disk_mask


def auto_detect_geometry(image: np.ndarray, cfg: Config) -> tuple[WaferGeometry, float]:
    """Detect wafer grid geometry (center, angle, pitch) from the image alone.

    Replaces manual 4-point calibration. Returns (geometry, confidence) where
    confidence is the final grid-alignment score. Raises AutoDetectFailed when
    the wafer disk or grid cannot be located confidently — callers fall back to
    manual 4-point input.
    """
    (cx, cy), radius, _disk_mask = _detect_wafer_disk(image, cfg)

    rows = len(ROW_COUNTS)
    max_cols = max(ROW_COUNTS)
    # Disk diameter spans 23 chips (middle row has 23 cols; 23 rows tall).
    diameter = radius * 2.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    response = cv2.GaussianBlur(255 - gray, (3, 3), 0)

    center = np.array([cx, cy], dtype=np.float32)
    step = cfg.auto_detect_angle_step_deg
    span = cfg.auto_detect_angle_range_deg
    angles = np.arange(-span, span + step * 0.5, step)
    scales = np.linspace(cfg.auto_detect_scale_min, cfg.auto_detect_scale_max, cfg.auto_detect_scale_steps)
    candidates: list[tuple[float, WaferGeometry]] = []
    for scale in scales:
        size = diameter * float(scale)
        for angle in angles:
            candidate = WaferGeometry(
                center=center,
                width=size,
                height=size * rows / max_cols,
                angle_deg=float(angle),
            )
            candidates.append((grid_alignment_score(response, candidate), candidate))

    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, geometry = candidates[0]
    distinct_scores = [
        score
        for score, candidate in candidates[1:]
        if abs(candidate.angle_deg - geometry.angle_deg) >= max(2.0, step * 2.0)
    ]
    runner_up = distinct_scores[0] if distinct_scores else -math.inf
    margin = best_score - runner_up

    if best_score < cfg.auto_detect_min_score:
        raise AutoDetectFailed(
            f"自动网格定位置信度不足（分数 {best_score:.1f} < {cfg.auto_detect_min_score}），请改用手动 4 点校准。"
        )
    if abs(geometry.angle_deg) >= span - step * 0.5:
        raise AutoDetectFailed(f"自动网格角度落在搜索边界（{geometry.angle_deg:.1f}°），请改用手动 4 点校准。")
    if margin < cfg.auto_detect_min_margin:
        raise AutoDetectFailed(
            f"自动网格方向不明确（最优分数仅领先 {margin:.1f} < {cfg.auto_detect_min_margin}），请改用手动 4 点校准。"
        )

    geometry, refined_score = refine_grid_geometry(image, geometry, _with_grid_refine_enabled(cfg))
    region_score = grid_alignment_region_score(response, geometry)
    if region_score < cfg.auto_detect_min_region_score:
        raise AutoDetectFailed(
            f"自动网格局部贴合不足（最差区域 {region_score:.1f} < {cfg.auto_detect_min_region_score}），请改用手动 4 点校准。"
        )

    return geometry, float(refined_score)


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


def _grid_alignment_scores(response: np.ndarray, geometry: WaferGeometry) -> tuple[float, float]:
    """Score rotated grid boundaries against neighboring chip interiors.

    Dark separator lines should have a stronger inverted-gray response than
    samples roughly one third of a pitch inside the adjacent chips.  Sampling
    is performed in the candidate's rotated coordinate system, so angle,
    center, and pitch all affect the result.
    """
    rows = len(ROW_COUNTS)
    max_cols = max(ROW_COUNTS)
    pitch_x = geometry.width / max_cols
    pitch_y = geometry.height / rows
    samples_per_line = 128

    horizontal_x = np.linspace(-geometry.width * 0.40, geometry.width * 0.40, samples_per_line)
    horizontal_y = (np.arange(1, rows, dtype=np.float32) - rows / 2.0) * pitch_y
    hx = np.tile(horizontal_x, len(horizontal_y))
    hy = np.repeat(horizontal_y, samples_per_line)

    vertical_y = np.linspace(-geometry.height * 0.40, geometry.height * 0.40, samples_per_line)
    vertical_x = (np.arange(1, max_cols, dtype=np.float32) - max_cols / 2.0) * pitch_x
    vx = np.repeat(vertical_x, samples_per_line)
    vy = np.tile(vertical_y, len(vertical_x))

    def sample(local_x: np.ndarray, local_y: np.ndarray) -> np.ndarray:
        points = np.column_stack([local_x, local_y]).astype(np.float32)
        rotated = rotate_points(points, geometry.angle_deg)
        map_x = (rotated[:, 0] + geometry.center[0]).reshape(1, -1).astype(np.float32)
        map_y = (rotated[:, 1] + geometry.center[1]).reshape(1, -1).astype(np.float32)
        values = cv2.remap(
            response,
            map_x,
            map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        ).reshape(-1).astype(np.float32)
        valid = (
            (map_x.reshape(-1) >= 1)
            & (map_x.reshape(-1) < response.shape[1] - 1)
            & (map_y.reshape(-1) >= 1)
            & (map_y.reshape(-1) < response.shape[0] - 1)
        )
        values[~valid] = np.nan
        return values

    band = max(1.0, min(pitch_x, pitch_y) * 0.025)
    interior_x = pitch_x * 0.30
    interior_y = pitch_y * 0.30

    def mean_available(parts: list[np.ndarray]) -> np.ndarray:
        stacked = np.stack(parts)
        counts = np.sum(np.isfinite(stacked), axis=0)
        return np.divide(
            np.nansum(stacked, axis=0),
            counts,
            out=np.full(counts.shape, np.nan, dtype=np.float32),
            where=counts > 0,
        )

    horizontal_boundary = mean_available([sample(hx, hy - band), sample(hx, hy), sample(hx, hy + band)])
    horizontal_interior = mean_available([sample(hx, hy - interior_y), sample(hx, hy + interior_y)])
    vertical_boundary = mean_available([sample(vx - band, vy), sample(vx, vy), sample(vx + band, vy)])
    vertical_interior = mean_available([sample(vx - interior_x, vy), sample(vx + interior_x, vy)])
    horizontal_contrast = horizontal_boundary - horizontal_interior
    vertical_contrast = vertical_boundary - vertical_interior

    region_scores: list[float] = []
    for x_positive in (False, True):
        for y_positive in (False, True):
            h_mask = (
                ((hx >= 0) == x_positive)
                & ((hy >= 0) == y_positive)
                & (np.abs(hx) <= geometry.width * 0.28)
            )
            v_mask = (
                ((vx >= 0) == x_positive)
                & ((vy >= 0) == y_positive)
                & (np.abs(vy) <= geometry.height * 0.28)
            )
            h_region = horizontal_contrast[h_mask]
            v_region = vertical_contrast[v_mask]
            region = np.concatenate([h_region, v_region])
            region = region[np.isfinite(region)]
            if region.size:
                region_scores.append(float(np.mean(region)))

    all_contrast = np.concatenate([horizontal_contrast, vertical_contrast])
    all_contrast = all_contrast[np.isfinite(all_contrast)]
    if not all_contrast.size or len(region_scores) != 4:
        return -math.inf, -math.inf
    return float(np.mean(all_contrast)), float(min(region_scores))


def grid_alignment_score(response: np.ndarray, geometry: WaferGeometry) -> float:
    return _grid_alignment_scores(response, geometry)[0]


def grid_alignment_region_score(response: np.ndarray, geometry: WaferGeometry) -> float:
    return _grid_alignment_scores(response, geometry)[1]


def refine_grid_geometry(warped: np.ndarray, geometry: WaferGeometry, cfg: Config) -> tuple[WaferGeometry, float]:
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    response = cv2.GaussianBlur(255 - gray, (3, 3), 0)
    if not cfg.refine_grid:
        return geometry, grid_alignment_score(response, geometry)

    best_geometry = geometry
    best_score = grid_alignment_score(response, geometry)
    if cfg.grid_refine_angle_range_deg > 0 and cfg.grid_refine_angle_steps > 0:
        angle_values = np.linspace(
            geometry.angle_deg - cfg.grid_refine_angle_range_deg,
            geometry.angle_deg + cfg.grid_refine_angle_range_deg,
            cfg.grid_refine_angle_steps * 2 + 1,
        )
        for angle in angle_values:
            candidate = WaferGeometry(
                center=geometry.center.copy(),
                width=geometry.width,
                height=geometry.height,
                angle_deg=float(angle),
            )
            score = grid_alignment_score(response, candidate)
            if score > best_score:
                best_score = score
                best_geometry = candidate

    geometry = best_geometry
    pitch_x = geometry.width / max(ROW_COUNTS)
    pitch_y = geometry.height / len(ROW_COUNTS)
    offset_x_values = np.linspace(-pitch_x * cfg.grid_refine_offset_fraction, pitch_x * cfg.grid_refine_offset_fraction, cfg.grid_refine_offset_steps)
    offset_y_values = np.linspace(-pitch_y * cfg.grid_refine_offset_fraction, pitch_y * cfg.grid_refine_offset_fraction, cfg.grid_refine_offset_steps)
    pitch_x_scale_values = np.linspace(1.0 - cfg.grid_refine_pitch_fraction, 1.0 + cfg.grid_refine_pitch_fraction, cfg.grid_refine_pitch_steps * 2 + 1)
    pitch_y_scale_values = np.linspace(1.0 - cfg.grid_refine_pitch_fraction, 1.0 + cfg.grid_refine_pitch_fraction, cfg.grid_refine_pitch_steps * 2 + 1)

    for sx in pitch_x_scale_values:
        for sy in pitch_y_scale_values:
            for dx in offset_x_values:
                for dy in offset_y_values:
                    candidate = WaferGeometry(
                        center=geometry.center + np.array([dx, dy], dtype=np.float32),
                        width=float(geometry.width * sx),
                        height=float(geometry.height * sy),
                        angle_deg=geometry.angle_deg,
                    )
                    score = grid_alignment_score(response, candidate)
                    if score > best_score:
                        best_score = score
                        best_geometry = candidate

    return best_geometry, best_score


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


def local_cell_masks(shape: tuple[int, int], cell: ChipCell, cfg: Config) -> tuple[tuple[int, int, int, int], np.ndarray, np.ndarray]:
    sample_poly = cell_polygon(cell, cfg.inner_cell_scale)
    marker_poly = cell_polygon(cell, cfg.marker_cell_scale)
    combined = np.vstack([sample_poly, marker_poly])
    pad = max(3, int(min(cell.width, cell.height) * 0.08))
    x0 = max(0, int(np.min(combined[:, 0])) - pad)
    y0 = max(0, int(np.min(combined[:, 1])) - pad)
    x1 = min(shape[1], int(np.max(combined[:, 0])) + pad + 1)
    y1 = min(shape[0], int(np.max(combined[:, 1])) + pad + 1)
    if x1 <= x0 or y1 <= y0:
        return (0, 0, 1, 1), np.zeros((1, 1), dtype=np.uint8), np.zeros((1, 1), dtype=np.uint8)

    offset = np.array([x0, y0], dtype=np.int32)
    local_shape = (y1 - y0, x1 - x0)
    sample = np.zeros(local_shape, dtype=np.uint8)
    marker_sample = np.zeros(local_shape, dtype=np.uint8)
    cv2.fillConvexPoly(sample, sample_poly - offset, 255)
    cv2.fillConvexPoly(marker_sample, marker_poly - offset, 255)
    return (x0, y0, x1, y1), sample, marker_sample


def build_image_features(image: np.ndarray, cfg: Config) -> ImageFeatures:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blue = (
        (h >= cfg.blue_hue_low)
        & (h <= cfg.blue_hue_high)
        & (s >= cfg.blue_sat_min)
        & (v >= cfg.blue_value_min)
    ).astype(np.uint8) * 255
    return ImageFeatures(hsv=hsv, gray=gray, h=h, s=s, v=v, blue=blue)


def marker_component_metrics(
    binary: np.ndarray,
    sample_mask: np.ndarray,
    gray: np.ndarray,
    cfg: Config,
    cell: ChipCell,
) -> dict[str, float | int]:
    masked = cv2.bitwise_and(binary, sample_mask)
    sample_area = max(1, int(cv2.countNonZero(sample_mask)))
    components, labels, stats, _ = cv2.connectedComponentsWithStats(masked, connectivity=8)
    if components <= 1:
        return {
            "largest_fraction": 0.0,
            "largest_pixels": 0,
            "total_fraction": 0.0,
            "total_pixels": 0,
            "line_pixels": 0,
            "line_length": 0.0,
            "contrast": 0.0,
        }

    valid_mask = np.zeros(masked.shape, dtype=np.uint8)
    largest_pixels = 0
    total_pixels = 0
    best_line_pixels = 0
    best_line_length = 0.0
    best_contrast = 0.0
    background_values = gray[(sample_mask > 0) & (masked == 0)]
    background_median = float(np.median(background_values)) if background_values.size else float(np.median(gray[sample_mask > 0]))

    for label in range(1, components):
        w = int(stats[label, cv2.CC_STAT_WIDTH])
        h = int(stats[label, cv2.CC_STAT_HEIGHT])
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < cfg.marker_line_min_pixels:
            continue

        line_length = float(max(w, h))
        line_thickness = float(min(w, h))
        likely_grid_line = (
            line_length >= max(cell.width, cell.height) * cfg.marker_grid_line_reject_fraction
            and line_thickness <= cfg.marker_grid_line_thin_max
        )
        if likely_grid_line:
            continue

        component = labels == label
        component_values = gray[component]
        component_mean = float(np.mean(component_values)) if component_values.size else 255.0
        contrast = background_median - component_mean
        if contrast < cfg.marker_component_contrast_min:
            continue

        valid_mask[component] = 255
        largest_pixels = max(largest_pixels, area)
        total_pixels += area

        if line_length > best_line_length:
            best_line_length = line_length
            best_line_pixels = area
            best_contrast = contrast

    if cfg.marker_morph_kernel > 1:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (cfg.marker_morph_kernel, cfg.marker_morph_kernel))
        valid_mask = cv2.morphologyEx(valid_mask, cv2.MORPH_CLOSE, kernel)
        components, labels, stats, _ = cv2.connectedComponentsWithStats(valid_mask, connectivity=8)
        if components > 1:
            largest_pixels = int(np.max(stats[1:, cv2.CC_STAT_AREA]))
            total_pixels = int(np.sum(stats[1:, cv2.CC_STAT_AREA]))

    return {
        "largest_fraction": largest_pixels / sample_area,
        "largest_pixels": largest_pixels,
        "total_fraction": total_pixels / sample_area,
        "total_pixels": total_pixels,
        "line_pixels": best_line_pixels,
        "line_length": best_line_length,
        "contrast": best_contrast,
    }


def evaluate_chip(image: np.ndarray, cell: ChipCell, cfg: Config, features: ImageFeatures | None = None) -> ChipResult:
    features = features or build_image_features(image, cfg)
    (x0, y0, x1, y1), sample, marker_sample = local_cell_masks(features.gray.shape, cell, cfg)
    s = features.s[y0:y1, x0:x1]
    v = features.v[y0:y1, x0:x1]
    gray = features.gray[y0:y1, x0:x1]
    blue = features.blue[y0:y1, x0:x1]

    sample_area = max(1, int(cv2.countNonZero(sample)))
    blue_fraction = cv2.countNonZero(cv2.bitwise_and(blue, sample)) / sample_area

    values = gray[sample > 0]
    texture_std = float(np.std(values)) if values.size else 0.0
    marker_values = gray[marker_sample > 0]
    local_median_v = float(np.median(v[marker_sample > 0])) if marker_values.size else 0.0

    absolute_dark = v <= cfg.dark_value_max
    locally_dark = (v <= cfg.local_dark_value_max) & ((local_median_v - v) >= cfg.local_dark_delta_min)
    dark = ((absolute_dark | locally_dark) & (s <= cfg.dark_saturation_max)).astype(np.uint8) * 255
    metrics = marker_component_metrics(dark, marker_sample, gray, cfg, cell)

    marker_fraction = float(metrics["largest_fraction"])
    marker_pixels = int(metrics["largest_pixels"])
    marker_total_fraction = float(metrics["total_fraction"])
    marker_total_pixels = int(metrics["total_pixels"])
    marker_line_pixels = int(metrics["line_pixels"])
    marker_line_length = float(metrics["line_length"])
    marker_contrast = float(metrics["contrast"])

    min_line_length = min(cell.width, cell.height) * cfg.marker_line_min_length_fraction
    marker_defect = (
        (marker_fraction >= cfg.marker_min_area_fraction and marker_pixels >= cfg.marker_min_pixels)
        or marker_total_fraction >= cfg.marker_total_area_fraction
        or (marker_line_pixels >= cfg.marker_line_min_pixels and marker_line_length >= min_line_length)
    )
    missing_defect = (
        blue_fraction >= cfg.blue_missing_fraction
        and texture_std <= cfg.low_texture_std_max
        and values.size > 0
        and float(np.mean(v[sample > 0])) >= cfg.missing_value_min
    )
    review_reasons: list[str] = []
    if not marker_defect:
        margin = cfg.review_margin
        if marker_fraction >= cfg.marker_min_area_fraction * margin and marker_pixels >= cfg.marker_min_pixels * margin:
            review_reasons.append("near_marker_area")
        if marker_total_fraction >= cfg.marker_total_area_fraction * margin:
            review_reasons.append("near_marker_total")
        if marker_line_pixels >= cfg.marker_line_min_pixels * margin and marker_line_length >= min_line_length * margin:
            review_reasons.append("near_marker_line")
    if not missing_defect and blue_fraction >= cfg.blue_missing_fraction * cfg.review_margin:
        review_reasons.append("near_missing_chip")
    review_candidate = bool(review_reasons) and not (marker_defect or missing_defect)

    return ChipResult(
        cell=cell,
        defective=marker_defect or missing_defect,
        marker_defect=marker_defect,
        missing_defect=missing_defect,
        marker_fraction=marker_fraction,
        marker_total_fraction=marker_total_fraction,
        marker_largest_pixels=marker_pixels,
        marker_total_pixels=marker_total_pixels,
        marker_line_pixels=marker_line_pixels,
        marker_line_length=marker_line_length,
        marker_contrast=marker_contrast,
        blue_fraction=blue_fraction,
        texture_std=texture_std,
        review_candidate=review_candidate,
        review_reason=";".join(review_reasons),
    )


def analyze_image_manual(
    image: np.ndarray,
    points: np.ndarray,
    cfg: Config,
) -> tuple[list[ChipResult], WaferGeometry, dict[str, np.ndarray], np.ndarray, np.ndarray]:
    warped, matrix, inverse, geometry = perspective_from_points(image, points)
    geometry, grid_score = refine_grid_geometry(warped, geometry, cfg)
    cells = generate_chip_cells(geometry, cfg)
    features = build_image_features(warped, cfg)
    results = [evaluate_chip(warped, cell, cfg, features) for cell in cells]
    debug_masks = {
        "warped": warped,
        "manual_points": points,
        "perspective_matrix": matrix,
        "grid_alignment_score": grid_score,
    }
    return results, geometry, debug_masks, warped, inverse


def analyze_image_auto(
    image: np.ndarray,
    cfg: Config,
) -> tuple[list[ChipResult], WaferGeometry, dict[str, np.ndarray], np.ndarray, np.ndarray | None]:
    """Auto-calibration variant of analyze_image_manual.

    Detects wafer geometry from the image alone (no 4-point input). Cells are
    generated directly in the original image coordinate system, so the inverse
    transform is None and downstream transform_* helpers pass coordinates through.
    """
    geometry, grid_score = auto_detect_geometry(image, cfg)
    cells = generate_chip_cells(geometry, cfg)
    features = build_image_features(image, cfg)
    results = [evaluate_chip(image, cell, cfg, features) for cell in cells]
    debug_masks = {
        "warped": image,
        "manual_points": None,
        "perspective_matrix": None,
        "grid_alignment_score": grid_score,
    }
    return results, geometry, debug_masks, image, None


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
    draw_indices: bool = True,
) -> np.ndarray:
    out = image.copy()
    results = list(results)
    for result in results:
        color = cfg.defect_color if result.defective else cfg.normal_color
        polygon = transform_polygon(cell_polygon(result.cell), cell_to_image)
        cv2.polylines(out, [polygon], True, color, cfg.line_thickness, cv2.LINE_AA)
        if draw_indices:
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

    manual_points = debug_masks.get("manual_points")
    if manual_points is not None:
        labels = ["上", "右", "下", "左"]
        for label, point in zip(labels, np.asarray(manual_points, dtype=np.float32)):
            center = tuple(np.round(point).astype(int))
            cv2.circle(out, center, 12, (0, 0, 255), -1, cv2.LINE_AA)
            draw_text(out, label, (center[0] + 14, center[1] - 14), cfg, size=28, color=(0, 0, 255))

    draw_text(out, f"网格角度：{geometry.angle_deg:.2f} 度", (18, 14), cfg, size=24, color=(255, 220, 100))
    draw_text(out, f"预期芯片数：{TOTAL_EXPECTED_CHIPS}", (18, 46), cfg, size=24, color=(255, 220, 100))
    grid_score = debug_masks.get("grid_alignment_score")
    if grid_score is not None:
        draw_text(out, f"网格贴合分数：{float(grid_score):.1f}", (18, 78), cfg, size=24, color=(255, 220, 100))
    refine_text = "开" if cfg.refine_grid else "关"
    draw_text(out, f"网格微调：{refine_text}", (18, 110), cfg, size=24, color=(255, 220, 100))
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


def save_chip_report(path: Path, results: list[ChipResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "index",
        "row",
        "col",
        "defective",
        "marker_defect",
        "missing_defect",
        "marker_fraction",
        "marker_total_fraction",
        "marker_largest_pixels",
        "marker_total_pixels",
        "marker_line_pixels",
        "marker_line_length",
        "marker_contrast",
        "blue_fraction",
        "texture_std",
        "review_candidate",
        "review_reason",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "index": result.cell.index,
                    "row": result.cell.row,
                    "col": result.cell.col,
                    "defective": int(result.defective),
                    "marker_defect": int(result.marker_defect),
                    "missing_defect": int(result.missing_defect),
                    "marker_fraction": f"{result.marker_fraction:.6f}",
                    "marker_total_fraction": f"{result.marker_total_fraction:.6f}",
                    "marker_largest_pixels": result.marker_largest_pixels,
                    "marker_total_pixels": result.marker_total_pixels,
                    "marker_line_pixels": result.marker_line_pixels,
                    "marker_line_length": f"{result.marker_line_length:.2f}",
                    "marker_contrast": f"{result.marker_contrast:.2f}",
                    "blue_fraction": f"{result.blue_fraction:.6f}",
                    "texture_std": f"{result.texture_std:.2f}",
                    "review_candidate": int(result.review_candidate),
                    "review_reason": result.review_reason,
                }
            )


def natural_sort_key(path: Path) -> list[int | str]:
    parts = re.split(r"(\d+)", path.stem)
    return [int(part) if part.isdigit() else part.lower() for part in parts]


def find_points_file(points_dir: Path, image_path: Path) -> Path:
    candidates = [
        points_dir / f"{image_path.stem}.json",
        points_dir / f"points{image_path.stem}.json",
        points_dir / f"points_{image_path.stem}.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def process_one_image(
    image_path: Path,
    points: np.ndarray | None,
    cfg: Config,
    out_path: Path,
    debug_path: Path,
    report_path: Path | None,
) -> list[ChipResult]:
    image = load_image(image_path)
    if points is None:
        results, geometry, debug_masks, _warped, cell_to_image = analyze_image_auto(image, cfg)
    else:
        results, geometry, debug_masks, _warped, cell_to_image = analyze_image_manual(image, points, cfg)
    if len(results) != TOTAL_EXPECTED_CHIPS:
        raise RuntimeError(f"内部布局错误：预期 {TOTAL_EXPECTED_CHIPS} 个芯片，实际 {len(results)} 个。")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    annotated = annotate_results(image, results, cfg, cell_to_image=cell_to_image)
    debug_grid = draw_debug_grid(image, results, geometry, debug_masks, cfg, cell_to_image=cell_to_image)
    save_image(out_path, annotated)
    save_image(debug_path, debug_grid)
    if report_path:
        save_chip_report(report_path, results)
    return results


def write_batch_summary(path: Path, rows: list[dict[str, int | float | str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "image",
        "total",
        "defective",
        "defect_rate",
        "marker_defects",
        "missing_defects",
        "review_candidates",
        "result",
        "debug",
        "chip_report",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def process_batch(args: argparse.Namespace, cfg: Config) -> None:
    batch_dir = args.batch_dir
    output_dir = args.batch_output_dir
    points_dir = args.points_dir or (output_dir / "points")
    points_dir.mkdir(parents=True, exist_ok=True)

    image_paths: list[Path] = []
    for pattern in args.batch_pattern:
        image_paths.extend(batch_dir.glob(pattern))
    image_paths = sorted(set(path for path in image_paths if path.is_file()), key=natural_sort_key)
    if not image_paths:
        raise FileNotFoundError(f"没有在 {batch_dir} 找到图片。")

    print(f"批量处理图片数：{len(image_paths)}")
    summary_rows: list[dict[str, int | float | str]] = []
    for image_path in image_paths:
        points_path = find_points_file(points_dir, image_path)
        print(f"\n处理：{image_path.name}")
        points: np.ndarray | None = None
        if not args.auto and points_path.exists():
            points = load_points(points_path)
            print(f"已读取点位：{points_path}")
        elif args.auto:
            print("使用自动网格定位。")
        else:
            # No saved points and not --auto: try auto first, fall back to manual 4-point.
            try:
                image = load_image(image_path)
                _ = auto_detect_geometry(image, cfg)
                points = None
                print("自动网格定位可用。")
            except AutoDetectFailed as exc:
                print(f"自动定位失败：{exc}；改为人工点击 4 点。")
                image = load_image(image_path)
                points = collect_manual_points(image, cfg)
                save_points(points_path, points)
                print(f"已保存点位：{points_path}")

        stem = image_path.stem
        out_path = output_dir / f"result{stem}.png"
        debug_path = output_dir / f"debug{stem}.png"
        report_path = output_dir / f"chips{stem}.csv"
        results = process_one_image(image_path, points, cfg, out_path, debug_path, report_path)

        total = len(results)
        defective = sum(result.defective for result in results)
        marker = sum(result.marker_defect for result in results)
        missing = sum(result.missing_defect for result in results)
        review = sum(result.review_candidate for result in results)
        rate = defective / total if total else 0.0
        summary_rows.append(
            {
                "image": image_path.name,
                "total": total,
                "defective": defective,
                "defect_rate": f"{rate:.4f}",
                "marker_defects": marker,
                "missing_defects": missing,
                "review_candidates": review,
                "result": str(out_path),
                "debug": str(debug_path),
                "chip_report": str(report_path),
            }
        )
        print(f"完成：缺陷 {defective}/{total}，缺陷率 {rate * 100:.2f}%，建议复核 {review} 颗")

    summary_path = output_dir / "batch_summary.csv"
    write_batch_summary(summary_path, summary_rows)
    print(f"\n批量处理完成，汇总表：{summary_path}")


def process_image_file(
    image_path: Path,
    points: list[list[float]] | np.ndarray | None,
    output_dir: Path,
    cfg: Config | None = None,
) -> ImageAnalysisSummary:
    """Analyze one image from a web/API caller without opening manual UI windows.

    When points is None, auto-detects wafer geometry from the image; otherwise
    falls back to manual 4-point calibration.
    """
    cfg = cfg or Config()
    output_dir.mkdir(parents=True, exist_ok=True)

    image = load_image(image_path)
    if points is None:
        results, geometry, debug_masks, _warped, cell_to_image = analyze_image_auto(image, cfg)
    else:
        point_array = np.array(points, dtype=np.float32)
        if point_array.shape != (4, 2):
            raise ValueError("points must contain four [x, y] coordinates")
        results, geometry, debug_masks, _warped, cell_to_image = analyze_image_manual(image, point_array, cfg)
    if len(results) != TOTAL_EXPECTED_CHIPS:
        raise RuntimeError(f"Expected {TOTAL_EXPECTED_CHIPS} chips, got {len(results)}")

    review_path = output_dir / f"{image_path.stem}_review.jpg"
    debug_path = output_dir / f"{image_path.stem}_grid.jpg"
    report_path = output_dir / f"{image_path.stem}_chips.csv"
    review = annotate_results(image, results, cfg, cell_to_image=cell_to_image, draw_indices=False)
    debug_grid = draw_debug_grid(image, results, geometry, debug_masks, cfg, cell_to_image=cell_to_image)
    save_image(review_path, review)
    save_image(debug_path, debug_grid)
    save_chip_report(report_path, results)

    total = len(results)
    defective = sum(r.defective for r in results)
    marker = sum(r.marker_defect for r in results)
    missing = sum(r.missing_defect for r in results)
    return ImageAnalysisSummary(
        total_chips=total,
        defective_chips=defective,
        defect_rate=defective / total if total else 0.0,
        marker_defects=marker,
        missing_defects=missing,
        result_image=str(review_path),
        review_image=str(review_path),
        debug_image=str(debug_path),
        chip_report=str(report_path),
        chips=[],
    )


def generate_grid_preview_file(
    image_path: Path,
    points: list[list[float]] | np.ndarray,
    output_dir: Path,
    cfg: Config | None = None,
) -> str:
    """Generate only the calibrated grid preview, before running defect detection."""
    cfg = cfg or Config()
    output_dir.mkdir(parents=True, exist_ok=True)

    image = load_image(image_path)
    point_array = np.array(points, dtype=np.float32)
    if point_array.shape != (4, 2):
        raise ValueError("points must contain four [x, y] coordinates")

    _warped, _matrix, cell_to_image, geometry = perspective_from_points(image, point_array)
    cells = generate_chip_cells(geometry, cfg)

    out = image.copy()
    rect = (tuple(geometry.center), (geometry.width, geometry.height), geometry.angle_deg)
    outer = transform_polygon(cv2.boxPoints(rect).astype(np.int32), cell_to_image)
    cv2.polylines(out, [outer], True, cfg.grid_color, 3, cv2.LINE_AA)
    for cell in cells:
        polygon = transform_polygon(cell_polygon(cell), cell_to_image)
        cv2.polylines(out, [polygon], True, cfg.grid_color, 1, cv2.LINE_AA)

    for point_index, point in enumerate(point_array, start=1):
        center = tuple(np.round(point).astype(int))
        cv2.circle(out, center, 8, (0, 0, 255), 2, cv2.LINE_AA)
        draw_label(out, str(point_index), (center[0] + 10, center[1] - 10), scale=0.7, color=(0, 0, 255))

    draw_text(out, "网格预览：确认框线对齐后再开始统计", (18, 14), cfg, size=26, color=(255, 220, 100))
    preview_path = output_dir / f"{image_path.stem}_grid_preview.jpg"
    save_image(preview_path, out)
    return str(preview_path)


def generate_grid_preview_auto(
    image_path: Path,
    output_dir: Path,
    cfg: Config | None = None,
) -> str:
    """Generate an auto-detected grid preview (no manual 4-point input)."""
    cfg = cfg or Config()
    output_dir.mkdir(parents=True, exist_ok=True)

    image = load_image(image_path)
    geometry, score = auto_detect_geometry(image, cfg)

    cells = generate_chip_cells(geometry, cfg)
    out = image.copy()
    rect = (tuple(geometry.center), (geometry.width, geometry.height), geometry.angle_deg)
    outer = transform_polygon(cv2.boxPoints(rect).astype(np.int32), None)
    cv2.polylines(out, [outer], True, cfg.grid_color, 3, cv2.LINE_AA)
    for cell in cells:
        polygon = transform_polygon(cell_polygon(cell), None)
        cv2.polylines(out, [polygon], True, cfg.grid_color, 1, cv2.LINE_AA)

    draw_text(
        out,
        f"自动网格预览：分数 {score:.1f}，角度 {geometry.angle_deg:.1f} 度；对不准请用手动 4 点",
        (18, 14),
        cfg,
        size=26,
        color=(255, 220, 100),
    )
    preview_path = output_dir / f"{image_path.stem}_grid_preview.jpg"
    save_image(preview_path, out)
    return str(preview_path)


def apply_detection_preset(cfg: Config, preset: str) -> None:
    """Apply coarse threshold presets before per-parameter CLI overrides."""
    if preset == "sensitive":
        cfg.local_dark_delta_min = 35
        cfg.marker_component_contrast_min = 32.0
    elif preset == "balanced":
        cfg.local_dark_delta_min = 45
        cfg.marker_component_contrast_min = 42.0
    elif preset == "strict":
        cfg.local_dark_delta_min = 45
        cfg.marker_component_contrast_min = 46.0
    else:
        raise ValueError(f"Unknown detection preset: {preset}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="晶圆缺陷自动计数程序。默认点击上中、右中、下中、左中四颗参考芯片中心进行校准。")
    parser.add_argument("image", type=Path, nargs="?", help="输入晶圆图片。")
    parser.add_argument("--out", type=Path, default=Path("result.png"), help="带缺陷标注的结果图。")
    parser.add_argument("--debug", type=Path, default=Path("debug_grid.png"), help="芯片网格调试图。")
    parser.add_argument("--debug-masks", type=Path, default=None, help="可选：保存额外调试图。")
    parser.add_argument("--chip-report", type=Path, default=None, help="可选：导出每颗芯片的检测指标 CSV。")
    parser.add_argument("--batch-dir", type=Path, default=None, help="批量处理图片文件夹。")
    parser.add_argument("--batch-output-dir", type=Path, default=Path("batch_output"), help="批量处理输出文件夹。")
    parser.add_argument("--batch-pattern", nargs="*", default=["*.jpg", "*.jpeg"], help="批量图片匹配规则；默认不处理 PNG，避免把结果图当输入。")
    parser.add_argument("--points-dir", type=Path, default=None, help="批量模式点位文件夹；没有点位时会弹窗点选并保存。")
    parser.add_argument("--refine-grid", action="store_true", help="实验功能：自动微调网格中心和间距。默认关闭。")
    parser.add_argument("--no-refine-grid", action="store_true", help="兼容旧参数：保持关闭网格自动微调。")
    parser.add_argument("--points", type=Path, default=None, help="读取已保存的四个参考芯片中心点，跳过人工点击。")
    parser.add_argument("--auto", action="store_true", help="自动定位网格，不弹窗、不读 4 点；失败时报错退出。")
    parser.add_argument("--save-points", type=Path, default=None, help="保存本次人工点击的四个参考芯片中心点。")
    parser.add_argument(
        "--detection-preset",
        choices=["balanced", "sensitive", "strict"],
        default="balanced",
        help="检测预设：balanced 为默认；sensitive 等同已存档第四版阈值；strict 更保守。",
    )

    parser.add_argument("--dark-value-max", type=int, default=None, help="黑色标记亮度阈值，调高可识别更淡笔迹。")
    parser.add_argument("--local-dark-value-max", type=int, default=None, help="相对背景变暗检测的最高亮度，调高可识别更淡笔迹。")
    parser.add_argument("--local-dark-delta-min", type=int, default=None, help="相对背景至少变暗多少才算候选墨迹。")
    parser.add_argument("--marker-cell-scale", type=float, default=None, help="marker 检测范围，调高可覆盖更靠边的墨迹。")
    parser.add_argument("--marker-min-area-fraction", type=float, default=None, help="标记最小面积比例，调高减少误报。")
    parser.add_argument("--marker-total-area-fraction", type=float, default=None, help="多个小墨迹累计面积比例阈值。")
    parser.add_argument("--blue-missing-fraction", type=float, default=None, help="蓝膜暴露比例阈值，调高减少缺片误报。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = Config()
    apply_detection_preset(cfg, args.detection_preset)
    if args.refine_grid:
        cfg.refine_grid = True
    if args.no_refine_grid:
        cfg.refine_grid = False

    for arg_name, cfg_name in [
        ("dark_value_max", "dark_value_max"),
        ("local_dark_value_max", "local_dark_value_max"),
        ("local_dark_delta_min", "local_dark_delta_min"),
        ("marker_cell_scale", "marker_cell_scale"),
        ("marker_min_area_fraction", "marker_min_area_fraction"),
        ("marker_total_area_fraction", "marker_total_area_fraction"),
        ("blue_missing_fraction", "blue_missing_fraction"),
    ]:
        value = getattr(args, arg_name)
        if value is not None:
            setattr(cfg, cfg_name, value)

    if args.batch_dir:
        process_batch(args, cfg)
        return

    if args.image is None:
        raise SystemExit("请提供一张图片，或使用 --batch-dir 指定批量处理文件夹。")

    image = load_image(args.image)
    if args.auto:
        print("当前模式：自动网格定位（无需点 4 点）。")
        results, geometry, debug_masks, warped, cell_to_image = analyze_image_auto(image, cfg)
    else:
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

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.debug.parent.mkdir(parents=True, exist_ok=True)
    save_image(args.out, annotated)
    save_image(args.debug, debug_grid)
    if args.debug_masks:
        save_matplotlib_debug(args.debug_masks, image, debug_masks)
    if args.chip_report:
        save_chip_report(args.chip_report, results)

    print_summary(results)
    print(f"已保存结果图：{args.out}")
    print(f"已保存网格调试图：{args.debug}")
    if args.debug_masks:
        print(f"已保存额外调试图：{args.debug_masks}")
    if args.chip_report:
        print(f"已保存芯片指标表：{args.chip_report}")


if __name__ == "__main__":
    main()
