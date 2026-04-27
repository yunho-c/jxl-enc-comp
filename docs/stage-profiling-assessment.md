# Stage Profiling Assessment

## Short Answer

The current profiling setup does not provide real time attribution for internal JPEG XL stages. It can measure whole-process encode time for `cjxl-rs` and shape that as a single synthetic `encode_total` stage in `stage_timing.json`.

That is enough to compare encoder/runtime behavior across corpus images, modes, distances, efforts, warmups, and repeated samples. It is not enough to answer how much time went to color transform, block statistics, DCT/IDCT candidate transforms, quantization scoring, filter simulation, or histogram prepass.

## Current Boundary

`jxl-parity profile` currently invokes the encoder CLI as an external process and times the command from Python. The timing boundary is around `subprocess.run`, so the harness sees command arguments, exit status, stderr, output size, and elapsed wall time. It does not run inside the Rust encoder process and cannot observe internal function spans.

The generated `stage_timing.json` is intentionally conservative:

- `stage_source`: `wall_clock_encode_total`
- per case stage: `encode_total`
- repeated samples: average/min/median/max/stdev are derived from measured process runs

The `--instrument-stages` flag marks the run as intended for stage work and emits profiler guidance. It does not instrument the stock `cjxl-rs` binary.

## What The Rust Encoder Exposes

The installed `jxl-encoder` crate has useful code boundaries for this work, but it does not expose timing data through the CLI:

- Public API stats expose output size, mode, strategy counts, gaborish, ANS, loop count, and pixel-domain-loss flags, but no timings.
- The VarDCT path has clear sections for XYB conversion, noise/denoise, patches/splines, chromacity stats, adaptive quant field, gaborish inverse, CfL, AC strategy search, quantization loops, transform/quantize, EPF sharpness, and entropy coding.
- Entropy coding has explicit histogram, clustering, ANS, and Huffman modules.
- Existing bitstream tracing is about written fields and bit positions, not elapsed time.

So this is feasible in `jxl-encoder`, but not from the current Python harness alone.

## Stage Map

A practical first-pass stage schema should be small and stable:

| Requested area | Proposed stage name | Likely Rust boundary |
| --- | --- | --- |
| color transform | `color_xyb` | input conversion into padded XYB planes |
| block statistics | `block_stats` | chromacity stats, adaptive quant field, masking |
| DCT/IDCT candidate transforms | `ac_strategy_search` | AC strategy computation and candidate scoring |
| quantization scoring | `quant_scoring` | quant-field adjustment and optional butteraugli/SSIM2/zensim loops |
| filter simulation | `filter_simulation` | gaborish inverse, EPF sharpness, optional noise/denoise |
| histogram prepass | `entropy_prepass` | token collection, histogram build, clustering, code construction |
| transform and quantize | `transform_quantize` | final DCT and coefficient quantization |
| bitstream write | `bitstream_write` | DC/AC global and group section writing |

Some requested labels overlap in the implementation. For example, AC strategy search may include DCT/IDCT candidate work and entropy estimates; quantization loops may re-run reconstruction and metric calculations. The schema should document inclusive timing rules so totals remain interpretable.

## What It Would Take

1. Add a low-overhead timer to a fork or patch of `jxl-encoder`.

   Use RAII spans in Rust around the selected boundaries. Accumulate durations by stable string or enum stage names. For parallel work, record either wall-clock span time, summed worker CPU time, or both; wall-clock is easier to compare with the harness total.

2. Expose timing output from `cjxl-rs`.

   Add a CLI option such as `--stage-timing-json <path>` or `--stage-timing-stderr`. A sidecar JSON file is less fragile than parsing normal stderr.

3. Teach this harness to ingest sidecar timing.

   Add a `--stage-timing-command` or jxl-encoder-specific env/flag injection, pass a per-sample sidecar path, read it after each encode, and merge it into `profile_samples.json` plus `stage_timing.json`. Keep `encode_total` as the outer timing so overhead and missing spans are visible.

4. Validate overhead and accounting.

   Run before/after comparisons with instrumentation disabled/enabled. Track `unattributed = encode_total - sum(stage_wall_seconds)` for each sample. The first target should be stable stage proportions, not exact nanosecond accounting.

## Recommended Path

Start with `jxl-encoder` only. Instrumenting libjxl would require modifying C/C++ internals or relying on profiler symbol attribution, which is higher risk and harder to keep aligned with this Python harness.

For the first implementation, keep the harness behavior unchanged for stock binaries and add optional sidecar ingestion. That preserves the current repeatable corpus-level encode timings while allowing custom `cjxl-rs` builds to provide real named-stage timings.
