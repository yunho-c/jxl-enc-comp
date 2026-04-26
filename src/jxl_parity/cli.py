from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .profiler import ProfileConfig, run_profile
from .runner import RunConfig, run_suite


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
        config = RunConfig(
            corpus=args.corpus,
            out_dir=args.out,
            cjxl=args.cjxl,
            djxl=args.djxl,
            jxl_encoder=args.jxl_encoder,
            modes=_csv(args.modes),
            distances=[float(value) for value in _csv(args.distances)],
            efforts=[int(value) for value in _csv(args.efforts)],
            max_images=args.max_images,
            metrics=_csv(args.metrics),
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
        config = ProfileConfig(
            corpus=args.corpus,
            out_dir=args.out,
            cjxl=args.cjxl,
            jxl_encoder=args.jxl_encoder,
            encoder=args.encoder,
            modes=_csv(args.modes),
            distances=[float(value) for value in _csv(args.distances)],
            efforts=[int(value) for value in _csv(args.efforts)],
            max_images=args.max_images,
            keep_work=args.keep_work,
            instrument_stages=args.instrument_stages,
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


if __name__ == "__main__":
    raise SystemExit(main())
