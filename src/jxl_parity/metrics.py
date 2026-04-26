from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

from .codecs import run_command, tool_path


@dataclass(frozen=True)
class PixelComparison:
    same_size: bool
    same_mode: bool
    equal_pixels: bool
    max_channel_delta: int | None
    psnr: float | None


def compare_pixels(reference_path: Path, decoded_path: Path) -> PixelComparison:
    with Image.open(reference_path) as reference, Image.open(decoded_path) as decoded:
        same_size = reference.size == decoded.size
        same_mode = reference.mode == decoded.mode
        if not same_size:
            return PixelComparison(False, same_mode, False, None, None)

        if reference.mode != decoded.mode:
            decoded = decoded.convert(reference.mode)

        diff = ImageChops.difference(reference, decoded)
        extrema = diff.getextrema()
        if extrema and isinstance(extrema[0], tuple):
            max_delta = max(channel[1] for channel in extrema)
        elif extrema:
            max_delta = int(extrema[1])
        else:
            max_delta = 0

        mse = _mse(reference, decoded)
        psnr = math.inf if mse == 0 else 20 * math.log10(255.0 / math.sqrt(mse))
        return PixelComparison(same_size, same_mode, max_delta == 0, max_delta, psnr)


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


def _mse(reference: Image.Image, decoded: Image.Image) -> float:
    ref_bytes = reference.tobytes()
    dec_bytes = decoded.tobytes()
    if len(ref_bytes) != len(dec_bytes):
        return math.inf
    if not ref_bytes:
        return 0.0
    total = 0
    for left, right in zip(ref_bytes, dec_bytes, strict=True):
        delta = left - right
        total += delta * delta
    return total / len(ref_bytes)

