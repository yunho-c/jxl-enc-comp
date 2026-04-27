# Stage Profiling Assessment

## Short Answer

Yes. The viable shape is to keep `encode_total` as the outer wall-clock
reference, report fine-grained encoder spans as flat leaf stages, and treat the
older coarse names as grouping metadata rather than simultaneously measured
parent spans. That gives useful granularity without double-counting parent and
child timings in the current sidecar schema.

The profiling setup can report real `jxl-encoder` stage timings when it is run
with an instrumented `cjxl-rs` build that exposes `--stage-timing-json`. In that
case, `jxl-parity profile --instrument-stages` writes a per-sample sidecar path,
ingests the sidecar, and merges named stages into `profile_samples.json` and
`stage_timing.json`.

Without a compatible sidecar-capable binary, the harness still measures
whole-process encode time and shapes it as a single synthetic `encode_total`
stage. That fallback is enough to compare runtime behavior across corpus
images, modes, distances, efforts, warmups, and repeated samples, but it cannot
answer how much time went to color transform, block statistics, DCT/IDCT
candidate transforms, quantization scoring, filter simulation, or histogram
prepass.

## Current Boundary

`jxl-parity profile` invokes the encoder CLI as an external process and times
the command from Python. The outer timing boundary is around `subprocess.run`,
so the harness sees command arguments, exit status, stderr, output size, and
elapsed wall time. That timing is always preserved as `encode_total`.

When `--instrument-stages` is used with `jxl-encoder` and the selected
`cjxl-rs` supports `--stage-timing-json`, the harness also passes a sidecar path
to each warmup and measured encode. The Rust encoder records internal spans and
writes structured JSON; the harness reads that file into each sample and
aggregates measured samples into `stage_timing.json`.

The generated `stage_timing.json` is intentionally conservative:

- `stage_source`: `jxl_encoder_stage_sidecar` when sidecars are ingested,
  otherwise `wall_clock_encode_total`
- per case stage: `encode_total`, plus named stages when sidecars are ingested
- repeated samples: average/min/median/max/stdev are derived from measured process runs
- sidecar accounting: sidecar elapsed time, named-stage total, sidecar
  unattributed time, and harness unattributed time

The `--instrument-stages` flag does not modify the encoder binary. If the
selected `cjxl-rs` does not list `--stage-timing-json` in `--help`, the run
falls back to encode-total timing and emits profiler guidance.

Support for `--stage-timing-json` only means the CLI can write sidecars. Some
encoder paths can still emit no named stages; those runs remain
`wall_clock_encode_total` and the profile CLI reports that no named sidecar
stages were ingested. Use a VarDCT run to verify the instrumented fork's named
stages.

## What The Instrumented Rust Encoder Exposes

The instrumented `jxl-encoder` fork exposes timing data through `cjxl-rs
--stage-timing-json <file>` and currently reports these coarse stable stages:

- `color_xyb`
- `block_stats`
- `filter_simulation`
- `ac_strategy_search`
- `quant_scoring`
- `transform_quantize`
- `entropy_prepass`
- `bitstream_write`

Stock `cjxl-rs` builds still expose output size, mode, strategy counts,
gaborish, ANS, loop count, and pixel-domain-loss flags, but no timings. The
Python harness cannot infer named internal spans from those binaries alone.

## Granularity Model

Do not replace `encode_total`. It is the only timing that includes CLI startup,
I/O, normalization handoff, sidecar overhead, and any unattributed encoder work.

For internal spans, prefer a flat leaf-stage sidecar. A sidecar that records both
`ac_strategy_search` and nested `block_strategy`, `transform_selection`, and
`adaptive_quantization` as measured stages would make named-stage totals exceed
the instrumented work. If parent stages are needed later, add an explicit schema
field such as `stage_group` or `parent_stage` and teach the harness how to
aggregate it. Until then, keep parent groups as metadata only.

