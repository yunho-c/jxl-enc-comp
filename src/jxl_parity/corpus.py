from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps


DEFAULT_CORPORA = (
    Path("~/GitHub/Kodak-Lossless-True-Color-Image-Suite").expanduser(),
    Path("~/GitHub/test_images").expanduser(),
)

IMAGE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".pam",
    ".pgm",
    ".png",
    ".pnm",
    ".ppm",
    ".tif",
    ".tiff",
    ".webp",
}


@dataclass(frozen=True)
class ImageRecord:
    image_id: str
    source_path: Path
    reference_path: Path
    width: int
    height: int
    mode: str
    source_format: str
    has_alpha: bool
    bit_depth: int | None

    @property
    def megapixels(self) -> float:
        return (self.width * self.height) / 1_000_000


def discover_images(paths: list[Path], work_dir: Path, max_images: int | None = None) -> list[ImageRecord]:
    inputs = paths or [path for path in DEFAULT_CORPORA if path.exists()]
    if not inputs:
        raise FileNotFoundError(
            "no corpus paths supplied and no default corpora were found under ~/GitHub"
        )

    candidates: list[Path] = []
    for input_path in inputs:
        expanded = input_path.expanduser()
        if expanded.is_file() and _is_image(expanded):
            candidates.append(expanded)
        elif expanded.is_dir():
            candidates.extend(
                path
                for path in expanded.rglob("*")
                if path.is_file() and not _is_hidden(path) and _is_image(path)
            )

    candidates = sorted(dict.fromkeys(candidates))
    if max_images is not None:
        candidates = candidates[:max_images]
    if not candidates:
        searched = ", ".join(str(path.expanduser()) for path in inputs)
        raise FileNotFoundError(f"no image files found in corpus paths: {searched}")

    reference_dir = work_dir / "reference"
    reference_dir.mkdir(parents=True, exist_ok=True)
    return [_prepare_reference(path, reference_dir) for path in candidates]


def _prepare_reference(source_path: Path, reference_dir: Path) -> ImageRecord:
    image_id = _image_id(source_path)
    reference_path = reference_dir / f"{image_id}.png"

    with Image.open(source_path) as image:
        source_format = image.format or source_path.suffix.lstrip(".").upper()
        image = ImageOps.exif_transpose(image)
        mode = image.mode
        has_alpha = _has_alpha(image)
        bit_depth = _bit_depth(image)

        normalized = _normalize_for_png(image)
        if source_path.suffix.lower() == ".png" and normalized.mode == image.mode:
            shutil.copyfile(source_path, reference_path)
        else:
            normalized.save(reference_path)
            mode = normalized.mode
            has_alpha = _has_alpha(normalized)
            bit_depth = _bit_depth(normalized)

    with Image.open(reference_path) as reference:
        width, height = reference.size
        mode = reference.mode
        has_alpha = _has_alpha(reference)
        bit_depth = _bit_depth(reference)

    return ImageRecord(
        image_id=image_id,
        source_path=source_path,
        reference_path=reference_path,
        width=width,
        height=height,
        mode=mode,
        source_format=source_format,
        has_alpha=has_alpha,
        bit_depth=bit_depth,
    )


def _normalize_for_png(image: Image.Image) -> Image.Image:
    if image.mode in {"L", "LA", "RGB", "RGBA", "I;16", "I;16B", "I;16L"}:
        return image.copy()
    if image.mode == "1":
        return image.convert("L")
    if image.mode == "P" and ("transparency" in image.info):
        return image.convert("RGBA")
    if image.mode == "P":
        return image.convert("RGB")
    if _has_alpha(image):
        return image.convert("RGBA")
    return image.convert("RGB")


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def _is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def _image_id(path: Path) -> str:
    stem = "-".join(part for part in path.with_suffix("").parts[-3:] if part)
    slug = "".join(char.lower() if char.isalnum() else "-" for char in stem).strip("-")
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:10]
    return f"{slug[:80]}-{digest}"


def _has_alpha(image: Image.Image) -> bool:
    return "A" in image.getbands() or image.mode in {"LA", "RGBA"}


def _bit_depth(image: Image.Image) -> int | None:
    if image.mode in {"1"}:
        return 1
    if image.mode in {"I;16", "I;16B", "I;16L"}:
        return 16
    if image.mode in {"L", "LA", "P", "RGB", "RGBA"}:
        return 8
    return None
