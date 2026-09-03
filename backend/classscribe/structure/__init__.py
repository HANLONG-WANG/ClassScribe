"""Long-form structural transcription, diarization fallback, and speaker stitching."""

from classscribe.structure.models import (
    STRUCTURE_TEXT_ROLE,
    StructureResult,
    StructureSegment,
    build_moss_structure_request,
    parse_moss_structure_response,
)

__all__ = [
    "STRUCTURE_TEXT_ROLE",
    "StructureResult",
    "StructureSegment",
    "build_moss_structure_request",
    "parse_moss_structure_response",
]
