from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from classscribe.classroom import ProductionStageRunner
from classscribe.config import load_config
from classscribe.contracts import LanguageMode
from classscribe.db.models import (
    DecisionEvent,
    Job,
    JobCheckpoint,
    Recording,
    SpeechRegion,
    TranscriptSegment,
)
from classscribe.models import ModelManager, SandboxedModelInvoker, load_registry
from classscribe.paths import AppPaths
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker


def setup_segment(session: Session) -> tuple[Job, TranscriptSegment, JobCheckpoint]:
    recording = Recording(
        source_name="a",
        source_sha256="a" * 64,
        source_path="a",
        duration_samples=32000,
        sample_rate=16000,
        channels=1,
    )
    job = Job(
        recording=recording, language_mode=LanguageMode.ENGLISH, profile_id="en", options_json={}
    )
    segment = TranscriptSegment(
        job=job,
        start_sample=0,
        end_sample=32000,
        language=LanguageMode.ENGLISH,
        raw_text="Hello world",
        faithful_text="Hello world",
        smart_corrected_text="Hello world",
    )
    session.add(segment)
    session.flush()
    session.add(
        SpeechRegion(
            job_id=job.id,
            start_sample=0,
            end_sample=32000,
            vad_score=1,
            acoustic_class="speech",
            source="test",
        )
    )
    session.flush()
    return job, segment, JobCheckpoint(segment_id=segment.id)


def make_runner(tmp_path: Path, invoke: Any) -> ProductionStageRunner:
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()

    class Installed:
        def resolve_for_runtime(self, model_id: str) -> Path:
            return tmp_path / model_id

    return ProductionStageRunner(
        paths,
        load_config(environment={}),
        load_registry(Path("config/model-registry.v1.yaml")),
        cast(ModelManager, Installed()),
        cast(SandboxedModelInvoker, invoke),
    )


@pytest.mark.parametrize("failure", ["timeout", "invalid"])
def test_production_alignment_failure_falls_back(
    database: tuple[Engine, sessionmaker[Session], Path], tmp_path: Path, failure: str
) -> None:
    async def invoke(entry: Any, request: Any) -> Any:
        if failure == "timeout":
            raise TimeoutError("worker timed out")
        from classscribe_protocol import RPCResponse

        return RPCResponse(request.request_id, request.job_id, True, entry.id, entry.revision)

    runner = make_runner(tmp_path, invoke)
    with database[1].begin() as session:
        job, segment, checkpoint = setup_segment(session)
        runner._forced_alignment(session, job, checkpoint)
        session.flush()
        event = session.scalar(select(DecisionEvent).where(DecisionEvent.segment_id == segment.id))
        assert event is not None and event.output_json["coarse_timing"] is True
        assert any(
            reason.startswith("forced_aligner_failed:")
            for reason in event.output_json["fallback_reasons"]
        )


@pytest.mark.parametrize("source", ["native", "moss"])
def test_production_uses_matching_existing_timing_before_aligner(
    database: tuple[Engine, sessionmaker[Session], Path], tmp_path: Path, source: str
) -> None:
    from classscribe.db.models import ASRCandidate, StructureSegmentRecord, TokenSpan

    async def invoke(*args: Any) -> Any:
        pytest.fail("existing timing must avoid forced alignment")

    runner = make_runner(tmp_path, invoke)
    with database[1].begin() as session:
        job, segment, checkpoint = setup_segment(session)
        if source == "native":
            candidate = ASRCandidate(
                segment_id=segment.id,
                model_id="test",
                model_revision="a" * 40,
                raw_text="Hello world",
                normalized_text="Hello world",
                is_valid=True,
            )
            session.add(candidate)
            session.flush()
            for word, start, end in (("Hello", 100, 16000), ("world", 16000, 31900)):
                session.add(
                    TokenSpan(
                        segment_id=segment.id,
                        candidate_id=candidate.id,
                        token=word,
                        normalized_token=word,
                        start_sample=start,
                        end_sample=end,
                        provenance_json={"native_model_time": True},
                    )
                )
        else:
            session.add(
                StructureSegmentRecord(
                    job_id=job.id,
                    window_ordinal=0,
                    start_sample=100,
                    end_sample=31900,
                    coarse_text="Hello world",
                    source_model="moss_td_0_9b",
                    source_revision="a" * 40,
                    selection_score=0.95,
                    fallback=False,
                    overlap=False,
                    exclusive=True,
                    acoustic_events_json=[],
                    text_role="coarse_timeline_consensus_candidate_boundary_reference",
                )
            )
        session.flush()
        runner._forced_alignment(session, job, checkpoint)
        session.flush()
        event = session.scalar(select(DecisionEvent).where(DecisionEvent.segment_id == segment.id))
        assert event is not None and event.output_json["coarse_timing"] is False
        assert event.output_json["source"] == (
            "reliable_native_word_timestamps"
            if source == "native"
            else "moss_native_structure_timing"
        )


@pytest.mark.parametrize("faithful,smart", [(True, False), (False, True), (True, True)])
def test_automatic_export_honors_layers_and_speakers(
    database: tuple[Engine, sessionmaker[Session], Path],
    tmp_path: Path,
    faithful: bool,
    smart: bool,
) -> None:
    from classscribe.db.models import ExportArtifact

    runner = make_runner(tmp_path, None)
    with database[1].begin() as session:
        job, segment, checkpoint = setup_segment(session)
        segment.speaker_id = "speaker-private"
        segment.smart_corrected_text = "Corrected text"
        job.options_json = {
            "outputs": ["md"],
            "include_faithful": faithful,
            "include_smart": smart,
            "include_speakers": False,
        }
        session.flush()
        runner._automatic_exports(session, job, checkpoint)
        session.flush()
        artifacts = tuple(session.scalars(select(ExportArtifact)))
        assert {item.text_layer for item in artifacts} == ({"faithful"} if faithful else set()) | (
            {"smart"} if smart else set()
        )
        for artifact in artifacts:
            content = runner.paths.data_path(artifact.relative_path).read_text()
            assert "speaker-private" not in content
            assert (
                "Hello world" if artifact.text_layer == "faithful" else "Corrected text"
            ) in content
            assert artifact.text_layer in artifact.file_name
