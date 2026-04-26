from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
    total_cases: int
    passed_cases: int
    failed_cases: int
    skipped_cases: int


def run_suite(config: RunConfig) -> RunSummary:
    config.out_dir.mkdir(parents=True, exist_ok=True)
    raise NotImplementedError("suite runner is not implemented yet")

