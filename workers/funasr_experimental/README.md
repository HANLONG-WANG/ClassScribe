# funasr_experimental worker

Explicitly gated Fun-ASR-Nano 2512 expert adapter using the upstream Transformers implementation.
It is disabled in the registry, requires both the expert role and an explicit enable flag, and
rejects output that fails its mandatory repeated-token loop check before returning a candidate.

The core project must never import this directory or its future deep-learning dependencies.
