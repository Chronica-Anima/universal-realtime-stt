# Semantic Understanding Metric

CER and WER count every missing word ending, dropped conjunction and transposed letter. For Czech and other highly inflected languages this overstates how badly an STT output fails: a transcript can differ a lot at the character level and still carry the same facts.

The semantic understanding metric asks an LLM to extract facts from both the ground truth and the STT output, and computes a **Semantic Error Rate (SER)** from how much of the expected meaning the STT output lost.

## How it works

1. One LLM call receives the expected transcript and the STT output.
2. The LLM extracts facts as subject, predicate and object from both texts.
3. Each fact gets a verdict: `both` (preserved), `expected` (lost by the STT) or `got` (only in the STT output, a possible hallucination).
4. The result is a `SemanticMetricResult` with the SER and the fact list.

```
SER = facts_missing / (facts_both + facts_missing) * 100
```

Lower is better, as with WER and CER. `got`-only facts stay out of the denominator because they are extra content, not loss. Understanding is `100 - SER`.

## Setup

Set `GEMINI_API_KEY` in `.env` and install the `gemini` extra:

```bash
pip install -e ".[gemini]"
```

The model is `gemini-4-hydrogen`, set by `_MODEL_ID` in `helpers/semantic_understanding.py`. Without the key or the package, `benchmark.py` logs a warning and runs without the metric.

## Usage

`benchmark.py` enables the metric on its own when the key and package are present. To use it directly:

```python
from helpers.semantic_understanding import SemanticUnderstandingAnalyzer

analyzer = SemanticUnderstandingAnalyzer(api_key=os.getenv("GEMINI_API_KEY"))
report = await transcribe_and_diff(
    provider, wav_path, txt_path, out_path,
    custom_metric_fn=analyzer.compare,
)
print(f"SER: {report.custom_metric.score:.1f}%")
print(f"Understanding: {report.custom_metric.understanding:.1f}%")
```

The benchmark TSV gains a `custom_metric` column holding the SER, and each HTML diff gains a Semantic Understanding section with the SER, understanding, missing and extra percentages and the fact list grouped by verdict. Without `custom_metric_fn`, both are omitted.

## Writing your own custom metric

Any async callable with this signature works as `custom_metric_fn`:

```python
async def my_metric(text_expected: str, text_got: str) -> CustomMetricResult:
    ...
    return CustomMetricResult(score=score, detail=explanation)
```

`CustomMetricResult` (`helpers/diff_report.py`) is a frozen dataclass:

```python
@dataclass(frozen=True)
class CustomMetricResult:
    score: float  # error rate in percent, 0 to 100, lower is better
    detail: str   # plain-text summary for logging and the TSV

    def to_html(self) -> str: ...  # override for custom HTML rendering
```

For richer HTML, subclass it and override `to_html()`. The returned fragment is embedded in the diff report and can use the report's CSS classes (`.panel`, `.stats`, `.stat`, `.stat-label`, `.stat-value`, `.stat-detail`).

## Limitations

- **Cost:** one Gemini call per provider and audio file.
- **Non-determinism:** temperature is 0.0, which reduces but does not remove run-to-run variation in the extracted facts.
- **Language:** very colloquial or dialectal Czech can confuse fact extraction.
- **Short texts:** transcripts under about 20 words may yield too few facts for a meaningful score.
