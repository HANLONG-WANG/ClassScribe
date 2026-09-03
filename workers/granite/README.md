# granite worker

Offline Granite Speech 4.1 2B adapter for Japanese and English. It uses the official English
`transcribe the speech to text.` task prompt for non-English ASR and appends canonical term plus
reading keyword hints. Decode remains single-item, greedy, seeded, and duration-bounded.

The core project must never import this directory or its future deep-learning dependencies.
