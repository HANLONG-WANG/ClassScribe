"""Audit regressions with isolated model and database fixtures."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from classscribe.alignment import AlignedToken, AlignmentRepository, CanonicalTiming, TimingSource
from classscribe.asr.context import core_evidence
from classscribe.asr.models import (
    ASRCandidateEvidence,
    ASRTokenEvidence,
    CandidateRole,
    decode_policy,
)
from classscribe.audio import AudioPreprocessor, PCMQualityAnalyzer
from classscribe.audio.segmentation import BoundaryCue, BoundaryKind, make_transcript_chunks
from classscribe.benchmark import (
    GoldCoverage,
    GoldRecord,
    rank_candidates,
    score_text,
    score_timeline,
    validate_phase12_acceptance,
)
from classscribe.benchmark.models import WordAnnotation
from classscribe.benchmark.runner import _aggregate_metrics
from classscribe.classroom import ProductionStageRunner
from classscribe.config import load_config
from classscribe.consensus import (
    ConfusionNetwork,
    ConsensusCandidate,
    ConsensusInputs,
    ReliabilityProfile,
)
from classscribe.contracts import LanguageMode
from classscribe.db.models import (
    ASRCandidate,
    DecisionEvent,
    GlossaryTerm,
    Job,
    JobCheckpoint,
    LanguageSpan,
    Recording,
    StructureSegmentRecord,
    TimingQuality,
    TokenSpan,
    TranscriptSegment,
)
from classscribe.exports import (
    ExportFormat,
    ExportLayer,
    ExportSegment,
    ExportToken,
    build_subtitle_cues,
    load_export_segments,
    render_export,
)
from classscribe.models import ModelManager, SandboxedModelInvoker, load_registry
from classscribe.paths import AppPaths
from classscribe.quality import (
    ComparisonContext,
    QualityContext,
    QualityFeatureExtractor,
    ReviewRouter,
    compare_language_candidates,
)
from classscribe.quality.loop import inspect_decode_loop
from classscribe.quality.models import QualityReport
from classscribe.structure.diarization import _dominant_exclusive_speaker
from classscribe.terminology import (
    ConfirmedSegment,
    CourseConfig,
    CourseTerm,
    TerminologyRepository,
    TermUsage,
    select_rolling_context,
)
from classscribe.terminology.models import ConfirmationStatus
from classscribe.timeline import SAMPLE_RATE, AudioSpan
from classscribe_protocol import RPCResponse
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

SPAN = AudioSpan(0, 8 * SAMPLE_RATE)
Database = tuple[Engine, sessionmaker[Session], Path]


def candidate(
    identifier: str, text: str, tokens: tuple[ASRTokenEvidence, ...] = ()
) -> ConsensusCandidate:
    evidence = ASRCandidateEvidence(
        identifier,
        "b" * 40,
        CandidateRole.PRIMARY,
        "en",
        "en",
        SPAN,
        SPAN,
        text,
        text,
        0.9,
        tokens,
        decode_policy(SPAN.duration_samples),
        {},
        (),
        {},
    )
    quality = QualityReport(identifier, identifier, True, 0.9, False, (), {}, {}, {}, {}, None)
    return ConsensusCandidate(identifier, evidence, quality)


def resolve(candidates: tuple[ConsensusCandidate, ...]) -> Any:
    return ConfusionNetwork().resolve(
        candidates,
        canonical_span=SPAN,
        language="en",
        inputs=ConsensusInputs(ReliabilityProfile({}, "neutral", False, "classroom"), {}, (SPAN,)),
    )


@pytest.mark.parametrize("count", [2, 3])
def test_minority_insertion_has_epsilon_opposition(count: int) -> None:
    candidates = (
        *(candidate(str(i), "Give 5 mg daily.") for i in range(count - 1)),
        candidate("extra", "Give 5 mg extra daily."),
    )
    assert resolve(candidates).text == "Give 5 mg daily."


def test_majority_insertion_retains_nonunanimous_support() -> None:
    result = resolve(
        (
            candidate("a", "Give 5 mg extra daily."),
            candidate("b", "Give 5 mg extra daily."),
            candidate("c", "Give 5 mg daily."),
        )
    )
    assert "extra" in result.text
    assert next(
        token.support_score for token in result.tokens if token.text == "extra"
    ) == pytest.approx(2 / 3)


def test_partial_native_words_never_replace_complete_body() -> None:
    value = candidate("partial", "Give 5 mg daily.", (ASRTokenEvidence("Give", SPAN, 0.9, 0),))
    actual = QualityFeatureExtractor().inspect(
        "partial", value.evidence, QualityContext(SPAN, (SPAN,), 1.0)
    )
    assert actual.timing_features["native_word_text_complete"] is False
    assert actual.rule_version == "quality-gate-v3"
    result = resolve((replace(value, quality=actual),))
    assert result.text == "Give 5 mg daily."
    assert result.rule_version == "time-aligned-confusion-network-v3"
    assert all(
        source["timing_source"] == "constrained_interval"
        for token in result.tokens
        for source in token.provenance["candidate_sources"]
    )


@pytest.mark.parametrize("kind", [BoundaryKind.LANGUAGE_SWITCH, BoundaryKind.SPEAKER_CHANGE])
def test_short_and_tail_chunks_keep_mandatory_boundaries(kind: BoundaryKind) -> None:
    chunks = make_transcript_chunks(
        AudioSpan(0, 20 * SAMPLE_RATE), (BoundaryCue(10 * SAMPLE_RATE, kind),)
    )
    assert [chunk.core_span for chunk in chunks] == [
        AudioSpan(0, 10 * SAMPLE_RATE),
        AudioSpan(10 * SAMPLE_RATE, 20 * SAMPLE_RATE),
    ]
    tail = make_transcript_chunks(
        AudioSpan(0, 35 * SAMPLE_RATE), (BoundaryCue(32 * SAMPLE_RATE, kind),)
    )
    assert any(chunk.core_span.end_sample == 32 * SAMPLE_RATE for chunk in tail)


def contextual_evidence() -> ASRCandidateEvidence:
    full, core = (
        AudioSpan(9 * SAMPLE_RATE, 21 * SAMPLE_RATE),
        AudioSpan(10 * SAMPLE_RATE, 20 * SAMPLE_RATE),
    )
    return replace(
        candidate("context", "before inside after").evidence,
        audio_span=full,
        core_span=core,
        tokens=(
            ASRTokenEvidence("before", AudioSpan(full.start_sample, core.start_sample), 0.9, 0),
            ASRTokenEvidence("inside", core, 0.9, 1),
            ASRTokenEvidence("after", AudioSpan(core.end_sample, full.end_sample), 0.9, 2),
        ),
    )


def test_context_assigns_only_native_core_and_retains_original_evidence() -> None:
    original = contextual_evidence()
    evidence = core_evidence(original)
    assert evidence.raw_text == "before inside after" and evidence.normalized_text == "inside"
    assert evidence.tokens[0].source_index == 1
    assert evidence.provenance["original_normalized_text"] == original.normalized_text
    assert len(evidence.provenance["original_tokens"]) == 3
    report = QualityFeatureExtractor().inspect(
        "context", evidence, QualityContext(evidence.core_span, (evidence.core_span,), 1)
    )
    assert report.valid_for_consensus
    final = ConfusionNetwork().resolve(
        (ConsensusCandidate("context", evidence, report),),
        canonical_span=evidence.core_span,
        language="en",
        inputs=ConsensusInputs(
            ReliabilityProfile({}, "neutral", False, "classroom"), {}, (evidence.core_span,)
        ),
    )
    assert final.text == "inside"
    assert final.tokens[0].provenance["candidate_sources"][0]["source_token_index"] == 1


def test_untimed_context_never_deletes_words_and_requires_review() -> None:
    original = replace(contextual_evidence(), tokens=())
    evidence = core_evidence(original)
    assert evidence.normalized_text == original.normalized_text
    assert evidence.provenance["context_assignment"] == "unresolved_context_boundary"
    quality = replace(candidate("context", "unused").quality, candidate_id="context")
    result = ConfusionNetwork().resolve(
        (ConsensusCandidate("context", evidence, quality),),
        canonical_span=evidence.core_span,
        language="en",
        inputs=ConsensusInputs(
            ReliabilityProfile({}, "neutral", False, "classroom"), {}, (evidence.core_span,)
        ),
    )
    assert result.text == original.raw_text and result.low_confidence
    assert "unresolved_context_boundary" in result.warnings


def test_native_words_keep_split_cues_after_punctuation_projection() -> None:
    words = [
        "We",
        "use",
        "a",
        "carefully",
        "designed",
        "explanation",
        "for",
        "this",
        "class",
        "and",
        "then",
        "compare",
        "several",
        "methods",
        "to",
        "understand",
        "the",
        "lesson",
        "clearly",
    ]
    text = " ".join(words) + "."
    tokens = tuple(
        ExportToken(word, AudioSpan(i * SAMPLE_RATE, (i + 1) * SAMPLE_RATE))
        for i, word in enumerate(words)
    )
    segment = ExportSegment(
        "words", AudioSpan(0, len(words) * SAMPLE_RATE), "en", text, text, text, None, tokens
    )
    cues = build_subtitle_cues((segment,), ExportLayer.FAITHFUL)
    assert len(cues) > 1 and all(len(line.split()) <= 8 for cue in cues for line in cue.lines)
    assert " ".join(line for cue in cues for line in cue.lines) == text
    assert all(not cue.coarse_timing for cue in cues)


def test_long_sentence_time_exports_bounded_explicit_coarse_cues() -> None:
    text = (
        "这是一句超过十八个汉字且有可靠整句结构时间的课堂说明我们继续展开介绍相关证据以及具体方法。"
    )
    segment = ExportSegment(
        "sentence", SPAN, "zh", text, text, text, None, (ExportToken(text, SPAN),)
    )
    cues = build_subtitle_cues((segment,), ExportLayer.FAITHFUL)
    assert all(
        1 <= len(cue.lines) <= 2 and all(0 < len(line) <= 18 for line in cue.lines) for cue in cues
    )
    assert "".join(line for cue in cues for line in cue.lines) == text
    assert all(cue.coarse_timing for cue in cues)
    assert "粗时间" in render_export(
        (segment,), output_format=ExportFormat.SRT, layer=ExportLayer.FAITHFUL
    )


def test_numeric_relationship_and_negation_changes_request_review() -> None:
    prefix = (
        "We carefully examine all relevant information in this classroom and record "
        "the detailed explanation of the daily schedule before the later visit to compare "
        "available evidence with the other observations from the lesson. "
    )
    context = ComparisonContext({}, {})
    comparison = compare_language_candidates(
        prefix + "Take 5 mg in the morning and 10 mg at night.",
        prefix + "Take 10 mg in the morning and 5 mg at night.",
        "en",
        context,
    )
    assert "number" in comparison.high_value_conflicts
    quality = candidate("q", "ordinary text").quality
    assert ReviewRouter().tertiary(quality, quality, comparison).run_tertiary
    assert (
        "negation"
        in compare_language_candidates(
            "Do not heat but stir.", "Heat but do not stir.", "en", context
        ).high_value_conflicts
    )
    assert (
        "negation"
        not in compare_language_candidates(
            "Notice the topic.", "Note the topic.", "en", context
        ).high_value_conflicts
    )


def test_decimals_are_distinct_from_separate_numbers() -> None:
    rate = score_text("Give 3.5 mg", "Give 3 5 mg", "en")["normalized_wer"]
    assert isinstance(rate, (int, float)) and rate > 0


def test_missing_word_timing_is_unobserved_and_ineligible() -> None:
    missing = score_timeline((WordAnnotation("hello", 0, 16000),), (), duration_samples=16000)
    assert missing["word_boundary_mae_ms"] is None and missing["word_timing_coverage"] == 0
    base = {
        "normalized_wer": 0,
        "term": {"f1": 1},
        "punctuation": {"per": 0},
        "rtf": 0.2,
        "provenance_coverage": 1,
        "runtime_observation_coverage": 1,
    }
    value = {
        "language": "en",
        "scenario": "classroom",
        "model_id": "missing",
        "model_revision": "b" * 40,
        "calibration": {
            "method": "isotonic",
            "model_revision": "b" * 40,
            "manifest_sha256": "f" * 64,
        },
        "metrics": {**base, **missing},
    }
    ranked = rank_candidates((value,), language="en", scenario="classroom")
    assert not ranked[0]["eligible"] and "incomplete_word_timing" in ranked[0]["violations"]
    values: tuple[dict[str, object], ...] = (
        {**missing},
        {**score_timeline((), (), duration_samples=16000)},
    )
    combined = _aggregate_metrics(values)
    assert combined["word_timing_coverage"] == 0


def test_uncovered_structure_span_has_no_speaker() -> None:
    exclusive = (SimpleNamespace(span=AudioSpan(0, 16000), speaker_local="unrelated"),)
    assert _dominant_exclusive_speaker(AudioSpan(32000, 48000), cast(Any, exclusive)) is None


def test_english_repetition_excludes_titles_and_decimals() -> None:
    result = inspect_decode_loop(
        "Today we discuss a new topic in the classroom. " * 3, "en", voiced_seconds=8
    )
    assert result.metrics["maximum_repeated_sentence_count"] == 3 and result.issues
    ordinary = inspect_decode_loop(
        "Dr. Smith measured 3.5 mg. Dr. Jones measured 4.5 mg. Dr. May measured 5.5 mg.",
        "en",
        voiced_seconds=8,
    )
    assert ordinary.metrics["maximum_repeated_sentence_count"] == 1


def test_structured_context_respects_rendered_bound() -> None:
    history = tuple(ConfirmedSegment("x" * 1000, "en", "course", None, i) for i in range(3))
    terms = tuple(TermUsage(CourseTerm("T" * 100 + str(i), "r" * 100), "course") for i in range(12))
    bundle = select_rolling_context(
        course_id="course",
        chapter=None,
        language="en",
        history=history,
        usages=terms,
        max_characters=128,
    )
    assert len(bundle.rendered) <= 128 and sum(map(len, bundle.confirmed_segments)) <= 128
    assert all(
        term.canonical in bundle.rendered and term.reading in bundle.rendered
        for term in bundle.keywords
    )


def test_gold_coverage_uses_union_and_sparse_extent_is_not_90_minutes() -> None:
    record = GoldRecord(
        "one", "same.wav", "classroom", "train", "en", 0, 600 * SAMPLE_RATE, "lesson"
    )
    assert (
        GoldCoverage.inspect((record, replace(record, item_id="two"))).classroom_seconds["en"]
        == 600
    )
    sparse = (
        replace(record, end_sample=SAMPLE_RATE),
        replace(
            record, item_id="late", start_sample=5399 * SAMPLE_RATE, end_sample=5400 * SAMPLE_RATE
        ),
    )
    acceptance = validate_phase12_acceptance(sparse, {}, {}, {}, manifest_sha256="a" * 64)
    assert (
        not acceptance.checks["real_90m_classroom"]
        and acceptance.details["maximum_classroom_seconds"] == 2
    )


def seed(
    session: Session, text: str, language: LanguageMode = LanguageMode.CHINESE
) -> tuple[Job, TranscriptSegment]:
    recording = Recording(
        source_name="probe.wav",
        source_sha256="a" * 64,
        source_path="/tmp/probe.wav",
        duration_samples=24 * SAMPLE_RATE,
        sample_rate=SAMPLE_RATE,
        channels=1,
    )
    job = Job(recording=recording, language_mode=language, profile_id="balanced")
    segment = TranscriptSegment(
        job=job,
        start_sample=0,
        end_sample=SPAN.end_sample,
        language=language,
        raw_text=text,
        faithful_text=text,
        smart_corrected_text=text,
    )
    session.add(segment)
    session.flush()
    return job, segment


def runner(tmp_path: Path, invoke: Any = None) -> ProductionStageRunner:
    paths = AppPaths.from_environment({}, home=tmp_path / "home")
    paths.ensure()
    return ProductionStageRunner(
        paths,
        load_config(environment={}),
        load_registry(Path("config/model-registry.v1.yaml")),
        cast(ModelManager, object()),
        cast(SandboxedModelInvoker, invoke),
    )


def test_production_moss_subtitles_coarse_metadata_and_explicit_empty_edit(
    database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = runner(tmp_path)
    monkeypatch.setattr(value, "_installed", lambda _: False)
    text = "这是一句超过十八个汉字且有可靠整句结构时间的课堂说明我们继续介绍相关方法。"
    with database[1].begin() as session:
        job, segment = seed(session, text)
        session.add(
            StructureSegmentRecord(
                job_id=job.id,
                window_ordinal=0,
                start_sample=0,
                end_sample=SPAN.end_sample,
                coarse_text=text,
                source_model="moss_td_0_9b",
                source_revision="b" * 40,
                selection_score=0.9,
                text_role="coarse_timeline_consensus_candidate_boundary_reference",
                fallback=False,
                overlap=False,
                exclusive=True,
            )
        )
        session.flush()
        value._forced_alignment(session, job, JobCheckpoint(segment_id=segment.id))
        session.flush()
        assert "粗时间" in render_export(
            load_export_segments(session, job.id),
            output_format=ExportFormat.SRT,
            layer=ExportLayer.FAITHFUL,
        )
        segment.timing_quality = TimingQuality.STRUCTURE
        assert value._export_segments(session, job.id)[0].coarse_timing
        segment.user_text = ""
        value._forced_alignment(session, job, JobCheckpoint(segment_id=segment.id))
        session.flush()
        value._final_validation(session, job, JobCheckpoint(segment_id=segment.id))
        assert (
            render_export(
                value._export_segments(session, job.id),
                output_format=ExportFormat.SRT,
                layer=ExportLayer.USER,
            )
            == ""
        )


def test_production_keeps_context_request_and_source_indices(
    database: Database, tmp_path: Path
) -> None:
    captured: list[Any] = []

    async def invoke(entry: Any, request: Any) -> RPCResponse:
        captured.append(request)
        original = contextual_evidence()
        return RPCResponse(
            request.request_id,
            request.job_id,
            True,
            entry.id,
            entry.revision,
            raw_text=original.raw_text,
            normalized_text=original.normalized_text,
            language="en",
            segments=(
                {
                    "start_sample": original.audio_span.start_sample,
                    "end_sample": original.audio_span.end_sample,
                    "text": original.raw_text,
                    "words": [
                        {
                            "text": token.text,
                            "start_sample": token.span.start_sample,
                            "end_sample": token.span.end_sample,
                        }
                        for token in original.tokens
                    ],
                },
            ),
        )

    value = runner(tmp_path, invoke)
    with database[1].begin() as session:
        job, segment = seed(session, "", LanguageMode.ENGLISH)
        segment.start_sample, segment.end_sample = 10 * SAMPLE_RATE, 20 * SAMPLE_RATE
        session.add(
            DecisionEvent(
                segment_id=segment.id,
                event_type="natural_segment_context",
                actor_type="automatic",
                input_json={},
                output_json={
                    "core_start_sample": segment.start_sample,
                    "core_end_sample": segment.end_sample,
                    "audio_start_sample": 9 * SAMPLE_RATE,
                    "audio_end_sample": 21 * SAMPLE_RATE,
                },
                rule_version="natural-context-v1",
            )
        )
        session.flush()
        evidence = value._transcribe(
            session, job, segment, value.registry.model("qwen3_asr_1_7b"), CandidateRole.PRIMARY
        )
        assert (
            captured[0].params["start_sample"] == 9 * SAMPLE_RATE
            and captured[0].params["core_start_sample"] == 10 * SAMPLE_RATE
        )
        assert evidence.normalized_text == "inside" and evidence.raw_text == "before inside after"
        row = value._store_candidate(session, segment, evidence)
        session.flush()
        restored = value._evidence(session, segment, row)
        assert (
            restored.tokens[0].source_index == 1
            and restored.audio_span.start_sample == 9 * SAMPLE_RATE
        )


def test_production_passes_lid_boundaries_into_structure(
    database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Structure:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def process(self, **kwargs: Any) -> Any:
            cues = kwargs["boundary_cues"]
            assert len(cues) == 1 and cues[0].sample == 10 * SAMPLE_RATE
            return SimpleNamespace(
                segments=(),
                windows=(),
                transcript_chunks=make_transcript_chunks(AudioSpan(0, 20 * SAMPLE_RATE), cues),
            )

    monkeypatch.setattr("classscribe.classroom.production.StructurePipeline", Structure)
    value = runner(tmp_path)
    with database[1].begin() as session:
        job, old = seed(session, "", LanguageMode.AUTO_MIXED)
        session.delete(old)
        session.add_all(
            [
                LanguageSpan(
                    job_id=job.id,
                    start_sample=0,
                    end_sample=10 * SAMPLE_RATE,
                    language=LanguageMode.JAPANESE,
                    confidence_raw=0.95,
                ),
                LanguageSpan(
                    job_id=job.id,
                    start_sample=10 * SAMPLE_RATE,
                    end_sample=20 * SAMPLE_RATE,
                    language=LanguageMode.ENGLISH,
                    confidence_raw=0.95,
                ),
            ]
        )
        session.flush()
        value._moss_structure(session, job, JobCheckpoint())
        session.flush()
        segments = session.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.job_id == job.id)
            .order_by(TranscriptSegment.start_sample)
        ).all()
        assert [segment.language for segment in segments] == [
            LanguageMode.JAPANESE,
            LanguageMode.ENGLISH,
        ]
        assert session.scalars(
            select(DecisionEvent).where(DecisionEvent.event_type == "natural_segment_context")
        ).all()


def test_alignment_repository_preserves_each_source(database: Database) -> None:
    with database[1].begin() as session:
        _job, segment = seed(session, "one two", LanguageMode.ENGLISH)
        source = ASRCandidate(
            segment_id=segment.id,
            model_id="qwen",
            model_revision="b" * 40,
            raw_text="one two",
            normalized_text="one two",
        )
        session.add(source)
        session.flush()
        for index, (word, start, end) in enumerate(
            (("one", 0, 4 * SAMPLE_RATE), ("two", 4 * SAMPLE_RATE, SPAN.end_sample))
        ):
            session.add(
                TokenSpan(
                    segment_id=segment.id,
                    token=word,
                    normalized_token=word,
                    start_sample=start,
                    end_sample=end,
                    provenance_json={
                        "candidate_sources": [
                            {
                                "candidate_id": source.id,
                                "source_token_index": index,
                                "source_start_sample": start,
                                "source_end_sample": end,
                            }
                        ]
                    },
                )
            )
        segment_id = segment.id
    AlignmentRepository(database[1]).apply(
        segment_id,
        CanonicalTiming(
            TimingSource.NATIVE_WORD,
            SPAN,
            "one two",
            (
                AlignedToken("one", AudioSpan(0, 4 * SAMPLE_RATE)),
                AlignedToken("two", AudioSpan(4 * SAMPLE_RATE, SPAN.end_sample)),
            ),
            False,
            1,
        ),
    )
    with database[1]() as session:
        tokens = session.scalars(
            select(TokenSpan)
            .where(TokenSpan.segment_id == segment_id, TokenSpan.candidate_id.is_(None))
            .order_by(TokenSpan.start_sample)
        ).all()
        assert [
            token.provenance_json["candidate_sources"][0]["source_token_index"] for token in tokens
        ] == [0, 1]


def test_repeated_suggestions_are_idempotent(database: Database) -> None:
    repository = TerminologyRepository(database[1])
    glossary = repository.store_course(CourseConfig("course", "en", "Course", (), ()))
    term = CourseTerm(
        "Term", "reading", weight=0.2, confirmation=ConfirmationStatus.SUGGESTED, language="en"
    )
    assert repository.record_suggestions(glossary, (term, term), default_language="en") == 1
    with database[1]() as session:
        assert (
            len(
                session.scalars(
                    select(GlossaryTerm).where(GlossaryTerm.glossary_id == glossary)
                ).all()
            )
            == 1
        )


def test_preprocessor_reuses_channel_analysis(tmp_path: Path) -> None:
    from tests.unit.test_audio_pipeline import prepared

    fixture = prepared(tmp_path / "audio.wav", SAMPLE_RATE)

    class Media:
        def import_source(self, *_args: Any) -> Any:
            return fixture.imported

        def make_multichannel_qc_wav(self, *_args: Any) -> None:
            pass

        def normalize(self, *_args: Any, **_kwargs: Any) -> Any:
            return fixture.master

    class Quality(PCMQualityAnalyzer):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def analyze_channels(self, _path: Path) -> Any:
            self.calls += 1
            return fixture.quality.channels

    quality = Quality()
    value = AudioPreprocessor(cast(Any, Media()), quality)
    value.prepare(
        tmp_path / "source.wav",
        source_directory=tmp_path / "source",
        master_path=tmp_path / "derived/audio_master.wav",
    )
    assert quality.calls == 1
