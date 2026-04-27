# jxl-enc-comp

`jxl-enc-comp` is a parity test harness for comparing `libjxl` (`cjxl`/`djxl`)
with the Rust `jxl-encoder` CLI (`cjxl-rs`).

The suite is intentionally output-oriented: it does not compare encoded `.jxl`
bytes. It encodes inputs with each encoder, decodes the outputs through
`djxl`, checks decoded dimensions/channel mode and lossless round trips,
records size/timing, computes optional quality metrics, and emits CSV, JSON,
Markdown, and HTML reports.

## Quick Start

```bash
python3 -m pip install -e .
jxl-parity run --out reports/parity
```

By default the runner looks for local sample corpora at:

- `~/GitHub/Kodak-Lossless-True-Color-Image-Suite`
- `~/GitHub/test_images`

It also auto-detects `cjxl`, `djxl`, `cjxl-rs`, `ssimulacra2`, and `butteraugli`
on `PATH`. Missing optional tools are reported in the output instead of causing
the whole run to fail.

Sweep arguments are validated before a run starts: unsupported modes/metrics,
empty effort lists, empty VarDCT distance lists, negative distances, and
non-positive `--max-images` values fail with a CLI error instead of producing an
empty or all-skipped report.

Install the Rust encoder CLI with:

```bash
cargo install jxl-encoder-cli
```

## Running Focused Sweeps

Use `--max-images` for a smoke run:

```bash
jxl-parity run --max-images 3 --modes lossless --efforts 1 --out reports/smoke
```

Run a lossy sweep with several distances and efforts:

```bash
jxl-parity run \
  --corpus ~/GitHub/Kodak-Lossless-True-Color-Image-Suite \
  --corpus ~/GitHub/test_images \
  --modes lossless,vardct \
  --distances 0.5,1.0,2.0,3.0 \
  --efforts 5,7,9 \
  --out reports/full
```

If your binaries are not on `PATH`, pass explicit commands:

```bash
jxl-parity run --cjxl /opt/homebrew/bin/cjxl --djxl /opt/homebrew/bin/djxl --jxl-encoder cjxl-rs
```

## What Gets Compared

Every input is prepared as a PNG pixel reference before encoding. PNG inputs in
direct pixel modes are copied directly, while palette/1-bit PNGs and other image
types are normalized through Pillow. This keeps the suite focused on
pixel-encoding parity and avoids mixing libjxl's JPEG transcoding path into
`jxl-encoder` comparisons.

Files with image-like extensions that Pillow cannot read are kept in the report
as `skipped` cases with an `unsupported input format` reason, rather than
aborting the whole corpus run.

For each image, mode, distance, effort, and encoder, the suite records:

- encode/decode success
- decoded dimensions, channel mode, and pixel comparison
- lossless pixel equality
- encoded bytes and bits per pixel
- encode/decode time
- PSNR
- optional SSIMULACRA2 and Butteraugli scores when tools are installed

Lossless cases fail on any decoded pixel mismatch. Lossy cases pass when encode
and decode succeed; quality and size are reported for comparison rather than
treated as bit-exact pass/fail criteria.

`paired_comparisons.csv` lines up libjxl and jxl-encoder rows for the same
image/settings and reports size, quality, and encode-time ratios.

## Report Artifacts

Each run writes:

- `summary.json`
- `summary.csv`
- `paired_comparisons.csv`
- `per_image_results.csv`
- `results.json`
- `corpus_manifest.csv`
- `feature_coverage.md`
- `report.html`
- `visual_diffs/` for lossless failures and low-quality lossy outliers

Use `--keep-work` to retain intermediate reference PNGs, `.jxl` files, and
decoded PNGs under `work/`.

## Profiling Sweeps

Use `jxl-parity profile` when you want profiling-oriented encode timings without
the decode and quality-metric pass:

```bash
jxl-parity profile \
  --encoder jxl-encoder \
  --instrument-stages \
  --max-images 5 \
  --corpus ~/GitHub/test_images \
  --modes lossless,vardct \
  --distances 1.0,2.0 \
  --efforts 5,7,9 \
  --out reports/profile
```

The profile command writes:

- `profile_summary.json`
- `profile_results.json`
- `profile_runs.csv`
- `stage_timing.json`
- `profiler_commands.md`

`stage_timing.json` records top-level encode wall time as `encode_total`. The
stock encoder CLIs do not expose internal JPEG XL stage timings, so use
`profiler_commands.md` to capture stack samples or flamegraphs for representative
cases and compare them with the parity report before treating a hot
`jxl-encoder` stage as a libjxl surrogate.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

The unit tests do not require `cjxl`, `djxl`, or `cjxl-rs`; external tool
integration is covered by smoke runs of `jxl-parity run`.
