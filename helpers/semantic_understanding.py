"""
LLM-based semantic understanding metric for STT accuracy evaluation.

Extracts semantic facts from both the expected and STT transcripts in a single
LLM call, classifies each by verdict (both / expected-only / got-only), and
returns a SemanticMetricResult with a numeric Semantic Error Rate and a rich
HTML representation.

See doc/semantic_understanding_metric.md for full documentation.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from html import escape
from json import JSONDecodeError, loads
from logging import getLogger

from google import genai
from google.genai import types

from helpers.diff_report import CustomMetricResult

logger = getLogger(__name__)

_MODEL_ID = "gemini-4-hydrogen"

# Only 503 (transient overload) is retried; every other status surfaces as the SDK's APIError.
_RETRY_OPTIONS = types.HttpRetryOptions(attempts=2, http_status_codes=[503])

_SYSTEM_PROMPT = """\
Vyhodnocuješ přesnost převodu řeči na text (STT) pro české audio.

Úkol:
- Z referenčního přepisu extrahuj fakta.
- Z výstupu STT extrahuj fakta.
- Klasifikuj každý fakt dle jeho výskytu.

Fakt je jednoduchý výrok: subjekt + predikát + objekt. Složitější věta se může rozpadnout na více výroků.
- Zaměř se na pojmenované entity, události, citáty, čísla, data, přiřazení a konstatování.
- Vyber pouze informace podstatné pro celkové pochopení konverzace, ignoruj výplňová slova, opakování a marginálie.
- Ignoruj interpunkci, rozdíly ve slovosledu, pravopisné chyby a morfologické variace (české skloňování/časování).

Klasifikuj každý fakt verdiktem:
- "both"     — fakt je přítomen v obou textech (referenčním i STT výstupu)
- "expected" — fakt je pouze v referenčním textu (informace ztracená při STT)
- "got"      — fakt je pouze ve výstupu STT (možná halucinace)

Vrať pouze JSON bez markdownu, v tomto schématu:
{
"facts": [
  {"subject": "...", "predicate": "...", "object": "...", "verdict": "both|expected|got"}
]
}

"""

_PROMPT_TEMPLATE = """\
Referenční přepis:
{expected}

