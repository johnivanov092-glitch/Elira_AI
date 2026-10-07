from copy import deepcopy

import pytest

from app.application.code_agent.run_evidence import RunEvidence
from app.application.web_evidence.receipts import excerpt_sources, format_source, make_source


QUOTE = "As an alternative, it is possible to specify the engines to keep."


def _presented_excerpts():
    # Reproduce the live Qwen error: the cited offset 0 and the actual quote at
    # offset 1500 share a page and content hash, but are distinct receipts.
    body = "Introductory documentation. " * 60
    body = body[:1500] + QUOTE + "\nOnly explicitly selected engines remain."
    sources = excerpt_sources(
        run_id="quote-binding", tool="web_fetch", url="https://example.org/settings",
        text=body, fetched_at=123.0,
    )
    evidence = RunEvidence()
    text = "\n\n".join(format_source(source) for source in sources)
    evidence.record_tool_result(
        tool_name="web_fetch", arguments={"url": "https://example.org/settings"},
        execution_status="ok", output={"ok": True, "sources": sources},
        text_result=text, state_changed=False,
    )
    evidence.mark_sources_presented([{"role": "tool", "content": text}])
    return evidence, sources


def test_direct_quote_rejects_presented_wrong_offset_without_replacing_marker():
    evidence, sources = _presented_excerpts()
    wrong, correct = sources
    assert wrong["offset"] == 0 and correct["offset"] == 1500
    assert wrong["content_hash"] == correct["content_hash"]
    assert QUOTE not in wrong["quote"] and QUOTE in correct["quote"]
    answer = f'Цитата: «{QUOTE}» [[source:{wrong["id"]}]]'

    citations = evidence.citations(answer)

    assert len(citations) == 1
    assert citations[0]["source_id"] == wrong["id"]
    assert citations[0]["status"] == "unresolved"
    assert citations[0]["reason"] == "quote_not_in_cited_excerpt"
    assert "source" not in citations[0]
    assert citations[0]["claim_support"] == "not_assessed"


def test_direct_quote_matches_only_the_exact_presented_excerpt():
    evidence, sources = _presented_excerpts()
    correct = sources[1]
    citation = evidence.citations(f'«{QUOTE}» [[source:{correct["id"]}]]')[0]
    assert citation["status"] == "matched"
    assert citation["source"]["id"] == correct["id"]
    assert citation["claim_support"] == "not_assessed"


def test_plain_paraphrase_remains_provenance_only():
    evidence, sources = _presented_excerpts()
    citation = evidence.citations(
        f'All engines are faster. [[source:{sources[0]["id"]}]]',
    )[0]
    assert citation["status"] == "matched"
    assert citation["claim_support"] == "not_assessed"


def test_unknown_paraphrase_source_gets_one_correction_then_degraded_answer():
    from app.application.code_agent.answer_acceptance import AnswerAcceptance
    from app.application.code_agent.task_outcomes import TaskOutcome
    evidence, sources = _presented_excerpts()
    acceptance = AnswerAcceptance()
    args = dict(final_text="Факт из документации. [[source:invented]]", raw_user_message="Кратко объясни по источнику.",
        pending_redirected_jobs=[], active_capability_groups=["web"], task_outcome=TaskOutcome(),
        run_evidence=evidence, code_input_epoch=0, quote_word_limit=None, step=1, run_id="unknown-citation")
    first = acceptance.evaluate(**args)
    assert first.action == "retry" and first.reason == "quote_source"
    assert "invented" in first.correction
    acceptance.commit(first)
    second = acceptance.evaluate(**args)
    assert second.action == "accept" and second.answer_status == "degraded"
    assert "Часть ссылок" in second.text
    args["final_text"] = f'Факт из документации. [[source:{sources[0]["id"]}]]'
    corrected = acceptance.evaluate(**args)
    assert corrected.action == "accept" and corrected.answer_status == "complete"


@pytest.mark.parametrize("template", [
    '"{quote}" {marker}', '«{quote}» {marker}', '“{quote}” {marker}',
    '> {quote}\n\n{marker}',
    '> {quote}\n\n[Documentation](https://example.org/settings) {marker}',
    '> {quote} {marker}',
])
def test_conventional_local_quote_reference_placements(template):
    evidence, sources = _presented_excerpts()
    marker = f'[[source:{sources[1]["id"]}]]'
    answer = template.format(quote=QUOTE, marker=marker)
    binding = evidence.quote_bindings(answer)[0]
    assert binding["status"] == "matched"
    assert binding["source_ids"] == [sources[1]["id"]]
    assert evidence.citations(answer)[0]["status"] == "matched"


