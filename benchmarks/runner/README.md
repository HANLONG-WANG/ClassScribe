# Offline runner

Generate one prediction JSONL row per gold item for each supported pinned model/revision through the
shared worker boundary, then run:

```bash
uv run python scripts/run_benchmark.py \
  --manifest "$XDG_DATA_HOME/classscribe/benchmarks/course-v1/gold.jsonl" \
  --manifest-version course-v1 \
  --predictions "$XDG_DATA_HOME/classscribe/benchmarks/course-v1/predictions.jsonl" \
  --parameters-json "$XDG_DATA_HOME/classscribe/benchmarks/course-v1/parameters.json" \
  --output "$XDG_DATA_HOME/classscribe/benchmarks/course-v1/report.json"
```

The runner sets all Hugging Face/Transformers/Datasets offline flags, records immutable model
revision, hardware and parameters, requires complete per-language/per-scenario coverage, fits on
`train`, selects calibration on `validation`, and reports final quality on `test`. The optional
`--starter-coverage` flag permits early 5-minute dataset development but its output is not valid for
release ranking.
