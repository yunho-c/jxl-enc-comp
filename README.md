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

From a checkout, the same suite can be run through `just` without installing
the console script first:

```bash
just parity
```

Additional `jxl-parity run` flags can be passed after the recipe name, for
example `just parity --max-images 3 --modes lossless`.

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

Named stage profiling requires a fork or local build of `cjxl-rs` that supports
`--stage-timing-json`. If you have the instrumented `jxl-encoder` checkout next
to this repo, install it over any stock `cjxl-rs` with:

```bash
cargo install --path ~/GitHub/jxl-encoder/jxl-encoder-cli --force
cjxl-rs --help | grep -- --stage-timing-json
```

## Running Focused Sweeps

Use `--max-images` for a smoke run:

```bash
jxl-parity run --max-images 3 --modes lossless --efforts 1 --out reports/smoke
```

The same smoke run is available as:

```bash
just parity-smoke
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
image/settings and reports size, quality, and encode-time ratios. The HTML
report also includes a paired encode-time chart that plots both encoders against
image size, so runtime comparability is visible without opening the CSV.

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
  --samples 5 \
  --warmups 1 \
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
- `profile_runs.json`
- `profile_runs.csv`
- `profile_samples.json`
- `profile_samples.csv`
- `profile_stage_summary.csv`
- `stage_timing.json`
- `profile_report.md`
- `profile_plots/`
- `profiler_commands.md`

`profile_runs.csv` and `profile_runs.json` have one aggregate row per
image/settings/encoder case. `profile_samples.csv` and `profile_samples.json`
have one row per warmup and measured encode invocation, so you can inspect
variance before comparing encoders. Warmups are recorded there but excluded from
aggregate timing. `stage_timing.json` records top-level encode wall time as
`encode_total`; when multiple samples are used, `encode_seconds` is the
measured-sample average and min/median/max/stdev are included alongside it.
`profile_stage_summary.csv` aggregates encode-total and named-stage timings by
encoder, mode, distance, and effort, with reporting-group metadata included for
each stage. `profile_report.md` embeds those rows as tables and writes SVG plots
under `profile_plots/` for quick visual comparison.

From a checkout, profile sweeps can also be run through `just`:

```bash
just profile --encoder jxl-encoder --instrument-stages --max-images 1
just profile-smoke
```

When `--instrument-stages` is used with a compatible `cjxl-rs` build that
supports `--stage-timing-json`, the harness writes per-sample sidecars and
merges the named Rust encoder stages into `profile_samples.json` and
`stage_timing.json`. `stage_timing.json` also summarizes sidecar accounting per
case, including named-stage total time and unattributed time. `encode_total`
remains the outer process wall-clock timing so stage overhead and unattributed
setup or I/O time stay visible. Known sidecar stage names are tagged with a
`stage_group` such as `input_color`, `vardct_frontend`, `entropy`, or
`bitstream`; unknown names are preserved with `stage_group: custom`.

Some encoder paths may accept the sidecar flag but emit no named stages. In that
case the CLI reports `stage_timing=encode_total only (no named sidecar stages
ingested)` and `stage_timing.json` keeps `stage_source:
wall_clock_encode_total`. Use a VarDCT smoke when verifying the current
instrumented fork's named-stage path.

Stock encoder CLIs do not expose internal JPEG XL stage timings. If
`cjxl-rs --help` does not list `--stage-timing-json`, the harness keeps the
encode-total behavior for that binary even when `--instrument-stages` is passed.
Use `profiler_commands.md` to capture stack samples or flamegraphs for
representative cases. Pass `--keep-work` when you want the exact normalized
input files and recorded commands to remain usable after the profile run. See
`docs/stage-profiling-assessment.md` for the detailed feasibility answer.

## Flamegraph Entrypoint

Use `jxl-parity flamegraph` when you want one normalized encoder invocation
wrapped directly by `flamegraph`:

```bash
jxl-parity flamegraph \
  --corpus ~/GitHub/test_images \
  --encoder jxl-encoder \
  --mode vardct \
  --distance 1.0 \
  --effort 7 \
  --out reports/flamegraph
```

The command writes `flamegraph.svg`, the exact encoder and profiler commands,
`run_flamegraph.sh`, `flamegraph_summary.json`, and the normalized input/output
under `work/`. On macOS, if the installed `flamegraph` tool records successfully
but fails while collapsing `xctrace` XML, the entrypoint falls back to exporting
the time-profile XML directly and writes `folded_stacks.txt` plus
`xctrace_*_command.txt` diagnostics. Use `--dry-run` to prepare artifacts
without executing the profiler. From a checkout:

```bash
just flamegraph --dry-run --corpus ~/GitHub/test_images
```

For clearer Rust stacks, rebuild the target encoder with debug symbols and frame
pointers before running the entrypoint, for example:

```bash
RUSTFLAGS="-C force-frame-pointers=yes -C debuginfo=2" cargo build --release
```

## Tests

```bash
just test
```

Without `just`, run the underlying command directly:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

The unit tests do not require `cjxl`, `djxl`, or `cjxl-rs`; external tool
integration is covered by smoke runs of `jxl-parity run`.