@pytest.mark.parametrize("opening,closing", [('"', '"'), ("«", "»"), ("“", "”")])
def test_blockquote_with_outer_paired_delimiter_binds_exact_inner_words(opening, closing):
    evidence, sources = _presented_excerpts()
    marker = f'[[source:{sources[1]["id"]}]]'
    answer = f'> {opening}{QUOTE}{closing} {marker}'
    assert evidence.quote_bindings(answer)[0]["status"] == "matched"
    assert evidence.quote_bindings(answer)[0]["quote"] == QUOTE
    altered = answer.replace("engines", "Engines")
    assert evidence.citations(altered)[0]["reason"] == "quote_not_in_cited_excerpt"


def test_exact_words_are_not_normalized_or_repaired():
    evidence, sources = _presented_excerpts()
    altered = QUOTE.replace("engines", "Engines")
    answer = f'«{altered}» [[source:{sources[1]["id"]}]]'
    assert evidence.quote_bindings(answer)[0]["reason"] == "quote_not_in_cited_excerpt"
    assert evidence.citations(answer)[0]["status"] == "unresolved"


def test_other_page_excerpt_cannot_repair_the_cited_page():
    evidence, sources = _presented_excerpts()
    other = make_source(
        run_id="quote-binding", tool="web_fetch", url="https://example.org/unrelated",
        status="excerpt", quote="This is a different page.", quote_verified=True,
    )
    evidence.record_tool_result(
        tool_name="web_fetch", arguments={"url": other["url"]}, execution_status="ok",
        output={"ok": True, "sources": [other]}, text_result=format_source(other), state_changed=False,
    )
    evidence.mark_sources_presented([{"role": "tool", "content": "\n".join(
        format_source(source) for source in [*sources, other])}])
    answer = f'«{QUOTE}» [[source:{other["id"]}]]'
    assert evidence.citations(answer)[0]["reason"] == "quote_not_in_cited_excerpt"


def test_two_locally_cited_quotes_do_not_cross_bind_their_receipts():
    evidence, sources = _presented_excerpts()
    wrong, correct = sources
    answer = (f'«Introductory documentation.» [[source:{wrong["id"]}]] '
              f'«{QUOTE}» [[source:{correct["id"]}]]')
    assert [binding["status"] for binding in evidence.quote_bindings(answer)] == ["matched", "matched"]
    assert [citation["status"] for citation in evidence.citations(answer)] == ["matched", "matched"]


def test_quote_reference_does_not_capture_later_paraphrase_in_same_paragraph():
    evidence, sources = _presented_excerpts()
    intro, quote = sources
    answer = (f'«{QUOTE}» [[source:{quote["id"]}]]. '
              f'Другая выдержка содержит вступление. [[source:{intro["id"]}]]')
    assert evidence.quote_bindings(answer)[0]["source_ids"] == [quote["id"]]
    assert all(citation["status"] == "matched" for citation in evidence.citations(answer))


def test_one_valid_marker_does_not_hide_another_wrong_marker_for_same_quote():
    evidence, sources = _presented_excerpts()
    answer = f'«{QUOTE}» [[source:{sources[0]["id"]}]] [[source:{sources[1]["id"]}]]'
    assert evidence.quote_bindings(answer)[0]["status"] == "unresolved"
    assert [citation["status"] for citation in evidence.citations(answer)] == ["unresolved", "matched"]


def test_later_reuse_of_marker_does_not_hide_a_wrong_quote_binding():
    evidence, sources = _presented_excerpts()
    marker = f'[[source:{sources[1]["id"]}]]'
    answer = f'«Invented quote.» {marker}\n\n«{QUOTE}» {marker}'
    assert len(evidence.citations(answer)) == 1
    assert evidence.citations(answer)[0]["status"] == "unresolved"


def test_current_presentation_is_required_after_compaction():
    evidence, sources = _presented_excerpts()
    assert evidence.sources[1]["presented"] is True
    evidence.mark_sources_presented([])
    answer = f'«{QUOTE}» [[source:{sources[1]["id"]}]]'
    assert evidence.sources[1]["presented"] is True  # Historical delivery alone is insufficient.
    assert evidence.quote_bindings(answer)[0]["reason"] == "quote_source_unavailable"


@pytest.mark.parametrize("change", ["quote", "excerpt_hash", "id"])
def test_stale_or_forged_receipt_cannot_bind_quote(change):
    _, sources = _presented_excerpts()
    stale = deepcopy(sources[1])
    stale[change] = "tampered"
    evidence = RunEvidence(sources=[stale])
    evidence.mark_sources_presented([{"role": "tool", "content": format_source(stale)}])
    answer = f'«{QUOTE}» [[source:{stale["id"]}]]'
    assert evidence.quote_bindings(answer)[0]["reason"] == "quote_source_unavailable"
    assert evidence.citations(answer)[0]["status"] == "unresolved"


