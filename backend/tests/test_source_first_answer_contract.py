"""Literal source-first rendering proves membership, never semantic support."""

import json

import pytest

from app.application.code_agent.answer_contracts import parse_source_first_answer
from app.application.web_evidence.receipts import make_source, source_ids


def source(quote, *, status="excerpt", verified=True):
    return make_source(run_id="source-first-test", tool="web_fetch",
        url="https://example.org/report", status=status, quote=quote,
        quote_verified=verified)


def fact(receipt, quote=None, translation=""):
    return {"source_id": receipt["id"], "quote": receipt["quote"] if quote is None else quote,
            "translation": translation}


def payload(facts, *, needs_more=False):
    return json.dumps({"facts": facts, "needs_more_reading": needs_more}, ensure_ascii=False)


def test_russian_output_is_actual_span_and_ignores_model_translation():
    receipt = source("Порывы ветра временами могут достигать 16–21 м/с.")
    result = parse_source_first_answer(payload([fact(receipt, translation="Сегодня произошло ЧП.")]),
                                      presented_sources=[receipt])
    assert result.status == "degraded"
    assert result.issues == ("fact:1:translation_ignored",)
    assert result.text == ("Из прочитанных источников:\n\n```text\n" + receipt["quote"]
                           + "\n```\n\n[[source:" + receipt["id"] + "]]")
    assert "Сегодня произошло ЧП" not in result.text
    assert not result.needs_more_reading


def test_whitespace_only_match_renders_the_original_contiguous_span():
    actual = "В горных\u00a0зонах  временами ожидаются\nпорывы ветра 16–21 м/с."
    receipt = source("До фрагмента.\n\n" + actual + "\n\nПосле фрагмента.")
    requested = "В горных зонах временами ожидаются порывы ветра 16–21 м/с."
    result = parse_source_first_answer(payload([fact(receipt, requested)]), presented_sources=[receipt])
    assert result.status == "ready" and result.issues == ()
    assert "```text\n" + actual + "\n```" in result.text
    assert requested not in result.text
    assert "До фрагмента" not in result.text and "После фрагмента" not in result.text


def test_latin_translation_is_separate_from_the_visible_actual_quote():
    receipt = source("For non-severe influenza with low probability of bacterial co-infection, do not use antibiotics.")
    translation = "При нетяжёлом гриппе с низкой вероятностью бактериальной коинфекции антибиотики не применяют."
    result = parse_source_first_answer(payload([fact(receipt, translation=translation)]),
                                      presented_sources=[receipt])
    assert result.status == "ready"
    assert result.text.index(translation) < result.text.index(receipt["quote"])
    assert "Перевод цитаты:\n\n```text\n" + translation + "\n```" in result.text
    assert "Оригинал:\n\n```text\n" + receipt["quote"] + "\n```" in result.text
    assert source_ids(result.text) == [receipt["id"]]


def test_translation_and_source_membership_are_not_semantic_certification():
    receipt = source("High-risk patients may need treatment.")
    result = parse_source_first_answer(payload([fact(receipt, translation="Всем нужно лечение.")]),
                                      presented_sources=[receipt])
    # A matching original stays visible; this deterministic parser cannot certify
    # translation equivalence, selected scope, relevance or answer completeness.
    assert result.status == "ready"
    assert receipt["quote"] in result.text and "Всем нужно лечение." in result.text


@pytest.mark.parametrize("quote", ["Было 18 наблюдений.", "было 17 наблюдений.",
                                   "Было 17 наблюдений!", "Было 17 наблюдений…"])
def test_literal_changes_drop_only_the_unsupported_fact(quote):
    receipt = source("Было 17 наблюдений.\n\nРезультат предварительный.")
    result = parse_source_first_answer(payload([fact(receipt, quote), fact(receipt, "Результат предварительный.")]),
                                      presented_sources=[receipt])
    assert result.status == "degraded"
    assert result.issues == ("fact:1:quote_not_in_presented_source",)
    assert quote not in result.text
    assert "Результат предварительный." in result.text