This means the existing coarse names can remain accepted for old instrumented
builds, while future builds can emit more granular leaf names. The harness
already ingests arbitrary stage strings, so this is mostly a Rust
instrumentation and documentation problem.

## VarDCT Stage Map

A practical VarDCT schema should use stable leaf stages and derive broader
groups for reporting:

| Requested area | Recommended leaf stage | Reporting group |
| --- | --- | --- |
| input conversion / pixel layout | `input_conversion`, `pixel_layout` | input_color |
| color transform | `color_transform` | input_color |
| LF image generation | `lf_image_generation` | vardct_frontend |
| block strategy / transform selection | `block_strategy`, `transform_selection` | vardct_frontend |
| adaptive quantization | `adaptive_quantization` | vardct_frontend |
| DCT / coefficient generation | `dct_coefficient_generation` | vardct_coefficients |
| chroma-from-luma | `chroma_from_luma` | vardct_coefficients |
| gaborish / filtering simulation | `gaborish_filtering` | vardct_coefficients |
| noise synthesis decisions | `noise_synthesis` | vardct_coefficients |
| Butteraugli-guided rate control | `butteraugli_rate_control` | vardct_frontend |
| coefficient tokenization | `coefficient_tokenization` | entropy |
| histogram construction | `histogram_construction` | entropy |
| histogram clustering | `histogram_clustering` | entropy |
| ANS/Huffman encoding | `ans_huffman_encoding` | entropy |
| bit writing | `bit_writing` | bitstream |
| container/metadata wrapping | `container_metadata` | bitstream |

## Modular Stage Map

The Modular lossless path should use a sibling leaf-stage set rather than force
the VarDCT stages onto a different pipeline:

| Requested area | Recommended leaf stage | Reporting group |
| --- | --- | --- |
| input conversion | `input_conversion` | input_color |
| color transform / reversible color transform | `color_transform`, `reversible_color_transform` | input_color |
| predictor selection | `predictor_selection` | modular_modeling |
| residual generation | `residual_generation` | modular_modeling |
| palette / local palette decisions | `palette_decisions` | modular_modeling |
| MA-tree / context modeling if present | `ma_tree_context_modeling` | modular_modeling |
| histogram construction | `histogram_construction` | entropy |
| histogram clustering | `histogram_clustering` | entropy |
| LZ77 search | `lz77_search` | entropy |
| ANS/Huffman encoding | `ans_huffman_encoding` | entropy |
| bit writing | `bit_writing` | bitstream |
| container/metadata wrapping | `container_metadata` | bitstream |

## Current Coarse Stage Map

For compatibility with already-instrumented builds, the existing coarse stages
map into the same reporting groups:

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

## Setup

Install the sidecar-capable fork over a stock CLI:

```bash
cargo install --path ~/GitHub/jxl-encoder/jxl-encoder-cli --force
cjxl-rs --help | grep -- --stage-timing-json
```

Then run a profiling smoke:

```bash
jxl-parity profile \
  --encoder jxl-encoder \
  --instrument-stages \
  --samples 3 \
  --warmups 1 \
  --max-images 1 \
  --corpus ~/GitHub/Kodak-Lossless-True-Color-Image-Suite \
  --modes vardct \
  --distances 1.0 \
  --efforts 7 \
  --out reports/profile-smoke
```

## Validation

Validate overhead and accounting by running before/after comparisons with
instrumentation disabled/enabled. Track `encode_total`, sidecar elapsed time,
named-stage total time, and unattributed time for each sample. The first target
should be stable stage proportions, not exact nanosecond accounting.

## Recommended Path

Start with `jxl-encoder` only. Instrumenting libjxl would require modifying C/C++ internals or relying on profiler symbol attribution, which is higher risk and harder to keep aligned with this Python harness.

Keep the harness behavior unchanged for stock binaries and use optional sidecar
ingestion for the instrumented Rust encoder. That preserves repeatable
corpus-level encode timings while allowing custom `cjxl-rs` builds to provide
real named-stage timings.
