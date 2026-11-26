"""
Average benchmark TSVs into the per-provider table published in doc/report.md.

Only the assets listed in the report file set count. The set holds every asset
on which at least one provider reached a word error rate at or below a cutoff:
a recording nobody transcribes reasonably says nothing about the providers.

Each run is given as LABEL=PATH[:PROVIDER], where PROVIDER is the provider column
in the TSV and defaults to LABEL. Run from the repository root:

    python -m helpers.report_summary --derive 50 "Cartesia=out/v2/20260504_094311_benchmark.tsv" ...
    python -m helpers.report_summary "Cartesia=out/v2/20260504_094311_benchmark.tsv" ...
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_FILE_SET_PATH = Path("doc/report-files.txt")


@dataclass(frozen=True)
class RunSpec:
    label: str
    path: Path
    provider: str


@dataclass(frozen=True)
class FileScores:
    word_error_rate: float
    character_error_rate: float
    semantic_error_rate: float | None


@dataclass(frozen=True)
class ProviderSummary:
    label: str
    files_scored: int
    missing_files: list[str]
    word_error_rate: float
    character_error_rate: float
    semantic_error_rate: float | None
    semantic_files_scored: int


def parse_run_spec(text: str) -> RunSpec:
    label, separator, location = text.partition("=")
    if not separator or not label or not location:
        raise ValueError(f"Run must look like LABEL=PATH[:PROVIDER], got {text!r}")
    path_text, provider_separator, provider = location.rpartition(":")
    if not provider_separator:
        path_text, provider = location, label
    return RunSpec(label=label, path=Path(path_text), provider=provider or label)


def load_scores(run: RunSpec) -> dict[str, FileScores]:
    """Scored rows of one provider in one benchmark TSV; failed rows carry no metrics and are left out."""
    scores: dict[str, FileScores] = {}
    with run.path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if row["provider"] != run.provider or not row["word_error_rate"]:
                continue
            scores[row["file"]] = FileScores(
                word_error_rate=float(row["word_error_rate"]),
                character_error_rate=float(row["character_error_rate"]),
                semantic_error_rate=float(row["custom_metric"]) if row.get("custom_metric") else None,
            )
    if not scores:
        raise ValueError(f"No scored rows for provider {run.provider!r} in {run.path}")
    return scores


def derive_file_set(score_tables: list[dict[str, FileScores]], word_error_rate_cutoff: float) -> list[str]:
    """Files on which the best provider reached a word error rate at or below the cutoff."""
    best_word_error_rate: dict[str, float] = {}
    for scores in score_tables:
        for file_name, file_scores in scores.items():
            previous_best = best_word_error_rate.get(file_name, float("inf"))
            best_word_error_rate[file_name] = min(previous_best, file_scores.word_error_rate)
    return sorted(name for name, rate in best_word_error_rate.items() if rate <= word_error_rate_cutoff)


def write_file_set(path: Path, file_names: list[str], word_error_rate_cutoff: float, runs: list[RunSpec]) -> None:
    header = [
        "# Assets counted in the published report averages.",
        f"# Listed when at least one provider reached a word error rate of {word_error_rate_cutoff:g}% or lower.",
        "# Written by helpers/report_summary.py --derive from these runs:",
        *(f"#   {run.label} = {run.path.as_posix()} ({run.provider})" for run in runs),
    ]
    path.write_text("\n".join(header + file_names) + "\n", encoding="utf-8")


def read_file_set(path: Path) -> list[str]:
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize(label: str, scores: dict[str, FileScores], file_set: list[str]) -> ProviderSummary:
    counted = [scores[name] for name in file_set if name in scores]
    if not counted:
        raise ValueError(f"{label} has no scores for any file in the file set")
    semantic_rates = [entry.semantic_error_rate for entry in counted if entry.semantic_error_rate is not None]
    return ProviderSummary(
        label=label,
        files_scored=len(counted),
        missing_files=[name for name in file_set if name not in scores],
        word_error_rate=_mean([entry.word_error_rate for entry in counted]),
        character_error_rate=_mean([entry.character_error_rate for entry in counted]),
        semantic_error_rate=_mean(semantic_rates),
        semantic_files_scored=len(semantic_rates),
    )


def format_table(summaries: list[ProviderSummary]) -> str:
    lines = ["| Provider | WER | CER | SER | Files | Files with SER |", "|---|---|---|---|---|---|"]
    for summary in summaries:
        semantic = "" if summary.semantic_error_rate is None else f"{summary.semantic_error_rate:.1f}"
        lines.append(
            f"| {summary.label} | {summary.word_error_rate:.1f} | {summary.character_error_rate:.1f} | {semantic} "
            f"| {summary.files_scored} | {summary.semantic_files_scored} |"
        )
    return "\n".join(lines)


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", help="LABEL=PATH[:PROVIDER]")
    parser.add_argument("--file-set", type=Path, default=DEFAULT_FILE_SET_PATH)
    parser.add_argument("--derive", type=float, metavar="WER_CUTOFF",
                        help="Rewrite the file set from these runs before summarizing.")
    options = parser.parse_args(arguments)

    runs = [parse_run_spec(text) for text in options.runs]
    score_tables = [load_scores(run) for run in runs]

    if options.derive is not None:
        write_file_set(options.file_set, derive_file_set(score_tables, options.derive), options.derive, runs)

    file_set = read_file_set(options.file_set)
    summaries = [summarize(run.label, scores, file_set) for run, scores in zip(runs, score_tables)]
    for summary in summaries:
        if summary.missing_files:
            print(f"WARNING: {summary.label} has no score for {', '.join(summary.missing_files)}", file=sys.stderr)

    print(f"{len(file_set)} files from {options.file_set}\n")
    print(format_table(summaries))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
