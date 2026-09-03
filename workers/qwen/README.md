# qwen worker

Offline Qwen3-ASR and Qwen3-ForcedAligner adapter using the official pinned `qwen-asr` package.
Forced alignment is allowed only for a manually selected language and a short segment that passed
the core acoustic/text safety gate; its token times are converted back to absolute sample indices
and it never changes the supplied final text. Batch ASR inference is fixed to
one item, accepts pronunciation context only as bias, and always passes Chinese/Japanese/English by
name. The plan's `1.7B-JA` experiment is an explicitly enabled forced-Japanese route over the same
official 1.7B checkpoint; no fictitious separate model identity is introduced.

The core project must never import this directory or its future deep-learning dependencies.
