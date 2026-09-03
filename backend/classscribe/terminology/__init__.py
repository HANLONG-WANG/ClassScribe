"""Course terminology, bounded context, and deterministic smart-layer correction."""

from classscribe.terminology.context import (
    ConfirmedSegment,
    ContextBundle,
    TermUsage,
    select_rolling_context,
)
from classscribe.terminology.correction import (
    CorrectionDiff,
    CorrectionEvidence,
    CorrectionResult,
    TextLayers,
    apply_terminology,
    select_export_text,
)
from classscribe.terminology.importers import (
    MaterialImport,
    confirmed_history_terms,
    import_csv_terms,
    import_material,
    suggest_terms,
)
from classscribe.terminology.models import (
    ConfirmationStatus,
    CourseConfig,
    CoursePerson,
    CourseTerm,
    TermSource,
    load_course_config,
)
from classscribe.terminology.persistence import TerminologyRepository

__all__ = [
    "ConfirmationStatus",
    "ConfirmedSegment",
    "ContextBundle",
    "CorrectionDiff",
    "CorrectionEvidence",
    "CorrectionResult",
    "CourseConfig",
    "CoursePerson",
    "CourseTerm",
    "MaterialImport",
    "TermSource",
    "TermUsage",
    "TerminologyRepository",
    "TextLayers",
    "apply_terminology",
    "confirmed_history_terms",
    "import_csv_terms",
    "import_material",
    "load_course_config",
    "select_export_text",
    "select_rolling_context",
    "suggest_terms",
]
