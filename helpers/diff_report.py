from __future__ import annotations

from dataclasses import dataclass, field, fields
from html import escape
from pathlib import Path

from diff_match_patch import diff_match_patch


@dataclass(frozen=True)
class CustomMetricResult:
    """Return type for any custom metric function supplied to DiffReport.

    score:  error rate in percent, 0 to 100 (lower is better, like WER/CER)
    detail: human-readable explanation for text contexts (logging, TSV)

    Override to_html() in subclasses for a richer HTML representation.
    """
    score: float
    detail: str

    def to_html(self) -> str:
        """HTML fragment embedded in DiffReport. Override for custom rendering."""
        return (
            '<div class="panel">'
            '<h2>Custom Metric</h2>'
            '<div class="stats"><div class="stat">'
            f'<div class="stat-label">Score</div>'
            f'<div class="stat-value">{self.score:.1f}%</div>'
            '</div></div>'
            '<details style="margin-top:12px">'
            '<summary style="cursor:pointer;font-size:13px;color:#555">Detail</summary>'
            f'<pre style="margin-top:8px">{escape(self.detail)}</pre>'
            '</details>'
            '</div>'
        )


_PUNCTUATION_NORMALIZE = str.maketrans({
    # Curly/smart double quotes → straight
    '\u201c': '"',  # "
    '\u201d': '"',  # "
    '\u201e': '"',  # „
    '\u201f': '"',  # ‟
    # Curly/smart single quotes → straight
    '\u2018': "'",  # '
    '\u2019': "'",  # '
    '\u201a': "'",  # ‚
    '\u201b': "'",  # ‛
    # Guillemets → straight
    '\u00ab': '"',  # «
    '\u00bb': '"',  # »
    '\u2039': "'",  # ‹
    '\u203a': "'",  # ›
    # Dashes → hyphen
    '\u2013': '-',  # en-dash –
    '\u2014': '-',  # em-dash —
    '\u2010': '-',  # hyphen ‐
    '\u2011': '-',  # non-breaking hyphen ‑
    '\u2212': '-',  # minus sign −
    # Ellipsis → period
    '\u2026': '.',  # …
})

_PUNCTUATION_REMOVE = str.maketrans('', '', '.,!?;:"\'-')


def normalize_text_for_diff(text: str) -> str:
    """
    Normalize text for comparison:
    - unify whitespace (converts all whitespaces to single space),
    - convert to lowercase (as case is really hard for stt),
    - normalize punctuation variants (curly quotes, dashes, etc.) to ASCII equivalents,
    - remove common punctuation entirely.
    """
    text = text.translate(_PUNCTUATION_NORMALIZE).translate(_PUNCTUATION_REMOVE)
    return " ".join(text.split()).lower()


def _word_levenshtein(ref: list[str], hyp: list[str]) -> int:
    """Word-level Levenshtein distance (standard DP)."""
    n, m = len(ref), len(hyp)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, m + 1):
            prev, dp[j] = dp[j], min(
                dp[j] + 1,           # deletion
                dp[j - 1] + 1,       # insertion
                prev + (ref[i - 1] != hyp[j - 1]),  # substitution
            )
    return dp[m]


