from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from classscribe.punctuation import (
    AcousticBoundary,
    BoundaryLabel,
    case_sensitive_wer,
    punctuate_chinese,
    punctuate_english,
    punctuate_japanese,
    punctuation_f1,
    strip_punctuation_and_spacing,
)
from classscribe.terminology import (
    ConfirmationStatus,
    ConfirmedSegment,
    CorrectionEvidence,
    CourseTerm,
    TermSource,
    TermUsage,
    TextLayers,
    apply_terminology,
    confirmed_history_terms,
    import_csv_terms,
    import_material,
    load_course_config,
    select_rolling_context,
)
from hypothesis import given
from hypothesis import strategies as st


def test_course_yaml_and_material_suggestions_are_strict_and_low_weight(tmp_path: Path) -> None:
    course = tmp_path / "course.yaml"
    course.write_text(
        """course_id: bio_2026_fall
language: ja
name: 生物学概論
instructors:
  - canonical: 田中太郎
    reading: たなかたろう
terms:
  - canonical: ヤマトタチバナ
    reading: やまとたちばな
    aliases: [大和橘]
    weight: 1.0
    source: manual
    confirmation: confirmed
""",
        encoding="utf-8",
    )
    loaded = load_course_config(course)
    assert loaded.course_id == "bio_2026_fall"
    assert loaded.terms[0].aliases == ("大和橘",)
    material = tmp_path / "lecture.md"
    material.write_text("# OIST\nヤマトタチバナ と OIST", encoding="utf-8")
    imported = import_material(material, language="ja")
    assert imported.source is TermSource.MARKDOWN
    assert imported.suggestions
    assert all(
        term.confirmation is ConfirmationStatus.SUGGESTED and term.weight <= 0.3
        for term in imported.suggestions
    )


def test_csv_pptx_pdf_handout_textbook_and_confirmed_history_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "terms.csv"
    csv_path.write_text(
        "canonical,reading,aliases,confirmation,weight\n"
        "OIST,oh eye ess tee,Institute,confirmed,1\n",
        encoding="utf-8",
    )
    assert import_csv_terms(csv_path, language="en")[0].user_confirmed

    pptx = tmp_path / "slides.pptx"
    with zipfile.ZipFile(pptx, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="p" xmlns:a="a"><a:t>Photosynthesis OIST</a:t></p:sld>',
        )
    assert "Photosynthesis" in import_material(pptx, language="en").text

    pdf = tmp_path / "chapter.pdf"
    pdf.write_bytes(b"%PDF fixture")
    monkeypatch.setattr(
        "classscribe.terminology.importers.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="生物学 OIST".encode()),
    )
    assert "生物学" in import_material(pdf, language="ja").text

    handout = tmp_path / "handout.txt"
    handout.write_text("ClassScribe", encoding="utf-8")
    assert (
        import_material(handout, language="en", source=TermSource.HANDOUT).source
        is TermSource.HANDOUT
    )
    assert (
        import_material(handout, language="en", source=TermSource.TEXTBOOK).source
        is TermSource.TEXTBOOK
    )
    historical = confirmed_history_terms(
        [("ClassScribe", "class scribe", ("Class Scribe",), 4)], language="en"
    )
    assert historical[0].source is TermSource.HISTORICAL_CONFIRMED
    assert historical[0].user_confirmed and historical[0].weight > 0.75


def test_deterministic_correction_changes_only_smart_and_never_inserts_unheard_term() -> None:
    layers = TextLayers("大和橘を学ぶ", "大和橘を学ぶ。", "大和橘を学ぶ。")
    term = CourseTerm(
        "ヤマトタチバナ",
        "やまとたちばな",
        ("大和橘",),
        language="ja",
    )
    result = apply_terminology(
        layers,
        (term,),
        {"ヤマトタチバナ": CorrectionEvidence(frozenset({"大和橘"}))},
        language="ja",
    )
    assert result.layers.faithful_text == "大和橘を学ぶ。"
    assert result.layers.smart_corrected_text == "ヤマトタチバナを学ぶ。"
    assert result.diffs[0].rule_id.startswith("course-term:ja")
    unheard = apply_terminology(
        TextLayers("授業", "授業。", "授業。"),
        (term,),
        {"ヤマトタチバナ": CorrectionEvidence(frozenset())},
        language="ja",
    )
    assert unheard.layers.smart_corrected_text == "授業。"


def test_rolling_context_keeps_three_confirmed_segments_and_ranked_top_k() -> None:
    history = (
        *(ConfirmedSegment(f"sentence-{index}", "en", "bio", "one", index) for index in range(6)),
        ConfirmedSegment("unconfirmed", "en", "bio", "one", 7, False),
    )
    terms = tuple(
        TermUsage(CourseTerm(f"Term{index}", f"term {index}", language="en"), "bio", index)
        for index in range(5)
    )
    bundle = select_rolling_context(
        course_id="bio",
        chapter="one",
        language="en",
        history=history,
        usages=terms,
        top_k=2,
        max_characters=512,
    )
    assert bundle.confirmed_segments == ("sentence-3", "sentence-4", "sentence-5")
    assert [term.canonical for term in bundle.keywords] == ["Term4", "Term3"]
    assert bundle.bias_only and not bundle.permits_unsupported_insertion
    assert "unconfirmed" not in bundle.rendered


@given(st.text(alphabet="光合作用CPUabc123", min_size=1, max_size=40))
def test_chinese_punctuation_never_changes_non_punctuation_characters(text: str) -> None:
    proposal = text + "。"
    result = punctuate_chinese(
        text,
        firered_proposal=proposal,
        acoustic_boundaries=(AcousticBoundary(len(text), pause_ms=800),),
    )
    assert strip_punctuation_and_spacing(result.text) == strip_punctuation_and_spacing(text)


def test_three_language_punctuation_routing_rejects_changed_text_and_protects_names() -> None:
    chinese = punctuate_chinese("今天学习CPU", firered_proposal="今天学习GPU。")
    assert chinese.text == "今天学习CPU"
    assert chinese.rejected_sources == ("firered_punc_changed_characters",)

    english = punctuate_english(
        "We use ClassScribe",
        native_text="we use Classscribe.",
        firered_proposal="We use ClassScribe.",
        protected_forms=("ClassScribe",),
    )
    assert english.source == "firered_punc_fallback"
    assert english.text == "We use ClassScribe."

    japanese = punctuate_japanese(
        "今日は光合成を学びます",
        granite_proposal="今日は、光合成を学びます。",
        acoustic_boundaries=(AcousticBoundary(3, pause_ms=300),),
        tagger_labels=(BoundaryLabel(5, "。", 0.9),),
    )
    assert strip_punctuation_and_spacing(japanese.text) == "今日は光合成を学びます"
    assert japanese.source.startswith("granite_projection")


def test_english_case_sensitive_wer_and_punctuation_f1_are_reported_separately() -> None:
    assert case_sensitive_wer("Use CPU now", "use CPU now") == pytest.approx(1 / 3)
    assert punctuation_f1("Hello, world!", "Hello world!") == pytest.approx(2 / 3)