def test_valid_but_unverified_excerpt_is_not_exact_quote_proof():
    _, sources = _presented_excerpts()
    source = sources[1]
    source["quote_verified"] = False
    evidence = RunEvidence(sources=[source])
    evidence.mark_sources_presented([{"role": "tool", "content": format_source(source)}])
    answer = f'«{QUOTE}» [[source:{source["id"]}]]'
    assert evidence.citations(answer)[0]["reason"] == "quote_source_unverified"


def test_uncited_quoted_terminology_is_not_bound_to_other_paragraph():
    evidence, sources = _presented_excerpts()
    answer = f'The term “keep_only” names a setting.\n\nRead more [[source:{sources[0]["id"]}]]'
    assert evidence.quote_bindings(answer)[0]["status"] == "unbound"
    assert evidence.citations(answer)[0]["status"] == "matched"


def test_missing_marker_and_code_examples_do_not_invent_bindings():
    evidence, sources = _presented_excerpts()
    assert evidence.quote_bindings(f'«{QUOTE}»')[0]["status"] == "unbound"
    assert evidence.citations(f'«{QUOTE}» [[source:missing]]')[0]["reason"] == "quote_source_unavailable"
    marker = f'[[source:{sources[1]["id"]}]]'
    assert evidence.quote_bindings(f'```\n«{QUOTE}» {marker}\n```') == []
    assert evidence.quote_bindings(f'`«{QUOTE}» {marker}`') == []


def _record(evidence, tool, arguments, *, status="ok", output=None, state_changed=False):
    evidence.record_tool_result(
        tool_name=tool, arguments=arguments, execution_status=status,
        output=output or {}, text_result="", state_changed=state_changed,
    )


def test_search_observation_needs_success_and_real_discovery_not_ok_alone():
    evidence = RunEvidence()
    discovered = make_source(
        run_id="quote-binding", tool="web_search", url="https://example.org/settings", status="discovered",
    )
    _record(evidence, "web_search", {"query": "settings"}, output={"ok": True})
    _record(evidence, "web_search", {"query": "settings"}, status="error",
            output={"ok": True, "sources": [discovered]})
    _record(evidence, "web_search", {"query": "settings"},
            output={"ok": True, "sources": [discovered], "partial": True})
    assert evidence.web_operations == []
    _record(evidence, "web_search", {"query": " settings "}, output={"ok": True, "sources": [discovered]})
    assert evidence.web_operations == [{"tool_name": "web_search", "queries": ["settings"],
                                        "source_ids": [discovered["id"]]}]
    assert sum(row["query_count"] for row in evidence.tool_operations) == 4


def test_failed_repeated_attempts_store_and_runtime_operation_are_observed():
    evidence = RunEvidence()
    _record(evidence, "web_search", {"queries": ["first", "first"]}, status="error")
    _record(evidence, "web_fetch", {"urls": ["https://example.org/a", "https://example.org/a"], "store": True},
            status="error")
    _record(evidence, "mcp", {"action": "start", "server_id": "atlas"})
    assert evidence.operations_complete
    assert evidence.tool_operations[0]["query_count"] == 2
    assert evidence.tool_operations[1]["read_urls"] == ["https://example.org/a"] * 2
    assert evidence.tool_operations[1]["store"] is True
    assert evidence.tool_operations[1]["execution_status"] == "error"
    assert evidence.tool_operations[2]["operation"] == "start"
    snapshot = evidence.tool_operations
    snapshot[1]["read_urls"].clear()
    assert len(evidence.tool_operations[1]["read_urls"]) == 2


@pytest.mark.parametrize("tool,args", [
    ("web_search", {"queries": "not-a-list"}),
    ("web_search", {"query": "x" * 4097}),
    ("web_fetch", {"urls": [None]}),
    ("web_fetch", {"urls": ["https://example.org/a"] * 31}),
])
def test_malformed_or_oversized_attempt_history_fails_closed(tool, args):
    evidence = RunEvidence()
    _record(evidence, tool, args, status="error")
    assert evidence.operations_complete is False


def test_imported_source_history_and_operation_overflow_fail_closed():
    _, sources = _presented_excerpts()
    assert RunEvidence(sources=sources).operations_complete is False
    evidence = RunEvidence()
    for _ in range(257):
        _record(evidence, "mcp", {"action": "list"})
    assert len(evidence.tool_operations) == 256
    assert evidence.operations_complete is False


