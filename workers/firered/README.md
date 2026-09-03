# firered worker

Offline FireRedASR2-AED adapter for Chinese/English body transcription and FireRedPunc adapter for
strict insertion-only punctuation proposals. Each model loads only a complete local snapshot. ASR
returns upstream raw confidence plus absolute word/character sample ranges; punctuation preserves
the unpunctuated source separately so the core can enforce the character-invariance contract.
Japanese, automatic language mode, mixed batches, sampling, and unbounded output are rejected.

The core project must never import this directory or its future deep-learning dependencies.
