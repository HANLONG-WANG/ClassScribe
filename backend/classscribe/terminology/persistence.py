"""Short database transactions for course terms and smart-layer correction evidence."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from classscribe.contracts import LanguageMode
from classscribe.db.models import DecisionEvent, Glossary, GlossaryTerm, TranscriptSegment
from classscribe.errors import ClassScribeError, ErrorCode
from classscribe.terminology.correction import CorrectionResult
from classscribe.terminology.models import CourseConfig, CourseTerm


class TerminologyRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def store_course(self, config: CourseConfig) -> str:
        with self._sessions.begin() as session:
            glossary = session.scalar(
                select(Glossary).where(Glossary.course_id == config.course_id)
            )
            if glossary is None:
                glossary = Glossary(
                    name=config.name, course_id=config.course_id, version=config.version
                )
                session.add(glossary)
                session.flush()
            else:
                glossary.name = config.name
                glossary.version = config.version
            people = tuple(
                CourseTerm(
                    canonical=item.canonical,
                    reading=item.reading,
                    language=config.language,
                )
                for item in config.instructors
            )
            self._upsert_terms(session, glossary, (*people, *config.terms), config.language)
            return glossary.id

    def record_suggestions(
        self, glossary_id: str, terms: tuple[CourseTerm, ...], *, default_language: str
    ) -> int:
        if any(term.user_confirmed or term.weight > 0.3 for term in terms):
            raise ValueError("suggestion import accepts only low-weight unconfirmed terms")
        with self._sessions.begin() as session:
            glossary = session.get(Glossary, glossary_id)
            if glossary is None:
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "glossary does not exist")
            return self._upsert_terms(session, glossary, terms, default_language)

    def apply_correction(self, segment_id: str, result: CorrectionResult) -> None:
        with self._sessions.begin() as session:
            segment = session.get(TranscriptSegment, segment_id)
            if segment is None:
                raise ClassScribeError(ErrorCode.JOB_STATE_CONFLICT, "segment does not exist")
            if segment.faithful_text != result.layers.faithful_text:
                raise ClassScribeError(
                    ErrorCode.JOB_STATE_CONFLICT,
                    "correction cannot replace or diverge from faithful_text",
                )
            before = segment.smart_corrected_text
            segment.smart_corrected_text = result.layers.smart_corrected_text
            session.add(
                DecisionEvent(
                    segment_id=segment_id,
                    event_type="deterministic_terminology_corrected",
                    actor_type="automatic",
                    actor_id=None,
                    input_json={"faithful_text": segment.faithful_text, "smart_before": before},
                    output_json={
                        "smart_corrected_text": result.layers.smart_corrected_text,
                        "faithful_text_unchanged": True,
                        "diffs": [
                            {
                                "start": diff.start,
                                "end": diff.end,
                                "before": diff.before,
                                "after": diff.after,
                                "rule_id": diff.rule_id,
                                "source": diff.source,
                            }
                            for diff in result.diffs
                        ],
                    },
                    rule_version=result.rule_version,
                )
            )

    @staticmethod
    def _upsert_terms(
        session: Session,
        glossary: Glossary,
        terms: tuple[CourseTerm, ...],
        default_language: str,
    ) -> int:
        count = 0
        for term in terms:
            language = LanguageMode(term.language or default_language)
            record = session.scalar(
                select(GlossaryTerm).where(
                    GlossaryTerm.glossary_id == glossary.id,
                    GlossaryTerm.language == language,
                    GlossaryTerm.canonical == term.canonical,
                )
            )
            if record is None:
                record = GlossaryTerm(
                    glossary_id=glossary.id,
                    canonical=term.canonical,
                    language=language,
                )
                session.add(record)
                count += 1
            record.reading = term.reading
            record.aliases = list(term.aliases)
            record.weight = term.weight
            record.source = term.source.value
            record.user_confirmed = term.user_confirmed
        return count