def test_unknown_source_does_not_discard_a_valid_subset():
    receipt = source("Прочитанный факт.")
    unknown = {"source_id": "w_unread", "quote": "Непрочитанный факт.", "translation": ""}
    result = parse_source_first_answer(payload([unknown, fact(receipt)]), presented_sources=[receipt])
    assert result.status == "degraded" and source_ids(result.text) == [receipt["id"]]
    assert "Непрочитанный факт." not in result.text


@pytest.mark.parametrize("status,verified", [("discovered", True), ("fetched", True),
                                            ("failed", True), ("excerpt", False)])
def test_nonread_or_unverified_receipts_cannot_bind(status, verified):
    receipt = source("Слова из поискового сниппета.", status=status, verified=verified)
    result = parse_source_first_answer(payload([fact(receipt)]), presented_sources=[receipt])
    assert result.status == "invalid" and result.text == ""
    assert result.issues == ("fact:1:quote_not_in_presented_source",)


def test_tampered_or_unpresented_receipts_cannot_bind():
    receipt = source("Прочитанный факт.")
    modified = {**receipt, "quote": "Подменённый факт."}
    for presented in ([], [modified]):
        result = parse_source_first_answer(payload([fact(receipt)]), presented_sources=presented)
        assert result.status == "invalid" and result.text == ""


@pytest.mark.parametrize("needs_more,status", [(True, "need_more"), (False, "invalid")])
def test_empty_facts_do_not_manufacture_an_answer(needs_more, status):
    result = parse_source_first_answer(payload([], needs_more=needs_more), presented_sources=[])
    assert result.status == status and result.text == ""
    assert result.needs_more_reading is needs_more


def test_needs_more_preserves_valid_selection_without_claiming_complete():
    receipt = source("Частичный факт.")
    result = parse_source_first_answer(payload([fact(receipt)], needs_more=True), presented_sources=[receipt])
    assert result.status == "degraded" and result.needs_more_reading
    assert receipt["quote"] in result.text


def test_duplicate_selection_is_not_rendered_twice():
    receipt = source("Повторять факт не нужно.")
    result = parse_source_first_answer(payload([fact(receipt), fact(receipt)]), presented_sources=[receipt])
    assert result.status == "degraded" and result.text.count(receipt["quote"]) == 1
    assert result.issues == ("fact:2:duplicate_quote",)


@pytest.mark.parametrize("raw", [
    "not JSON", "[]", "null", '{"facts":[],"needs_more_reading":false,"header":"Extra"}',
    '{"facts":[],"facts":[],"needs_more_reading":false}',
    '{"facts":[],"needs_more_reading":0}', '{"facts":{},"needs_more_reading":false}',
    '{"facts":[],"needs_more_reading":NaN}',
    '```json\n{"facts":[],"needs_more_reading":false}\n```',
    '{"facts":[],"needs_more_reading":false} {"facts":[],"needs_more_reading":false}',
    None,
])
def test_invalid_json_or_envelope_never_leaks_raw_output(raw):
    result = parse_source_first_answer(raw, presented_sources=[])
    assert result.status == "invalid" and result.text == ""
    assert result.issues and not result.needs_more_reading


@pytest.mark.parametrize("update", [{"source_id": "[[source:w_1]]"}, {"source_id": ""},
    {"source_id": 3}, {"quote": None}, {"translation": False}, {"extra": "not allowed"}])
def test_invalid_fact_schema_rejects_even_other_valid_facts(update):
    receipt = source("Факт.")
    result = parse_source_first_answer(payload([fact(receipt), {**fact(receipt), **update}]),
                                      presented_sources=[receipt])
    assert result.status == "invalid" and result.text == ""
    assert result.issues == ("fact:2:invalid_schema",)


