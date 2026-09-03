# moss_td worker

Production offline adapter for MOSS-Transcribe-Diarize 0.9B. The isolated environment pins the
official implementation to a full Git commit and loads only a complete local model snapshot. A
`transcribe_batch` request clips the requested absolute-sample window, supplies language, hotword,
speaker-count, timestamp, and acoustic-event instructions, and returns absolute structural spans.

Returned text is explicitly tagged
`coarse_timeline_consensus_candidate_boundary_reference` and `adopted_as_final=false`; core may use
it as timing, speaker, consensus-candidate, and boundary evidence, never as an unconditional final
transcript. Generation runs off the event-loop thread and checks cancellation through the token
callback. Load and inference failures are converted to stable worker protocol errors.

The core project never imports this worker or its deep-learning dependencies.
