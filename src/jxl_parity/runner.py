from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .codecs import decode, encode, tool_path
from .corpus import ImageRecord, discover_images
from .metrics import compare_pixels, compute_external_metric, write_visual_diff
from .reports import (
    write_corpus_manifest,
    write_csv,
    write_feature_coverage,
    write_html,
    write_json,
    write_summary_csv,
)


@dataclass(frozen=True)
class RunConfig:
    corpus: list[Path]
    out_dir: Path
    cjxl: str
    djxl: str
    jxl_encoder: str
    modes: list[str]
    distances: list[float]
    efforts: list[int]
    max_images: int | None
    metrics: list[str]
    keep_work: bool


@dataclass(frozen=True)
class RunSummary:
    out_dir: Path
    images: int
    total_cases: int
    passed_cases: int
    failed_cases: int
    skipped_cases: int
    tool_status: dict[str, bool]


@dataclass
class CaseResult:
    image_id: str
    source_path: str
    encoder: str
    mode: str
    effort: int
    distance: float | None
    status: str
    reason: str
    width: int
    height: int
    megapixels: float
    source_format: str
    image_mode: str
    has_alpha: bool
    bit_depth: int | None
    encoded_path: str | None = None
    decoded_path: str | None = None
    encoded_bytes: int | None = None
    bits_per_pixel: float | None = None
    encode_seconds: float | None = None
    decode_seconds: float | None = None
    psnr: float | None = None
    ssimulacra2: float | None = None
    butteraugli: float | None = None
    equal_pixels: bool | None = None
    max_channel_delta: int | None = None
    visual_diff_path: str | None = None
    command: str | None = None
    stderr: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def run_suite(config: RunConfig) -> RunSummary:
    out_dir = config.out_dir
    work_dir = out_dir / "work"
    encoded_dir = work_dir / "encoded"
    decoded_dir = work_dir / "decoded"
    diff_dir = out_dir / "visual_diffs"
    for directory in (out_dir, work_dir, encoded_dir, decoded_dir, diff_dir):
        directory.mkdir(parents=True, exist_ok=True)

    tool_status = {
        "cjxl": tool_path(config.cjxl) is not None,
        "djxl": tool_path(config.djxl) is not None,
        "jxl_encoder": tool_path(config.jxl_encoder) is not None,
        "ssimulacra2": tool_path("ssimulacra2") is not None,
        "butteraugli": tool_path("butteraugli") is not None,
    }

    images = discover_images(config.corpus, work_dir, config.max_images)
    results: list[CaseResult] = []

    for image in images:
        for mode in config.modes:
            distances = [None] if mode == "lossless" else config.distances
            for effort in config.efforts:
                for distance in distances:
                    results.append(
                        _run_case(
                            config=config,
                            image=image,
                            encoder_name="libjxl",
                            encoder_command=config.cjxl,
                            encoder_available=tool_status["cjxl"],
                            djxl_available=tool_status["djxl"],
                            mode=mode,
                            effort=effort,
                            distance=distance,
                            encoded_dir=encoded_dir,
                            decoded_dir=decoded_dir,
                            diff_dir=diff_dir,
                        )
                    )
                    results.append(
                        _run_case(
                            config=config,
                            image=image,
                            encoder_name="jxl-encoder",
                            encoder_command=config.jxl_encoder,
                            encoder_available=tool_status["jxl_encoder"],
                            djxl_available=tool_status["djxl"],
                            mode=mode,
                            effort=effort,
                            distance=distance,
                            encoded_dir=encoded_dir,
                            decoded_dir=decoded_dir,
                            diff_dir=diff_dir,
                        )
                    )

    passed = sum(result.status == "passed" for result in results)
    failed = sum(result.status == "failed" for result in results)
    skipped = sum(result.status == "skipped" for result in results)
    summary = RunSummary(
        out_dir=out_dir,
        images=len(images),
        total_cases=len(results),
        passed_cases=passed,
        failed_cases=failed,
        skipped_cases=skipped,
        tool_status=tool_status,
    )

    row_dicts = [_flatten_result(result) for result in results]
    write_json(out_dir / "summary.json", asdict(summary))
    write_json(out_dir / "results.json", [asdict(result) for result in results])
    write_summary_csv(out_dir / "summary.csv", row_dicts)
    write_corpus_manifest(out_dir / "corpus_manifest.csv", row_dicts)
    write_csv(out_dir / "per_image_results.csv", row_dicts)
    write_feature_coverage(out_dir / "feature_coverage.md", row_dicts, tool_status)
    write_html(out_dir / "report.html", summary, row_dicts)

    if not config.keep_work:
        shutil.rmtree(work_dir, ignore_errors=True)

    return summary