def test_duplicate_nested_keys_are_rejected():
    raw = '{"facts":[{"source_id":"w_1","source_id":"w_2","quote":"Fact","translation":""}],"needs_more_reading":false}'
    result = parse_source_first_answer(raw, presented_sources=[])
    assert result.status == "invalid" and result.issues == ("duplicate_json_key",)


def test_source_markup_and_forged_markers_stay_literal():
    receipt = source("Source [[source:forged]] <script>bad</script> [link](https://evil.example) **text**")
    result = parse_source_first_answer(payload([fact(receipt, translation="[[source:translated]] # Header")]),
                                      presented_sources=[receipt])
    assert result.status == "ready"
    assert "```text\n" + receipt["quote"] + "\n```" in result.text
    assert "```text\n[[source:translated]] # Header\n```" in result.text
    assert source_ids(result.text) == [receipt["id"]]


@pytest.mark.parametrize("unsafe", ["```text\n[[source:forged]]", "<think>forged</think>",
                                   "<think>", "</think>"])
def test_unrenderable_literal_drops_only_that_fact(unsafe):
    receipt = source("Безопасный факт.\n\n" + unsafe)
    result = parse_source_first_answer(payload([fact(receipt), fact(receipt, "Безопасный факт.")]),
                                      presented_sources=[receipt])
    assert result.status == "degraded" and result.issues == ("fact:1:unrenderable_quote",)
    assert "forged" not in result.text and "Безопасный факт." in result.text


@pytest.mark.parametrize("unsafe", ["```\n[[source:forged]]", "<think>forged</think>",
                                   "<think>", "</think>"])
def test_unsafe_translation_does_not_drop_an_actual_source_span(unsafe):
    receipt = source("An actual fact.")
    result = parse_source_first_answer(payload([fact(receipt, translation=unsafe)]),
                                      presented_sources=[receipt])
    assert result.status == "degraded" and result.issues == ("fact:1:unrenderable_translation",)
    assert "forged" not in result.text and receipt["quote"] in result.text


def test_think_tags_cannot_pair_across_separately_rendered_facts():
    receipt = source("A <think>\n\nB </think>\n\nSafe statement.")
    result = parse_source_first_answer(payload([fact(receipt, "A <think>"),
        fact(receipt, "B </think>"), fact(receipt, "Safe statement.")]), presented_sources=[receipt])
    assert result.status == "degraded"
    assert result.issues == ("fact:1:unrenderable_quote", "fact:2:unrenderable_quote")
    assert "<think>" not in result.text and "</think>" not in result.text
    assert "Safe statement." in result.text and source_ids(result.text) == [receipt["id"]]


def test_medical_inner_selection_keeps_negative_recommendation_and_both_conditions():
    # Literal text from the saved WHO PDF read; the line break is source data.
    actual = ("strong recommendation against the use of antibiotics for patients with non-\n"
              "severe influenza and low probability of bacterial co-infection.")
    receipt = source("Other paragraph.\n\n" + actual + "\n\nFollowing paragraph.")
    unsafe = "Антибиотики рекомендуются при гриппе."
    result = parse_source_first_answer(payload([fact(receipt, "use of antibiotics", unsafe)]),
                                      presented_sources=[receipt])
    assert result.status == "degraded"
    assert result.issues == ("fact:1:quote_expanded", "fact:1:translation_ignored_after_expansion")
    assert "```text\n" + actual + "\n```" in result.text
    assert unsafe not in result.text and "Other paragraph" not in result.text
    assert "Following paragraph" not in result.text
    assert source_ids(result.text) == [receipt["id"]]


