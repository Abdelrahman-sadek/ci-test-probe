import pytest

from agentkit.arabic import detect_lang, franco_to_arabic, normalize, sentences, stem_ar, tokenize


def test_normalize_fixes_pdf_presentation_forms():
    assert normalize("ﻣﻮﺍﻋﻴﺪ") == normalize("مواعيد")


def test_normalize_digits_letters_diacritics_tanween():
    assert normalize("١٥ أَحْمَد مكتبة إلى ی ک") == "15 احمد مكتبه الي ي ك"
    assert normalize("كتابًا") == "كتاب"


def test_tokenize_drops_arabic_punctuation_and_stopwords():
    assert tokenize("ممكن الخريجين يستعيروا كتب؟ المكتبة الرئيسية، القاهرة") == \
        ["خريج", "يستعيروا", "كتب", "مكتب", "رييس", "قاهر"]


@pytest.mark.parametrize("word,stem", [("المكتبات", "مكتب"), ("والمكتبة", "مكتب"), ("للخريجين", "خريج"),
                                       ("كتابها", "كتاب")])
def test_light10_stemming(word, stem):
    assert stem_ar(normalize(word)) == stem


def test_english_light_stemming_matches_variants():
    assert tokenize("borrowed closing libraries") == tokenize("borrow close library")


def test_sentences_split_arabic_and_english():
    assert sentences("هل تفتح المكتبة؟ نعم. Open daily! Yes") == ["هل تفتح المكتبة؟", "نعم.", "Open daily!", "Yes"]


@pytest.mark.parametrize("text,expected", [("maktaba", "مكتبه"), ("el kotob", "الكتب"), ("ketab", "كتاب")])
def test_franco_to_arabic_candidates(text, expected):
    assert expected in franco_to_arabic(text)


@pytest.mark.parametrize("text,lang", [("مواعيد المكتبة", "ar"), ("3ayez a3raf mawa3id el maktaba", "arabizi"),
                                       ("library hours", "en")])
def test_detect_lang(text, lang):
    assert detect_lang(text) == lang