@dataclass(frozen=True)
class DiffReport:
    """Diff report comparing expected (ground truth) text against STT output.

    Only ``text_expected`` and ``text_got`` are provided at construction time.
    All metrics are derived automatically in ``__post_init__``.
    """
    text_expected: str
    text_got: str
    custom_metric: CustomMetricResult | None = field(default=None)

    # --- computed in __post_init__ (init=False) ---
    diffs: list[tuple[int, str]] = field(init=False, repr=False, compare=False)
    char_levenshtein: int = field(init=False, repr=False)
    chars_expected: int = field(init=False, repr=False)
    words_expected: int = field(init=False, repr=False)
    chars_got: int = field(init=False, repr=False)
    words_got: int = field(init=False, repr=False)
    chars_matched: int = field(init=False, repr=False)
    chars_inserted: int = field(init=False, repr=False)
    chars_deleted: int = field(init=False, repr=False)
    word_levenshtein: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        expected_norm = normalize_text_for_diff(self.text_expected)
        got_norm = normalize_text_for_diff(self.text_got)

        dmp = diff_match_patch()
        diffs = dmp.diff_main(expected_norm, got_norm)
        dmp.diff_cleanupSemantic(diffs)

        expected_words = expected_norm.split()
        got_words = got_norm.split()

        # object.__setattr__ is the standard pattern for frozen dataclass __post_init__
        _set = object.__setattr__
        _set(self, 'diffs', diffs)
        _set(self, 'char_levenshtein', dmp.diff_levenshtein(diffs))
        _set(self, 'chars_expected', len(expected_norm))
        _set(self, 'words_expected', len(expected_words))
        _set(self, 'chars_got', len(got_norm))
        _set(self, 'words_got', len(got_words))
        _set(self, 'chars_matched', sum(len(t) for op, t in diffs if op == 0))
        _set(self, 'chars_inserted', sum(len(t) for op, t in diffs if op == 1))
        _set(self, 'chars_deleted', sum(len(t) for op, t in diffs if op == -1))
        _set(self, 'word_levenshtein', _word_levenshtein(expected_words, got_words))

    @property
    def character_error_rate(self) -> float:
        """Character error rate in percent (based on char-level levenshtein distance)."""
        if self.chars_expected == 0:
            return 0.0
        return round(float(self.char_levenshtein) / self.chars_expected * 100, 1)

    @property
    def word_error_rate(self) -> float:
        """Word error rate in percent (based on word-level levenshtein distance)."""
        if self.words_expected == 0:
            return 0.0
        return round(float(self.word_levenshtein) / self.words_expected * 100, 1)

    @property
    def match_percentage(self) -> float:
        """Percentage of expected characters that matched."""
        if self.chars_expected == 0:
            return 100.0
        return round(float(self.chars_matched) / self.chars_expected * 100, 1)

    def to_metrics_dict(self) -> dict[str, str]:
        """Export all numeric fields and computed properties as an ordered dict of formatted strings.

        Skips str fields (raw texts). Includes @property computed metrics.
        Column order follows declaration order, so new fields and properties appear automatically.
        """
        d: dict[str, str] = {}
        for f in fields(self):
            val = getattr(self, f.name)
            if not isinstance(val, (int, float)):
                continue
            d[f.name] = str(val)
        for name, obj in type(self).__dict__.items():
            if isinstance(obj, property):
                val = getattr(self, name)
                d[name] = f"{val:.1f}" if isinstance(val, float) else str(val)
        if self.custom_metric is not None:
            d["custom_metric"] = f"{self.custom_metric.score:.1f}"
        return d

    def to_html(self, *, title: str, detail: str) -> str:
        """Render the diff report as a self-contained HTML document."""
        diff_html = diff_match_patch().diff_prettyHtml(self.diffs)

        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{escape(title)}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      margin: 2em;
    }}
    .hint {{
      padding: 10px 12px;
      border-left: 4px solid #888;
      background: #f6f6f6;
      margin: 12px 0 18px 0;
      white-space: pre-wrap;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      font-size: 12px;
    }}
    .panel {{
      margin: 16px 0;
    }}
    .panel h2 {{
      margin: 0 0 8px 0;
      font-size: 14px;
      color: #333;
    }}
    pre {{
      padding: 12px;
      background: #fafafa;
      border: 1px solid #e6e6e6;
      border-radius: 8px;
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      font-size: 12px;
      line-height: 1.4;
    }}
    .diff {{
      padding: 12px;
      border: 1px solid #e6e6e6;
      border-radius: 8px;
      background: #fff;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      font-size: 12px;
      line-height: 1.4;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin: 16px 0;
    }}
    .stat {{
      padding: 12px;
      background: #f8f9fa;
      border: 1px solid #e6e6e6;
      border-radius: 8px;
    }}
    .stat-label {{
      font-size: 11px;
      color: #666;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .stat-value {{
      font-size: 20px;
      font-weight: 600;
      color: #333;
      margin-top: 4px;
    }}
    .stat-detail {{
      font-size: 11px;
      color: #888;
      margin-top: 2px;
    }}
  </style>
</head>
<body>
  <h1>{escape(title)}: {self.word_error_rate}% WER / {self.character_error_rate}% CER</h1>

  <div class='hint'>{escape(detail)}</div>

  <div class="stats">
    <div class="stat">
      <div class="stat-label">Word Error Rate</div>
      <div class="stat-value">{self.word_error_rate:.1f}%</div>
      <div class="stat-detail">Word Levenshtein: {self.word_levenshtein}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Character Error Rate</div>
      <div class="stat-value">{self.character_error_rate:.1f}%</div>
      <div class="stat-detail">Char Levenshtein: {self.char_levenshtein}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Expected</div>
      <div class="stat-value">{self.chars_expected} chars</div>
      <div class="stat-detail">{self.words_expected} words</div>
    </div>
    <div class="stat">
      <div class="stat-label">Got</div>
      <div class="stat-value">{self.chars_got} chars</div>
      <div class="stat-detail">{self.words_got} words</div>
    </div>
    <div class="stat">
      <div class="stat-label">Matched</div>
      <div class="stat-value">{self.match_percentage:.1f}%</div>
      <div class="stat-detail">{self.chars_matched} chars</div>
    </div>
    <div class="stat">
      <div class="stat-label">Inserted</div>
      <div class="stat-value">{self.chars_inserted} chars</div>
      <div class="stat-detail">Extra in STT output</div>
    </div>
    <div class="stat">
      <div class="stat-label">Deleted</div>
      <div class="stat-value">{self.chars_deleted} chars</div>
      <div class="stat-detail">Missing from STT output</div>
    </div>
  </div>

  {self.custom_metric.to_html() if self.custom_metric is not None else ""}

  <div class="panel">
    <h2>Diff (regardless of punctuation, spaces and capitalization; red = deletions, green = insertions)</h2>
    <div class="diff">{diff_html}</div>
  </div>

  <div class="panel">
    <h2>Expected (Ground Truth)</h2>
    <pre>{escape(self.text_expected)}</pre>
  </div>

  <div class="panel">
    <h2>Got (Result of STT)</h2>
    <pre>{escape(self.text_got)}</pre>
  </div>


</body>
</html>
"""

    def write_html(self, out_path: Path, *, title: str, detail: str) -> Path:
        """Write the HTML diff report to a file. Returns the resolved path."""
        out_path = out_path.resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self.to_html(title=title, detail=detail), encoding="utf-8")
        return out_path