def test_scientific_numeric_selection_keeps_distance_uncertainty_and_observation_context():
    actual = ("The signal sweeps upwards in frequency from 35 to 250 Hz with a peak gravitational-wave "
              "strain of $1.0 \\times 10^{-21}$.\nThe source lies at a luminosity distance of "
              "$410^{+160}_{-180}$ Mpc corresponding to a redshift $z = 0.09^{+0.03}_{-0.04}$.")
    receipt = source(actual)
    unsafe = "Детекторы напрямую измерили расстояние 410 Мпк."
    result = parse_source_first_answer(payload([fact(receipt, "410", unsafe)]), presented_sources=[receipt])
    assert result.status == "degraded" and "quote_expanded" in result.issues[0]
    assert "```text\n" + actual.split("\n", 1)[1] + "\n```" in result.text and unsafe not in result.text
    assert "$410^{+160}_{-180}$" in result.text and "$z = 0.09^{+0.03}_{-0.04}$" in result.text
    assert "luminosity distance" in result.text


def test_inner_sentence_cannot_drop_conditions_on_next_wrapped_line():
    actual = ("Use antibiotics only if bacterial co-infection is confirmed.\n"
              "This does not apply to isolated viral influenza.")
    receipt = source("Other paragraph.\n\n" + actual + "\n\nFollowing paragraph.")
    result = parse_source_first_answer(payload([fact(receipt, "Use antibiotics")]), presented_sources=[receipt])
    assert result.status == "degraded" and result.issues == ("fact:1:quote_expanded",)
    assert "```text\n" + actual + "\n```" in result.text


def test_user_quote_limit_is_checked_on_expanded_original_before_fenced_render():
    actual = ("strong recommendation against the use of antibiotics for patients with non-\n"
              "severe influenza and low probability of bacterial co-infection.")
    receipt = source(actual)
    result = parse_source_first_answer(payload([fact(receipt, "use of antibiotics")]),
                                      presented_sources=[receipt], quote_word_limit=15)
    assert result.status == "invalid" and result.text == ""
    assert result.issues == ("fact:1:quote_expanded", "fact:1:quote_word_limit_exceeded")
    assert not result.needs_more_reading


def test_user_quote_limit_keeps_other_short_whole_paragraphs_without_truncating_context():
    long = source("This recommendation applies only to patients in the measured subgroup and does not "
                  "establish a treatment indication for the general population.")
    short = source("Antibiotics do not treat influenza.")
    result = parse_source_first_answer(payload([fact(long), fact(short)]),
                                      presented_sources=[long, short], quote_word_limit=6)
    assert result.status == "degraded" and result.issues == ("fact:1:quote_word_limit_exceeded",)
    assert long["quote"] not in result.text and short["quote"] in result.text
    assert source_ids(result.text) == [short["id"]]


def test_user_quote_limit_also_applies_to_the_optional_rendered_translation():
    receipt = source("Antibiotics do not treat influenza.")
    translation = "Антибиотики не применяют для лечения гриппа, вызываемого вирусной инфекцией."
    result = parse_source_first_answer(payload([fact(receipt, translation=translation)]),
                                      presented_sources=[receipt], quote_word_limit=6)
    assert result.status == "degraded" and result.issues == ("fact:1:translation_quote_word_limit_exceeded",)
    assert receipt["quote"] in result.text and translation not in result.text


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "15"])
def test_invalid_user_quote_limit_does_not_establish_an_implicit_limit(limit):
    receipt = source("A fact.")
    with pytest.raises(ValueError, match="quote_word_limit must be a positive integer or None"):
        parse_source_first_answer(payload([fact(receipt)]), presented_sources=[receipt], quote_word_limit=limit)


def test_expanded_quotes_from_same_paragraph_are_not_rendered_repeatedly():
    actual = "This applies only to the measured subgroup. It does not apply to every patient."
    receipt = source("Other paragraph.\n\n" + actual + "\n\nFollowing paragraph.")
    result = parse_source_first_answer(payload([fact(receipt, "measured subgroup"),
                                               fact(receipt, "every patient")]), presented_sources=[receipt])
    assert result.status == "degraded"
    assert result.text.count(actual) == 1 and "fact:2:duplicate_quote" in result.issues


