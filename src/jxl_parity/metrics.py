from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from PIL import Image, ImageChops, ImageEnhance

from .codecs import run_command, tool_path


@dataclass(frozen=True)
class PixelComparison:
    same_size: bool
    same_mode: bool
    equal_pixels: bool
    max_channel_delta: int | None
    psnr: float | None
    reference_mode: str
    decoded_mode: str
    reference_size: tuple[int, int]
    decoded_size: tuple[int, int]


def compare_pixels(reference_path: Path, decoded_path: Path) -> PixelComparison:
    with Image.open(reference_path) as reference, Image.open(decoded_path) as decoded:
        reference_mode = reference.mode
        decoded_mode = decoded.mode
        reference_size = reference.size
        decoded_size = decoded.size
        same_size = reference_size == decoded_size
        same_mode = reference_mode == decoded_mode
        if not same_size:
            return PixelComparison(
                same_size=False,
                same_mode=same_mode,
                equal_pixels=False,
                max_channel_delta=None,
                psnr=None,
                reference_mode=reference_mode,
                decoded_mode=decoded_mode,
                reference_size=reference_size,
                decoded_size=decoded_size,
            )

        reference_for_metrics, decoded_for_metrics = _coerce_for_metrics(reference, decoded)
        max_delta, mse = _channel_delta_and_mse(reference_for_metrics, decoded_for_metrics)
        peak = _peak_value(reference_for_metrics.mode)
        psnr = math.inf if mse == 0 else 20 * math.log10(peak / math.sqrt(mse))
        return PixelComparison(
            same_size=same_size,
            same_mode=same_mode,
            equal_pixels=max_delta == 0,
            max_channel_delta=max_delta,
            psnr=psnr,
            reference_mode=reference_mode,
            decoded_mode=decoded_mode,
            reference_size=reference_size,
            decoded_size=decoded_size,
        )


def compute_external_metric(metric: str, reference_path: Path, decoded_path: Path) -> float | None:
    binary = tool_path(metric)
    if binary is None:
        return None

    result = run_command([binary, str(reference_path), str(decoded_path)])
    if not result.ok:
        return None

    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", result.stdout)
    if not match:
        match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", result.stderr)
    return float(match.group(0)) if match else None


def write_visual_diff(reference_path: Path, decoded_path: Path, output_path: Path) -> bool:
    with Image.open(reference_path) as reference, Image.open(decoded_path) as decoded:
        if reference.size != decoded.size:
            return False
        reference, decoded = _coerce_for_visual_diff(reference, decoded)
        diff = ImageChops.difference(reference, decoded)
        if diff.mode not in {"RGB", "RGBA"}:
            diff = diff.convert("RGB")
        diff = ImageEnhance.Brightness(diff).enhance(8.0)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        diff.save(output_path)
        return True


def _coerce_for_metrics(reference: Image.Image, decoded: Image.Image) -> tuple[Image.Image, Image.Image]:
    reference = reference.copy()
    decoded = decoded.copy()
    if reference.mode == decoded.mode:
        return reference, decoded
    try:
        return reference, decoded.convert(reference.mode)
    except ValueError:
        mode = "RGBA" if _has_alpha(reference) or _has_alpha(decoded) else "RGB"
        return reference.convert(mode), decoded.convert(mode)


def _coerce_for_visual_diff(reference: Image.Image, decoded: Image.Image) -> tuple[Image.Image, Image.Image]:
    mode = "RGBA" if _has_alpha(reference) or _has_alpha(decoded) else "RGB"
    return reference.convert(mode), decoded.convert(mode)


def _channel_delta_and_mse(reference: Image.Image, decoded: Image.Image) -> tuple[int, float]:
    total = 0
    count = 0
    max_delta = 0
    for left_pixel, right_pixel in zip(reference.getdata(), decoded.getdata(), strict=True):
        for left, right in zip(_channels(left_pixel), _channels(right_pixel), strict=True):
            delta = abs(left - right)
            max_delta = max(max_delta, delta)
            total += delta * delta
            count += 1
    return max_delta, total / count if count else 0.0


def _channels(pixel: object) -> Iterator[int]:
    if isinstance(pixel, tuple):
        for channel in pixel:
            yield int(channel)
    else:
        yield int(pixel)


def _peak_value(mode: str) -> float:
    if mode in {"I;16", "I;16B", "I;16L"}:
        return 65535.0
    return 255.0


def _has_alpha(image: Image.Image) -> bool:
    return "A" in image.getbands() or image.mode in {"LA", "RGBA"}