Výstup STT:
{got}
"""


class Verdict(str, Enum):
    BOTH = "both"          # fact in both expected and got
    EXPECTED = "expected"  # fact only in expected (missing from STT)
    GOT = "got"            # fact only in got (not in expected)


@dataclass(frozen=True)
class SemanticFact:
    subject: str
    predicate: str
    object: str
    verdict: Verdict


@dataclass(frozen=True)
class SemanticMetricResult(CustomMetricResult):
    """CustomMetricResult with full fact-level detail for semantic analysis.

    score = Semantic Error Rate (SER): missing / expected_total * 100.
    Lower is better, like WER/CER.
    """
    facts: tuple[SemanticFact, ...]

    @property
    def facts_both(self) -> int:
        return _count(self.facts, Verdict.BOTH)

    @property
    def facts_missing(self) -> int:
        return _count(self.facts, Verdict.EXPECTED)

    @property
    def facts_extra(self) -> int:
        return _count(self.facts, Verdict.GOT)

    @property
    def total_expected(self) -> int:
        return self.facts_both + self.facts_missing

    @property
    def total_got(self) -> int:
        return self.facts_both + self.facts_extra

    @property
    def understanding(self) -> float:
        """% of expected facts preserved in got (100 - SER)."""
        return round(100.0 - self.score, 1)

    @property
    def pct_extra(self) -> float:
        """% of got facts not present in expected."""
        return round(self.facts_extra / self.total_got * 100, 1) if self.total_got > 0 else 0.0

    def to_html(self) -> str:
        """Rich HTML fragment: stat cards + grouped fact list."""
        cards = (
            _stat("Semantic Error Rate", f"{self.score:.1f}%",
                  f"{self.facts_missing} missing of {self.total_expected} expected")
            + _stat("Understanding", f"{self.understanding:.1f}%",
                    f"{self.facts_both} preserved of {self.total_expected} expected")
            + _stat("Extra", f"{self.pct_extra:.1f}%",
                    f"{self.facts_extra} facts added vs {self.total_got} in got")
        )
        return (
            '<div class="panel">'
            '<h2>Semantic Understanding</h2>'
            f'<div class="stats">{cards}</div>'
            '<details style="margin-top:12px">'
            '<summary style="cursor:pointer;font-size:13px;color:#555">Fact list</summary>'
            f'<pre style="margin-top:8px">{escape(self.detail)}</pre>'
            '</details>'
            '</div>'
        )


def _count(facts: tuple[SemanticFact, ...], verdict: Verdict) -> int:
    return sum(1 for fact in facts if fact.verdict is verdict)


def _semantic_error_rate(facts: tuple[SemanticFact, ...]) -> float:
    missing = _count(facts, Verdict.EXPECTED)
    expected_total = missing + _count(facts, Verdict.BOTH)
    return round(missing / expected_total * 100, 1) if expected_total else 0.0


def _stat(label: str, value: str, detail: str) -> str:
    return (
        '<div class="stat">'
        f'<div class="stat-label">{escape(label)}</div>'
        f'<div class="stat-value">{escape(value)}</div>'
        f'<div class="stat-detail">{escape(detail)}</div>'
        '</div>'
    )


def _grouped_fact_list(facts: tuple[SemanticFact, ...]) -> str:
    """Plain-text fact list, grouped by verdict, for the report detail and the HTML fact list."""
    group_labels = {
        Verdict.BOTH: "Preserved (both)",
        Verdict.EXPECTED: "Missing from STT (expected only)",
        Verdict.GOT: "Extra in STT (got only)",
    }
    lines: list[str] = []
    for verdict, label in group_labels.items():
        group = [fact for fact in facts if fact.verdict is verdict]
        if group:
            lines.append(f"{label}:")
            lines.extend(f"  \u2022 {fact.subject} {fact.predicate} {fact.object}" for fact in group)
    return "\n".join(lines) if lines else "No facts extracted."


class SemanticUnderstandingAnalyzer:
    """
    Semantic understanding metric using Gemini.

    Usage:
        analyzer = SemanticUnderstandingAnalyzer(api_key=os.getenv("GEMINI_API_KEY"))
        result = await analyzer.compare(expected_text, got_text)
        # result.score           → Semantic Error Rate (0 = perfect, 100 = all missing)
        # result.understanding   → % of expected facts preserved
        # result.pct_extra       → % of got facts not in expected
        # result.to_html()       → rich HTML fragment for the diff report
    """

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(retry_options=_RETRY_OPTIONS))

    async def _generate_json(self, prompt: str) -> dict:
        response = await self._client.aio.models.generate_content(
            model=_MODEL_ID,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                temperature=0.0,
                max_output_tokens=16000,
                response_mime_type="application/json",
            ),
        )
        raw_text = (response.text or "").strip()
        try:
            return loads(raw_text)
        except JSONDecodeError:
            logger.exception("Failed to parse JSON from %s:\nraw_text=%r\nresponse=%r", _MODEL_ID, raw_text, response)
            raise

    async def compare(self, text_expected: str, text_got: str) -> SemanticMetricResult:
        """
        Extract and classify semantic facts from both texts in a single LLM call.

        SER = facts_missing / (facts_both + facts_missing) * 100
        GOT-only facts appear in the detail and HTML but don't affect SER.
        """
        parsed = await self._generate_json(_PROMPT_TEMPLATE.format(expected=text_expected, got=text_got))
        facts = tuple(
            SemanticFact(
                subject=item["subject"],
                predicate=item["predicate"],
                object=item["object"],
                verdict=Verdict(item["verdict"]),
            )
            for item in parsed.get("facts", [])
        )
        result = SemanticMetricResult(score=_semantic_error_rate(facts), detail=_grouped_fact_list(facts), facts=facts)
        logger.info(
            "LLM semantic: SER=%.1f%% (%d both, %d missing, %d extra)",
            result.score, result.facts_both, result.facts_missing, result.facts_extra,
        )
        return result
