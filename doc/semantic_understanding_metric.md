# Semantic Understanding Metric

## Overview

CER and WER measure surface-level accuracy — every missing word ending, dropped conjunction, or transposed letter counts against the score. For Czech (and other highly inflected languages), this overstates how badly the STT output fails in practice: a transcript can differ significantly at the character level while conveying the same facts.

The semantic understanding metric takes a different angle. It asks a reasoning LLM to extract *semantic facts* from both the ground-truth and the STT output, classify each fact by where it appears, and compute a **Semantic Error Rate (SER)** based on how much of the expected meaning is missing from the STT output.

---

## How It Works

1. **Single LLM call** with both the expected transcript and the STT output.
2. The LLM extracts facts in subject / predicate / object form from both texts simultaneously.
3. Each fact is classified with a `verdict`:
   - `both` — the fact appears in both texts (information preserved).
   - `expected` — the fact is only in the expected text (information lost).
   - `got` — the fact is only in the STT output (possible addition or hallucination).
4. SER and supporting percentages are computed and returned as a `SemanticMetricResult`.

### Score Formula — Semantic Error Rate (SER)

```
SER = facts_missing / (facts_both + facts_missing) * 100
```

- **Lower is better** — analogous to WER and CER (0% = all expected facts preserved).
- `got`-only facts do not affect the denominator: they are extra content, not errors relative to the ground truth.
- Understanding score (complement) = `100 - SER`.

---

## Data Model

### `CustomMetricResult` (`helpers/diff_report.py`)

The generic interface for any custom metric function:

```python
@dataclass(frozen=True)
class CustomMetricResult:
    score: float   # 0–100, lower = worse (like WER/CER)
    detail: str    # plain-text summary for logging and TSV

    def to_html(self) -> str: ...  # override for custom HTML rendering
```

### `SemanticMetricResult` (`helpers/semantic_understanding.py`)

Subclass returned by `SemanticUnderstandingAnalyzer`. Holds the full fact list and exposes computed percentages:

```python
@dataclass(frozen=True)
class SemanticMetricResult(CustomMetricResult):
    facts: tuple[SemanticFact, ...]

    # properties
    facts_both:     int    # facts present in both texts
    facts_missing:  int    # facts only in expected (lost by STT)
    facts_extra:    int    # facts only in got
    total_expected: int    # facts_both + facts_missing
    total_got:      int    # facts_both + facts_extra
    understanding:  float  # 100 - score (% preserved)
    pct_missing:    float  # = score (alias for clarity)
    pct_extra:      float  # facts_extra / total_got * 100
```

### `SemanticFact` and `Verdict` (`helpers/semantic_understanding.py`)

```python
class Verdict(str, Enum):
    BOTH     = "both"      # fact in both expected and got
    EXPECTED = "expected"  # fact only in expected (missing from STT)
    GOT      = "got"       # fact only in got (not in expected)

@dataclass(frozen=True)
class SemanticFact:
    subject:   str
    predicate: str
    object:    str
    verdict:   Verdict
```

### LLM JSON Schema

The LLM is instructed to return:

```json
{
  "facts": [
    {"subject": "...", "predicate": "...", "object": "...", "verdict": "both|expected|got"}
  ]
}
```

---

## Configuration

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Yes (for this metric) | Gemini API key. If absent, the metric is skipped with a warning. |

The model is hardcoded to `gemini-4-hydrogen` in `helpers/semantic_understanding.py`.

Add to your `.env`:
```
GEMINI_API_KEY=your_key_here
```

### Optional dependency

`google-genai` is listed in `requirements.txt` but commented out by default (it is only needed for this metric). To enable:

```bash
# Option A — install directly
pip install google-genai

# Option B — uncomment in requirements.txt, then reinstall
pip install -r requirements.txt
```

If `GEMINI_API_KEY` is set but `google-genai` is not installed, `benchmark.py` logs a clear warning and continues without the metric.

---

## Integration

### In `benchmark.py` (automatic)

If `GEMINI_API_KEY` is set and `google-genai` is installed, `benchmark.py` builds a `SemanticUnderstandingAnalyzer` and passes its `compare` method to `transcribe_and_diff`. The TSV report gains a `custom_metric` column containing the SER value.

### In `transcribe_and_diff()` (manual / test)

```python
from helpers.semantic_understanding import SemanticUnderstandingAnalyzer

analyzer = SemanticUnderstandingAnalyzer(api_key=os.getenv("GEMINI_API_KEY"))
report = await transcribe_and_diff(
    provider, wav_path, txt_path, out_path,
    custom_metric_fn=analyzer.compare,
)
print(f"SER: {report.custom_metric.score:.1f}%")
print(f"Understanding: {report.custom_metric.understanding:.1f}%")
print(report.custom_metric.detail)
```

If `custom_metric_fn` is `None` (the default), `DiffReport.custom_metric` is `None` and the TSV column and HTML section are omitted.

---

## Output

### TSV Report

A `custom_metric` column is appended when the metric is active. The value is the **SER** (lower = better):

```
provider    file     ...  custom_metric
Deepgram    audio1   ...  12.5
ElevenLabs  audio1   ...  0.0
```

### HTML Diff Report

When `custom_metric` is present, the HTML report gains a **Semantic Understanding** section with four stat cards and a collapsible fact list:

| Card | Value | Meaning |
|---|---|---|
| Semantic Error Rate | SER% | Missing facts as % of expected (lower = better) |
| Understanding | (100−SER)% | Facts preserved from expected |
| Missing | pct_missing% | Expected facts absent from STT output |
| Extra | pct_extra% | Got facts not present in expected |

Below the cards, a `Fact list` toggle shows every fact grouped as *Preserved*, *Missing from STT*, or *Extra in STT*.

---

## Writing Your Own Custom Metric

Any async callable with the signature below can be used as `custom_metric_fn`:

```python
async def my_metric(text_expected: str, text_got: str) -> CustomMetricResult:
    ...
    return CustomMetricResult(score=score, detail=explanation)
```

For a richer HTML representation, subclass `CustomMetricResult` and override `to_html() -> str`. The returned string is an HTML fragment embedded in the diff report body — it can use the existing CSS classes (`.panel`, `.stats`, `.stat`, `.stat-label`, `.stat-value`, `.stat-detail`).

Pass your function to `transcribe_and_diff(custom_metric_fn=my_metric)`.

---

## Limitations

- **LLM cost**: one Gemini API call per (provider × audio file) pair.
- **Non-determinism**: LLMs may extract slightly different facts across runs. Temperature is set to 0.2 to reduce variance.
- **Language**: Gemini handles Czech well, but very colloquial or dialectal speech may confuse fact extraction.
- **Short texts**: transcripts shorter than ~20 words may yield too few facts for a meaningful score.
