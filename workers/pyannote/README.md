# pyannote worker

Production offline adapter for pyannote Community-1. It loads only a complete local snapshot and
accepts an exact speaker count or the configured minimum/maximum bounds. The response preserves
both regular and exclusive diarization, derives true multi-speaker overlap evidence, and returns
speaker embeddings with up to three high-quality support spans.

Embedding support is cut only from non-overlap audio. Detected overlap is subtracted rather than
silently labelling the entire region as one exclusive speaker; if no safe support remains, the
speaker has no reliable embedding and core creates a new job-local identity instead of forcing a
match. Inference runs off the event-loop thread and remains isolated behind the worker protocol.

The core project never imports this worker or its deep-learning dependencies.
