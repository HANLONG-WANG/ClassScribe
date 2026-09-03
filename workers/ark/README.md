# ark worker

Offline ARK-ASR-3B review adapter for Chinese, Japanese, and English. It uses the official processor
and special-token mask, forces a non-translation language instruction, and always performs one
duration-bounded greedy decode from a canonical WAV clip.

The core project must never import this directory or its future deep-learning dependencies.
