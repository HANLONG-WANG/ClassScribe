# moss_en worker

Offline MOSS Transcribe Preview 2B primary English adapter using the checkpoint's local dynamic
processor, mel frontend, chat template, and greedy decode. It also retains the disabled Whisper Tiny
CPU reference path used only for real-model RPC lifecycle smoke testing. MOSS Preview has no
supported terminology channel, so hints are audited but never injected through an invented API.

The core project must never import this directory or its future deep-learning dependencies.
