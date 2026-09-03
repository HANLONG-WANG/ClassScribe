# Private gold manifest placement

Do not commit classroom recordings, dictation audio, or corrected transcripts. Put the authoritative
JSONL manifest and audio below `${XDG_DATA_HOME}/classscribe/benchmarks/<dataset>/`; audio paths in
the manifest are relative to the manifest directory.

Each JSONL line follows `config/schema/benchmark-gold-item.v1.schema.json` and uses absolute 16 kHz
sample indices. Keep train, validation, and test speakers/recordings disjoint. A production gate
requires, for each of `zh`, `ja`, and `en`, at least 30 minutes of classroom audio, 50 IBus items,
one IBus item longer than 60 seconds, and non-empty train/validation/test splits.

Example shape (documentation only; this is not gold evidence):

```json
{"item_id":"ja-class-001","audio":"ja/class01.wav","scenario":"classroom","split":"train","language":"ja","start_sample":0,"end_sample":294400,"speaker":"teacher","text":"今日は人工知能について説明します。","terms":["人工知能"],"sentence_boundaries":[294400],"tags":["clear_lecture","terminology"]}
```

The runner refuses missing/symlinked audio, duplicate IDs, incomplete prediction coverage, missing
model revisions/confidence, split leakage by omission, and an incomplete production coverage gate.