def _presented_news_excerpt():
    # Live Kazakhstan run 2026-10-03: names were written «Хазар Қорғаны — 2026»
    # while the excerpt spells "Хазар қорғаны-2026".
    source = make_source(
        run_id="quote-names", tool="web_fetch", url="https://example.org/news",
        status="excerpt", quote_verified=True,
        quote=('Во время учений "Хазар қорғаны-2026" из-за резкого ухудшения погоды в море '
               'оказались 17 человек. Погибшие награждены орденом "Айбын" II степени.'),
    )
    evidence = RunEvidence()
    evidence.record_tool_result(
        tool_name="web_fetch", arguments={"url": source["url"]}, execution_status="ok",
        output={"ok": True, "sources": [source]}, text_result=format_source(source), state_changed=False,
    )
    evidence.mark_sources_presented([{"role": "tool", "content": format_source(source)}])
    return evidence, source


def test_short_guillemet_names_are_not_quotes_unless_quotes_were_requested():
    evidence, source = _presented_news_excerpt()
    answer = f'Во время учений «Хазар Қорғаны — 2026» погибли 14 человек [[source:{source["id"]}]].'
    assert evidence.citations(answer, skip_names=True)[0]["status"] == "matched"
    binding = evidence.quote_bindings(answer, skip_names=True)[0]
    assert binding["status"] == "matched" and binding["name"] is True
    # Strictness is unchanged when the user asked for quotations.
    assert evidence.citations(answer)[0]["reason"] == "quote_not_in_cited_excerpt"


def test_name_bound_to_invented_source_id_is_still_caught_and_only_marker_dropped():
    from app.application.code_agent.run_evidence import drop_unverified_quotes
    evidence, source = _presented_news_excerpt()
    answer = (f'Награда — орден «Айбын» [[source:{source["id"]}]].\n\n'
              'Россия приостановила ввоз мороженого «Шин-Лайн» [[source:web_search_27]].')
    bindings = evidence.quote_bindings(answer, skip_names=True)
    assert [b["status"] for b in bindings] == ["matched", "unresolved"]
    assert bindings[1]["reason"] == "quote_source_unavailable"
    result = drop_unverified_quotes(answer, bindings)
    assert "мороженого «Шин-Лайн»." in result
    assert "web_search_27" not in result
    assert f'«Айбын» [[source:{source["id"]}]]' in result


def test_sentence_quotes_stay_checked_even_when_names_are_skipped():
    evidence, source = _presented_news_excerpt()
    answer = f'«В море оказались 18 человек.» [[source:{source["id"]}]]'
    assert evidence.citations(answer, skip_names=True)[0]["reason"] == "quote_not_in_cited_excerpt"


def test_typography_is_normalized_but_letters_stay_exact():
    evidence, source = _presented_news_excerpt()
    marker = f'[[source:{source["id"]}]]'
    typographic = 'Во время учений “Хазар қорғаны — 2026” из-за резкого ухудшения погоды в море'
    assert evidence.quote_bindings(f'«{typographic}» {marker}')[0]["status"] == "matched"
    altered = typographic.replace("погоды", "Погоды")
    assert evidence.quote_bindings(f'«{altered}» {marker}')[0]["reason"] == "quote_not_in_cited_excerpt"


def test_drop_unverified_quotes_keeps_the_rest_of_the_answer():
    from app.application.code_agent.run_evidence import (
        UNVERIFIED_QUOTE_NOTE, UNVERIFIED_QUOTE_PLACEHOLDER, drop_unverified_quotes,
    )
    evidence, source = _presented_news_excerpt()
    marker = f'[[source:{source["id"]}]]'
    answer = (f"Погибли 14 военнослужащих {marker}.\n\n"
              f"По словам источника, «в море оказались 18 человек.» {marker}\n\nИтог недели.")
    result = drop_unverified_quotes(answer, evidence.quote_bindings(answer))
    assert result.startswith(f"Погибли 14 военнослужащих {marker}.")
    assert "18 человек" not in result
    assert f"{UNVERIFIED_QUOTE_PLACEHOLDER}\n\nИтог недели." in result
    assert result.endswith(UNVERIFIED_QUOTE_NOTE)
    assert result.count(marker) == 1


@pytest.mark.parametrize("message,expected", [
    ("Нужны короткие сводки по Казахстану за неделю", False),
    ("Приведи дословную цитату из статьи", True),
    ("Дай цитату до 15 слов", True),
    ("Quote the documentation verbatim", True),
])
def test_explicit_quote_request(message, expected):
    from app.application.code_agent.answer_contracts import explicit_quote_request
    assert explicit_quote_request(message) is expected