def _run_case(
    *,
    config: RunConfig,
    image: ImageRecord,
    encoder_name: str,
    encoder_command: str,
    encoder_available: bool,
    djxl_available: bool,
    mode: str,
    effort: int,
    distance: float | None,
    encoded_dir: Path,
    decoded_dir: Path,
    diff_dir: Path,
) -> CaseResult:
    result = _base_result(image, encoder_name, mode, effort, distance)
    if mode not in {"lossless", "vardct"}:
        result.status = "skipped"
        result.reason = f"unsupported mode: {mode}"
        return result
    if not encoder_available:
        result.status = "skipped"
        result.reason = f"encoder command not found: {encoder_command}"
        return result
    if not djxl_available:
        result.status = "skipped"
        result.reason = f"decoder command not found: {config.djxl}"
        return result

    case_id = _case_id(image, encoder_name, mode, effort, distance)
    encoded_path = encoded_dir / f"{case_id}.jxl"
    decoded_path = decoded_dir / f"{case_id}.png"
    result.encoded_path = str(encoded_path)
    result.decoded_path = str(decoded_path)

    encode_result = encode(
        encoder=encoder_name,
        command=encoder_command,
        input_path=image.reference_path,
        output_path=encoded_path,
        mode=mode,
        effort=effort,
        distance=distance,
    )
    result.encode_seconds = encode_result.elapsed_seconds
    result.command = encode_result.command_text
    if not encode_result.ok:
        result.status = "failed"
        result.reason = "encode failed"
        result.stderr = _short_error(encode_result.stderr)
        return result

    result.encoded_bytes = encoded_path.stat().st_size
    result.bits_per_pixel = (result.encoded_bytes * 8) / (image.width * image.height)

    decode_result = decode(config.djxl, encoded_path, decoded_path)
    result.decode_seconds = decode_result.elapsed_seconds
    if not decode_result.ok:
        result.status = "failed"
        result.reason = "decode failed"
        result.stderr = _short_error(decode_result.stderr)
        return result

    pixel_comparison = compare_pixels(image.reference_path, decoded_path)
    result.psnr = pixel_comparison.psnr
    result.equal_pixels = pixel_comparison.equal_pixels
    result.max_channel_delta = pixel_comparison.max_channel_delta

    if mode == "lossless" and not pixel_comparison.equal_pixels:
        result.status = "failed"
        result.reason = "lossless pixel mismatch"
    else:
        result.status = "passed"
        result.reason = "ok"

    if mode != "lossless":
        if "ssimulacra2" in config.metrics:
            result.ssimulacra2 = compute_external_metric("ssimulacra2", image.reference_path, decoded_path)
        if "butteraugli" in config.metrics:
            result.butteraugli = compute_external_metric("butteraugli", image.reference_path, decoded_path)

    if _needs_visual_diff(result):
        diff_path = diff_dir / f"{case_id}.png"
        if write_visual_diff(image.reference_path, decoded_path, diff_path):
            result.visual_diff_path = str(diff_path)

    return result


def _base_result(
    image: ImageRecord,
    encoder: str,
    mode: str,
    effort: int,
    distance: float | None,
) -> CaseResult:
    return CaseResult(
        image_id=image.image_id,
        source_path=str(image.source_path),
        encoder=encoder,
        mode=mode,
        effort=effort,
        distance=distance,
        status="pending",
        reason="",
        width=image.width,
        height=image.height,
        megapixels=image.megapixels,
        source_format=image.source_format,
        image_mode=image.mode,
        has_alpha=image.has_alpha,
        bit_depth=image.bit_depth,
    )


def _case_id(
    image: ImageRecord,
    encoder: str,
    mode: str,
    effort: int,
    distance: float | None,
) -> str:
    quality = "lossless" if distance is None else f"d{distance:g}".replace(".", "p")
    return f"{image.image_id}-{encoder}-{mode}-{quality}-e{effort}".replace("/", "-")


def _short_error(value: str) -> str:
    return value.strip()[-4000:]


def _flatten_result(result: CaseResult) -> dict[str, object]:
    row = asdict(result)
    row.pop("extra", None)
    return row


def _needs_visual_diff(result: CaseResult) -> bool:
    if result.status == "failed":
        return True
    if result.mode == "vardct":
        if result.ssimulacra2 is not None and result.ssimulacra2 < 70:
            return True
        if result.psnr is not None and result.psnr < 30:
            return True
    return False
