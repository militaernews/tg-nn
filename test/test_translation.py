from unittest.mock import MagicMock

import pytest

from bot import translation
from bot.translation import TranslationFailedError, chunk_paragraphs, translate

LOREM = """🇺🇸 Der frühere US-Präsident Clinton bedauert, dass er die Ukraine ermutigt hat, Atomwaffen aufzugeben

„Ich fühle mich persönlich betroffen, weil ich sie (die Ukraine – Anm. d. Red.) dazu gezwungen habe, der Aufgabe von Atomwaffen zuzustimmen. Und keiner von ihnen glaubt, dass Russland zu dieser (Invasion – Anm. d. Red.) gegangen wäre, wenn die Ukraine noch ihre Waffen gehabt hätte“, sagte er.

Laut Clinton wusste er, dass Putin im Gegensatz zu seinem Vorgänger Jelzin kein Analogon des Budapester Memorandums 4. April 2023, unterstützen würde – ein Dokument, das „Sicherheitsgarantien“ vorsah. für Kiew als Gegenleistung für den Verzicht auf Atomwaffen."""


def test_chunk_paragraphs():
    result = chunk_paragraphs(LOREM)
    expected = """🇺🇸 Der frühere US-Präsident Clinton bedauert, dass er die Ukraine ermutigt hat, Atomwaffen aufzugeben

„Ich fühle mich persönlich betroffen, weil ich sie (die Ukraine – Anm. d. Red.) dazu gezwungen habe, der Aufgabe von Atomwaffen zuzustimmen. Und keiner von ihnen glaubt, dass Russland zu dieser (Invasion – Anm. d. Red.) gegangen wäre, wenn die Ukraine noch ihre Waffen gehabt hätte“, sagte er.

Laut Clinton wusste er, dass Putin im Gegensatz zu seinem Vorgänger Jelzin kein Analogon des Budapester Memorandums 4. April 2023, unterstützen würde – ein Dokument, das „Sicherheitsgarantien“ vorsah. für Kiew als Gegenleistung für den Verzicht auf Atomwaffen."""

    print(f"result: {result}")
    assert result == expected


def test_translate_raises_instead_of_posting_untranslated_text_when_all_providers_fail(monkeypatch):
    monkeypatch.setattr(translation, "translator", None)

    class FailingTranslator:
        def __init__(self, *args, **kwargs):
            pass

        def translate(self, *args, **kwargs):
            raise Exception("boom")

    monkeypatch.setattr(translation, "GoogleTranslator", FailingTranslator)
    monkeypatch.setattr(translation, "MyMemoryTranslator", FailingTranslator)
    monkeypatch.setattr(translation, "_translate_via_googletrans",
                        lambda text: (_ for _ in ()).throw(Exception("boom")))
    monkeypatch.setattr(translation, "OPENROUTER_API_KEY", None)

    with pytest.raises(TranslationFailedError):
        translate("This stays in English no matter what.")


def test_translate_falls_back_to_second_google_client_when_first_fails(monkeypatch):
    monkeypatch.setattr(translation, "translator", None)

    class FailingTranslator:
        def __init__(self, *args, **kwargs):
            pass

        def translate(self, *args, **kwargs):
            raise Exception("boom")

    monkeypatch.setattr(translation, "GoogleTranslator", FailingTranslator)
    monkeypatch.setattr(translation, "_translate_via_googletrans", lambda text: "Übersetzt via googletrans")

    my_memory_mock = MagicMock()
    llm_mock = MagicMock()
    monkeypatch.setattr(translation, "MyMemoryTranslator", my_memory_mock)
    monkeypatch.setattr(translation, "_translate_via_llm", llm_mock)

    assert translate("Some English text") == "Übersetzt via googletrans"
    my_memory_mock.assert_not_called()
    llm_mock.assert_not_called()


if __name__ == "__main__":
    test_chunk_paragraphs()