# Actual saved read excerpts; fixtures remain independent of scratch at test time.
_NHS_FLATTENED_READ = "Antibiotics are used to treat or prevent some types of bacterial infection. They work by killing bacteria or preventing them from spreading. But they do not work for everything.\nMany mild bacterial infections get better on their own without using antibiotics.\nAntibiotics do not work for viral infections such as colds and flu, and most coughs.\nAntibiotics are no longer routinely used to treat:\nchest infections\near infections in children\nsore throats\nWhen it comes to antibiotics, take your doctor's advice on whether you need them or not. Antibiotic resistance is a big problem – taking antibiotics when you do not need them can mean they will not work for you in the future.\nWhen antibiotics are needed\nAntibiotics may be used to treat bacterial infections that:\nare unlikely to clear up without antibiotics\ncould take too long to clear without treatment\ncarry a risk of more serious complications\ncould infect others\nYou may still be infectious after starting a course of antibiotics. Depending on the infection and how it's treated, it can take between 48 hours and 14 days to stop being infectious. Ask a GP or pharmacist for advice.\nPeople at a high risk of infection may also be given antibiotics as a precaution, known as antibiotic prophylaxis.\nRead more about\nwhen antibiotics are used\nand\nwhy antibiotics are not routinely used to treat infections\n.\nHow to take antibiotics\nTake antibiotics as directed on the packet or the patient information leaflet that comes with the medicine"
_HIGHLAND_FLATTENED_READ = "Influenza (Flu) Treatment (Antimicrobial)\nTAM (Treatments and Medicines) NHS Highland\nNHS Highland\n!\nWarning\nWhat's new / Latest updates\n11/02/26 V2.1:\nAdded: Link to CMO letter: CMO2025(21)\n24/11/25 V2\n:\nUKHSA updated national guidance on influenza treatment and prophylaxis on 4th November 2025 from which many of these changes have been taken.\nThe list of patients at risk of severe influenza or hospitalisation along with examples of patients regarded as being immunosuppressed have been expanded.\nRecommendation for use of antibiotics for secondary bacterial infection in a patient with influenza should be based on clinical assessment.\nIV zanamivir has been in short supply in recent years so should only be considered if oral or enteral oseltamivir or inhaled zanamivir cannot be administered, noting the evidence for efficacy of antivirals in severe influenza is based on oseltamivir.\nNHS Highland Renal Team recommend using Renal Drug Database dosing advice for oseltamivir in renal impairment or renal replacement therapies.\nDosing in renal impairment has been added to the guidance.\n11/12/24 V2.1:\nError amended: Prophylaxis dose of oseltamivir amended from twice daily to once daily\nCMO2025(21):\nSeasonal influenza 2025-26: Current epidemiology, potential implications and use of influenza antivirals\nAnnual vaccination is essential for all those at risk of influenza\n. See\nPublic Health Scotland\nand\nUKHSA\nrecommendations for treatment and prophylaxis of Influenza. For ot"
_SCIENCE_FLATTENED_READ = "General Relativity and Quantum Cosmology\narXiv:1602.03837\n(gr-qc)\n[Submitted on 11 Feb 2016]\nTitle:\nObservation of Gravitational Waves from a Binary Black Hole Merger\nAuthors:\nThe\nLIGO Scientific Collaboration\n, the\nVirgo Collaboration\nView a PDF of the paper titled Observation of Gravitational Waves from a Binary Black Hole Merger, by The LIGO Scientific Collaboration and 1 other authors\nView PDF\nAbstract:\nOn September 14, 2015 at 09:50:45 UTC the two detectors of the Laser Interferometer Gravitational-Wave Observatory simultaneously observed a transient gravitational-wave signal. The signal sweeps upwards in frequency from 35 to 250 Hz with a peak gravitational-wave strain of $1.0 \\times 10^{-21}$. It matches the waveform predicted by general relativity for the inspiral and merger of a pair of black holes and the ringdown of the resulting single black hole. The signal was observed with a matched-filter signal-to-noise ratio of 24 and a false alarm rate estimated to be less than 1 event per 203 000 years, equivalent to a significance greater than 5.1 {\\sigma}. The source lies at a luminosity distance of $410^{+160}_{-180}$ Mpc corresponding to a redshift $z = 0.09^{+0.03}_{-0.04}$. In the source frame, the initial black hole masses are $36^{+5}_{-4} M_\\odot$ and $29^{+4}_{-4} M_\\odot$, and the final black hole mass is $62^{+4}_{-4} M_\\odot$, with $3.0^{+0.5}_{-0.5} M_\\odot c^2$ radiated in gravitational waves. All uncertainties define 90% credible\nthis http URL\nobservations "


