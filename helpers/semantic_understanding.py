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
from html import escape as _esc
from logging import getLogger

from helpers.diff_report import CustomMetricResult
from helpers.llm_api import LLMBasicClient

logger = getLogger(__name__)

_MODEL_ID = "gemini-3.1-pro-preview"

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
    Lower is better — analogous to WER/CER.
    """
    facts: tuple[SemanticFact, ...]

    @property
    def facts_both(self) -> int:
        return sum(1 for f in self.facts if f.verdict is Verdict.BOTH)

    @property
    def facts_missing(self) -> int:
        return sum(1 for f in self.facts if f.verdict is Verdict.EXPECTED)

    @property
    def facts_extra(self) -> int:
        return sum(1 for f in self.facts if f.verdict is Verdict.GOT)

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
    def pct_missing(self) -> float:
        """% of expected facts absent from got (= SER)."""
        return self.score

    @property
    def pct_extra(self) -> float:
        """% of got facts not present in expected."""
        return round(self.facts_extra / self.total_got * 100, 1) if self.total_got > 0 else 0.0

    def to_html(self) -> str:
        """Rich HTML fragment: 4 stat cards + grouped fact list."""
        n_both, n_miss, n_extra = self.facts_both, self.facts_missing, self.facts_extra
        n_exp, n_got = self.total_expected, self.total_got

        cards = (
            _stat("Semantic Error Rate", f"{self.score:.1f}%",
                  f"{n_miss} missing of {n_exp} expected")
            + _stat("Understanding", f"{self.understanding:.1f}%",
                    f"{n_both} preserved of {n_exp} expected")
            + _stat("Missing", f"{self.pct_missing:.1f}%",
                    f"{n_miss} facts lost from expected")
            + _stat("Extra", f"{self.pct_extra:.1f}%",
                    f"{n_extra} facts added vs {n_got} in got")
        )

        groups = {
            Verdict.BOTH: ("Preserved (both)", n_both),
            Verdict.EXPECTED: ("Missing from STT", n_miss),
            Verdict.GOT: ("Extra in STT", n_extra),
        }
        fact_lines: list[str] = []
        for verdict, (label, _) in groups.items():
            group = [f for f in self.facts if f.verdict is verdict]
            if not group:
                continue
            fact_lines.append(f"{label}:")
            for f in group:
                fact_lines.append(f"  \u2022 {f.subject} {f.predicate} {f.object}")

        fact_text = _esc("\n".join(fact_lines)) if fact_lines else "No facts extracted."
        return (
            '<div class="panel">'
            '<h2>Semantic Understanding</h2>'
            f'<div class="stats">{cards}</div>'
            '<details style="margin-top:12px">'
            '<summary style="cursor:pointer;font-size:13px;color:#555">Fact list</summary>'
            f'<pre style="margin-top:8px">{fact_text}</pre>'
            '</details>'
            '</div>'
        )


def _stat(label: str, value: str, detail: str) -> str:
    return (
        '<div class="stat">'
        f'<div class="stat-label">{_esc(label)}</div>'
        f'<div class="stat-value">{_esc(value)}</div>'
        f'<div class="stat-detail">{_esc(detail)}</div>'
        '</div>'
    )


def _build_detail(facts: list[SemanticFact]) -> str:
    """Plain-text fact list for logging and TSV detail field."""
    groups = {
        Verdict.BOTH: "Preserved (both)",
        Verdict.EXPECTED: "Missing from STT (expected only)",
        Verdict.GOT: "Extra in STT (got only)",
    }
    lines: list[str] = []
    for verdict, label in groups.items():
        group = [f for f in facts if f.verdict is verdict]
        if not group:
            continue
        lines.append(f"{label}:")
        for f in group:
            lines.append(f"  \u2022 {f.subject} {f.predicate} {f.object}")
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
        self._llm = LLMBasicClient(api_key=api_key, model_id=_MODEL_ID, max_tokens=16000, temperature=0.0)

    async def compare(self, text_expected: str, text_got: str) -> SemanticMetricResult:
        """
        Extract and classify semantic facts from both texts in a single LLM call.

        SER = facts_missing / (facts_both + facts_missing) * 100
        GOT-only facts appear in the detail and HTML but don't affect SER.
        """
        prompt = _PROMPT_TEMPLATE.format(expected=text_expected, got=text_got)
        raw = await self._llm.call_llm(prompt, _SYSTEM_PROMPT, max_retries=1)

        facts = [
            SemanticFact(
                subject=item["subject"],
                predicate=item["predicate"],
                object=item["object"],
                verdict=Verdict(item["verdict"]),
            )
            for item in raw.get("facts", [])
        ]

        n_both = sum(1 for f in facts if f.verdict is Verdict.BOTH)
        n_miss = sum(1 for f in facts if f.verdict is Verdict.EXPECTED)
        n_extra = sum(1 for f in facts if f.verdict is Verdict.GOT)
        total_expected = n_both + n_miss
        ser = round(n_miss / total_expected * 100, 1) if total_expected > 0 else 0.0

        logger.info(
            "LLM semantic: SER=%.1f%% (%d both, %d missing, %d extra)",
            ser, n_both, n_miss, n_extra,
        )

        return SemanticMetricResult(
            score=ser,
            detail=_build_detail(facts),
            facts=tuple(facts),
        )
