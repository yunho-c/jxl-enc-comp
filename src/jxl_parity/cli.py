from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Sequence

from .profiler import ProfileConfig, run_profile
from .runner import RunConfig, run_suite

VALID_MODES = {"lossless", "vardct"}
VALID_METRICS = {"psnr", "ssimulacra2", "butteraugli"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jxl-parity",
        description="Compare libjxl and jxl-encoder output parity across an image corpus.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run the parity suite.")
    run.add_argument(
        "--corpus",
        action="append",
        type=Path,
        default=[],
        help="Corpus directory or image file. May be supplied more than once.",
    )
    run.add_argument("--out", type=Path, default=Path("reports/parity"), help="Output directory.")
    run.add_argument("--cjxl", default="cjxl", help="libjxl encoder command.")
    run.add_argument("--djxl", default="djxl", help="libjxl decoder command.")
    run.add_argument("--jxl-encoder", default="cjxl-rs", help="jxl-encoder CLI command.")
    run.add_argument(
        "--modes",
        default="lossless,vardct",
        help="Comma-separated modes to run: lossless,vardct.",
    )
    run.add_argument(
        "--distances",
        default="1.0,2.0",
        help="Comma-separated Butteraugli distances for vardct mode.",
    )
    run.add_argument(
        "--efforts",
        default="7",
        help="Comma-separated effort values for both encoders.",
    )
    run.add_argument(
        "--max-images",
        type=int,
        default=None,
        help="Limit the number of discovered images for a smoke run.",
    )
    run.add_argument(
        "--metrics",
        default="psnr,ssimulacra2",
        help="Comma-separated metrics: psnr,ssimulacra2,butteraugli.",
    )
    run.add_argument(
        "--keep-work",
        action="store_true",
        help="Keep intermediate encoded and decoded files.",
    )

    profile = subparsers.add_parser("profile", help="Run profiling-oriented encoder sweeps.")
    profile.add_argument(
        "--corpus",
        action="append",
        type=Path,
        default=[],
        help="Corpus directory or image file. May be supplied more than once.",
    )
    profile.add_argument("--out", type=Path, default=Path("reports/profile"), help="Output directory.")
    profile.add_argument("--cjxl", default="cjxl", help="libjxl encoder command.")
    profile.add_argument("--jxl-encoder", default="cjxl-rs", help="jxl-encoder CLI command.")
    profile.add_argument(
        "--encoder",
        choices=("jxl-encoder", "libjxl", "both"),
        default="jxl-encoder",
        help="Encoder to profile.",
    )
    profile.add_argument(
        "--modes",
        default="lossless,vardct",
        help="Comma-separated modes to run: lossless,vardct.",
    )
    profile.add_argument(
        "--distances",
        default="1.0,2.0",
        help="Comma-separated Butteraugli distances for vardct mode.",
    )
    profile.add_argument(
        "--efforts",
        default="7",
        help="Comma-separated effort values.",
    )
    profile.add_argument(
        "--max-images",
        type=int,
        default=None,
        help="Limit the number of discovered images for a profiling smoke run.",
    )
    profile.add_argument(
        "--samples",
        type=int,
        default=1,
        help="Measured encode samples per image/settings case.",
    )
    profile.add_argument(
        "--warmups",
        type=int,
        default=0,
        help="Warmup encodes before each profiled case; excluded from aggregate timing.",
    )
    profile.add_argument(
        "--instrument-stages",
        action="store_true",
        help="Mark the run as intended for stage instrumentation and emit profiler guidance.",
    )
    profile.add_argument(
        "--keep-work",
        action="store_true",
        help="Keep intermediate reference PNGs and encoded files.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        modes = _parse_csv_choice(parser, "--modes", args.modes, VALID_MODES)
        distances = _parse_float_csv(parser, "--distances", args.distances, required=False, minimum=0.0)
        efforts = _parse_int_csv(parser, "--efforts", args.efforts, minimum=1)
        metrics = _parse_csv_choice(parser, "--metrics", args.metrics, VALID_METRICS, required=False)
        _validate_sweep(parser, modes, distances, args.max_images)
        config = RunConfig(
            corpus=args.corpus,
            out_dir=args.out,
            cjxl=args.cjxl,
            djxl=args.djxl,
            jxl_encoder=args.jxl_encoder,
            modes=modes,
            distances=distances,
            efforts=efforts,
            max_images=args.max_images,
            metrics=metrics,
            keep_work=args.keep_work,
        )
        try:
            summary = run_suite(config)
        except FileNotFoundError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(f"Wrote reports to {summary.out_dir}")
        print(
            f"cases={summary.total_cases} passed={summary.passed_cases} "
            f"failed={summary.failed_cases} skipped={summary.skipped_cases}"
        )
        return 1 if summary.failed_cases else 0

    if args.command == "profile":
        modes = _parse_csv_choice(parser, "--modes", args.modes, VALID_MODES)
        distances = _parse_float_csv(parser, "--distances", args.distances, required=False, minimum=0.0)
        efforts = _parse_int_csv(parser, "--efforts", args.efforts, minimum=1)
        _validate_sweep(parser, modes, distances, args.max_images)
        _validate_profile_counts(parser, args.samples, args.warmups)
        config = ProfileConfig(
            corpus=args.corpus,
            out_dir=args.out,
            cjxl=args.cjxl,
            jxl_encoder=args.jxl_encoder,
            encoder=args.encoder,
            modes=modes,
            distances=distances,
            efforts=efforts,
            max_images=args.max_images,
            keep_work=args.keep_work,
            instrument_stages=args.instrument_stages,
            samples=args.samples,
            warmups=args.warmups,
        )
        try:
            summary = run_profile(config)
        except FileNotFoundError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(f"Wrote profile artifacts to {summary.out_dir}")
        print(
            f"cases={summary.total_cases} completed={summary.completed_cases} "
            f"failed={summary.failed_cases} skipped={summary.skipped_cases}"
        )
        return 1 if summary.failed_cases else 0

    parser.error(f"unknown command: {args.command}")
    return 2


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_csv_choice(
    parser: argparse.ArgumentParser,
    option: str,
    value: str,
    allowed: set[str],
    *,
    required: bool = True,
) -> list[str]:
    items = _csv(value)
    if required and not items:
        parser.error(f"{option} must include at least one value")
    unknown = sorted(item for item in items if item not in allowed)
    if unknown:
        parser.error(
            f"{option} contains unsupported value(s): {', '.join(unknown)} "
            f"(supported: {', '.join(sorted(allowed))})"
        )
    return items


def _parse_float_csv(
    parser: argparse.ArgumentParser,
    option: str,
    value: str,
    *,
    required: bool = True,
    minimum: float | None = None,
) -> list[float]:
    items = _csv(value)
    if required and not items:
        parser.error(f"{option} must include at least one value")
    numbers: list[float] = []
    for item in items:
        try:
            number = float(item)
        except ValueError:
            parser.error(f"{option} contains a non-numeric value: {item}")
        if not math.isfinite(number):
            parser.error(f"{option} values must be finite numbers: {item}")
        if minimum is not None and number < minimum:
            parser.error(f"{option} values must be at least {minimum:g}: {item}")
        numbers.append(number)
    return numbers


def _parse_int_csv(
    parser: argparse.ArgumentParser,
    option: str,
    value: str,
    *,
    minimum: int | None = None,
) -> list[int]:
    items = _csv(value)
    if not items:
        parser.error(f"{option} must include at least one value")
    numbers: list[int] = []
    for item in items:
        try:
            number = int(item)
        except ValueError:
            parser.error(f"{option} contains a non-integer value: {item}")
        if minimum is not None and number < minimum:
            parser.error(f"{option} values must be at least {minimum}: {item}")
        numbers.append(number)
    return numbers


def _validate_sweep(
    parser: argparse.ArgumentParser,
    modes: list[str],
    distances: list[float],
    max_images: int | None,
) -> None:
    if "vardct" in modes and not distances:
        parser.error("--distances must include at least one value when vardct mode is enabled")
    if max_images is not None and max_images < 1:
        parser.error("--max-images must be at least 1")


def _validate_profile_counts(parser: argparse.ArgumentParser, samples: int, warmups: int) -> None:
    if samples < 1:
        parser.error("--samples must be at least 1")
    if warmups < 0:
        parser.error("--warmups must be at least 0")


if __name__ == "__main__":
    raise SystemExit(main())