def test_whole_nhs_sentence_in_actual_flattened_read_retains_translation():
    assert len(_NHS_FLATTENED_READ) == 1493 and "\n\n" not in _NHS_FLATTENED_READ
    receipt = source(_NHS_FLATTENED_READ)
    selected = "Antibiotics do not work for viral infections such as colds and flu, and most coughs."
    translation = ("Антибиотики не действуют на вирусные инфекции, такие как простуда и грипп, "
                   "а также на большинство случаев кашля.")
    result = parse_source_first_answer(payload([fact(receipt, selected, translation)]),
                                      presented_sources=[receipt])
    actual = next(line for line in _NHS_FLATTENED_READ.splitlines() if line.startswith("Antibiotics do not work"))
    assert result.status == "ready" and result.issues == ()
    assert "```text\n" + actual + "\n```" in result.text and translation in result.text
    assert "Antibiotics are used to treat" not in result.text
    assert "antibiotic prophylaxis" not in result.text


def test_whole_highland_clinical_assessment_sentence_retains_translation():
    assert "\n\n" not in _HIGHLAND_FLATTENED_READ
    receipt = source(_HIGHLAND_FLATTENED_READ)
    selected = ("Recommendation for use of antibiotics for secondary bacterial infection in a patient "
                "with influenza should be based on clinical assessment.")
    translation = ("Рекомендация по применению антибиотиков для вторичной бактериальной инфекции "
                   "у пациента с гриппом должна основываться на клинической оценке.")
    result = parse_source_first_answer(payload([fact(receipt, selected, translation)]),
                                      presented_sources=[receipt])
    assert result.status == "ready" and result.issues == ()
    assert "```text\n" + selected + "\n```" in result.text and translation in result.text
    assert "IV zanamivir" not in result.text and "CMO2025" not in result.text


def test_whole_scientific_distance_sentence_preserves_original_and_translation():
    assert len(_SCIENCE_FLATTENED_READ) == 1500 and "\n\n" not in _SCIENCE_FLATTENED_READ
    receipt = source(_SCIENCE_FLATTENED_READ)
    selected = ("The source lies at a luminosity distance of $410^{+160}_{-180}$ Mpc "
                "corresponding to a redshift $z = 0.09^{+0.03}_{-0.04}$.")
    translation = ("Светимостное расстояние до источника составляет $410^{+160}_{-180}$ Мпк, "
                   "что соответствует красному смещению $z = 0.09^{+0.03}_{-0.04}$.")
    result = parse_source_first_answer(payload([fact(receipt, selected, translation)]),
                                      presented_sources=[receipt])
    assert result.status == "ready" and result.issues == ()
    assert "```text\n" + selected + "\n```" in result.text and translation in result.text
    assert "203 000 years" not in result.text and "initial black hole masses" not in result.text


def test_inner_who_selection_without_paragraph_breaks_retains_full_wrapped_sentence():
    actual = ("strong recommendation against the use of antibiotics for patients with non-\n"
              "severe influenza and low probability of bacterial co-infection.")
    receipt = source("Previous sentence.\n" + actual + "\nFollowing sentence.")
    unsafe = "Антибиотики рекомендуются при гриппе."
    result = parse_source_first_answer(payload([fact(receipt, "use of antibiotics", unsafe)]),
                                      presented_sources=[receipt])
    assert result.status == "degraded"
    assert result.issues == ("fact:1:quote_expanded", "fact:1:translation_ignored_after_expansion")
    assert "```text\n" + actual + "\n```" in result.text and unsafe not in result.text
    assert "Previous sentence" not in result.text and "Following sentence" not in result.text


def test_numeric_selection_does_not_treat_decimal_points_as_sentence_boundaries():
    actual = "The redshift is 0.09 and the distance estimate is 410.5 Mpc."
    receipt = source("Previous sentence.\n" + actual + "\nFollowing sentence.")
    result = parse_source_first_answer(payload([fact(receipt, "410.5")]), presented_sources=[receipt])
    assert result.status == "degraded" and result.issues == ("fact:1:quote_expanded",)
    assert "```text\n" + actual + "\n```" in result.text
    assert "Previous sentence" not in result.text and "Following sentence" not in result.text


@pytest.mark.parametrize("ending", [".", "?", "!"])
def test_flattened_inner_selection_uses_complete_sentence_with_wrapped_conditions(ending):
    actual = "Use antibiotics only if bacterial co-infection\nis confirmed" + ending
    receipt = source("Previous sentence.\n" + actual + "\nFollowing sentence.")
    result = parse_source_first_answer(payload([fact(receipt, "Use antibiotics")]), presented_sources=[receipt])
    assert result.status == "degraded" and result.issues == ("fact:1:quote_expanded",)
    assert "```text\n" + actual + "\n```" in result.text
    assert "Previous sentence" not in result.text and "Following sentence" not in result.text


_NEWS_FLATTENED_READ = "Последние новости\nВ Алматинской области открыли завод по производству напитков мощностью до 1 млрд литров в год\n16:53\nСколько стоит валюта в обменниках Алматы 1 октября\n15:19\nЖителей и гостей Алматы предупредили о повышенном загрязнении воздуха\n14:08\nСпасатели помогли ребенку, оставшемуся одному в квартире в Конаеве\nвидео\n11:58\nВ Алматинской области ожидается сильный ветер и чрезвычайная пожарная опасность\n10:43\nАлматинский таэквондист стал чемпионом Азиатских игр\n18:12\n2 октября\nВ Казахстане началась вакцинация против гриппа: закуплено 2,1 млн доз\n17:39\n2 октября\nВ Алматы на три дня ограничат движение по улице Байсеитовой\n15:30\n2 октября\nВ Алматы снесли незаконную пристройку к жилому дому\n14:50\n2 октября\nВ Алматы несколько дней ищут иностранца в горах\nВидео\n13:20\n2 октября\nВ Алматы двух мужчин арестовали за видео с нецензурной бранью\n12:08\n2 октября\nМедали из Японии: призеры Азиатских игр вернулись в Алматы\nВидео\n11:01\n2 октября\nВ Алматы задержали мужчину по подозрению в насилии над 9-летним ребенком\n16:56, 2 октября\nВидео\nВ горах Алматы развернули масштабные поиски пропавшего туриста\n15:39, 1 октября\nВ Алматы врачи провели сложную операцию после травмы глаза\n14:30, 1 октября\nВидео\nНа Терренкуре в Алматы появились два плавающих фонтана\n13:17, 1 октября\nКакие скрининги доступны жителям Алматы\n16:55, 28 сентября\nКому вы доверите свою жизнь в критический момент?\nпроголосовало 49 посетителей\nВ Алматы снесли незаконную пристройку к жилому дому\n14:50, 2 октября\nВидео\nВ Алматы неск"


def test_exact_news_headline_and_timestamp_lines_do_not_expand_into_whole_feed():
    receipt = source(_NEWS_FLATTENED_READ)
    selected = ("В Алматинской области ожидается сильный ветер и чрезвычайная пожарная опасность\n"
                "10:43")
    result = parse_source_first_answer(payload([fact(receipt, selected)]), presented_sources=[receipt])
    assert result.status == "ready" and result.issues == ()
    assert "```text\n" + selected + "\n```" in result.text
    assert "завод по производству напитков" not in result.text
    assert "Алматинский таэквондист" not in result.text and "вакцинация против гриппа" not in result.text


def test_exact_news_headline_block_preserves_selected_date_without_unrelated_items():
    receipt = source(_NEWS_FLATTENED_READ)
    selected = "В Алматы несколько дней ищут иностранца в горах\nВидео\n13:20\n2 октября"
    result = parse_source_first_answer(payload([fact(receipt, selected)]), presented_sources=[receipt])
    assert result.status == "ready" and result.issues == ()
    assert "```text\n" + selected + "\n```" in result.text
    assert "В Алматы снесли незаконную пристройку" not in result.text
    assert "нецензурной бранью" not in result.text


def test_actual_scientific_abstract_starts_at_line_and_keeps_four_complete_sentences():
    receipt = source(_SCIENCE_FLATTENED_READ)
    selected = _SCIENCE_FLATTENED_READ.split("Abstract:\n", 1)[1].split(" The source lies", 1)[0]
    translation = ("14 сентября 2015 года в 09:50:45 UTC два детектора наблюдали сигнал; "
                   "частота от 35 до 250 Гц, пиковый strain $1.0 \\times 10^{-21}$. "
                   "Форма соответствует предсказанию ОТО для сближения, слияния и затухания. "
                   "Сигнал-шум 24; оценённая частота ложных тревог менее одного события за "
                   "203000 лет, значимость более $5.1\\sigma$.")
    result = parse_source_first_answer(payload([fact(receipt, selected, translation)]),
                                      presented_sources=[receipt])
    assert result.status == "ready" and result.issues == ()
    assert "```text\n" + selected + "\n```" in result.text and translation in result.text
    assert "View PDF" not in result.text and "luminosity distance" not in result.text


def test_exact_reference_lines_remain_a_literal_block_without_navigation():
    actual = ("Comments:\n16 pages\nJournal reference:\nPhys. Rev. Lett. 116, 061102 (2016)\n"
              "Related DOI\nhttps://doi.org/10.1103/PhysRevLett.116.061102\nSubmission history")
    receipt = source(actual)
    selected = "Journal reference: Phys. Rev. Lett. 116, 061102 (2016)"
    result = parse_source_first_answer(payload([fact(receipt, selected)]), presented_sources=[receipt])
    assert result.status == "ready" and result.issues == ()
    assert "```text\nJournal reference:\nPhys. Rev. Lett. 116, 061102 (2016)\n```" in result.text
    assert "Comments:" not in result.text and "Submission history" not in result.text


def test_complete_line_rule_cannot_remove_a_condition_prefix_on_same_line():
    actual = "Only if bacterial co-infection is confirmed should antibiotics be used."
    receipt = source("Previous statement.\n" + actual + "\nFollowing statement.")
    unsafe = "Антибиотики следует применять."
    result = parse_source_first_answer(payload([fact(receipt, "antibiotics be used.", unsafe)]),
                                      presented_sources=[receipt])
    assert result.status == "degraded"
    assert result.issues == ("fact:1:quote_expanded", "fact:1:translation_ignored_after_expansion")
    assert "```text\n" + actual + "\n```" in result.text and unsafe not in result.text


def test_whole_wrapped_negative_medical_sentence_preserves_all_qualifiers_and_translation():
    actual = ("A strong recommendation against the use of antibiotics for patients with non-\n"
              "severe influenza and low probability of bacterial co-infection.")
    receipt = source("Section heading\n" + actual + "\nOther heading")
    translation = ("Настоятельная рекомендация против применения антибиотиков при нетяжёлом "
                   "гриппе и низкой вероятности бактериальной коинфекции.")
    result = parse_source_first_answer(payload([fact(receipt, actual, translation)]),
                                      presented_sources=[receipt])
    assert result.status == "ready" and result.issues == ()
    assert "```text\n" + actual + "\n```" in result.text and translation in result.text
    assert "Section heading" not in result.text and "Other heading" not in result.text
