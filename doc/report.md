# Realtime STT Provider Test Report

Realtime speech-to-text on elderly Czech speakers (male/female), as of 2026-11-26.

## Results

Averages over 87 of the 91 recordings, ordered by WER. Lower is better.

| Provider     | Model                           | Run        | WER  | CER  | SER  |
| ------------ | ------------------------------- | ---------- | ---- | ---- | ---- |
| ElevenLabs   | `scribe_v3_live`                | 2026-11-26 | 17.8 | 9.5  | 15.7 |
| Speechmatics | `enhanced`                      | 2026-10-30 | 19.4 | 10.2 | 18.1 |
| Speechmatics | `standard`                      | 2026-08-20 | 21.5 | 11.9 | 21.5 |
| Google       | Cloud STT v1, default model     | 2026-05-04 | 36.4 | 25.1 | 39.0 |
| Cartesia     | `ink-whisper`                   | 2026-05-04 | 39.3 | 28.4 | 34.5 |
| ElevenLabs   | `scribe_v2_realtime`            | 2026-05-04 | 42.0 | 29.9 | 36.3 |
| Deepgram     | `nova-3`                        | 2026-05-04 | 42.5 | 32.6 | 41.1 |
| Google       | `gemini-3.1-flash-live-preview` | 2026-05-04 | 64.0 | 61.5 | 59.8 |

## Method

- **Audio:** 91 recordings from [Paměť národa][PN] (Memory of Nations), elderly speakers in home interviews. The list is in the private `assets` repository.
- **Streaming:** 16 kHz mono PCM in 200 ms chunks at real-time speed, language `cs`, default provider settings except the Speechmatics `enhanced` run (see Caveats).
- **Recordings counted:** those on which at least one provider reached 40% WER or better. The other 4 no provider transcribes usefully.
- **WER, CER:** word and character Levenshtein distance against a human-verified transcript, after lowercasing and removing punctuation.
- **SER:** share of facts in the reference transcript that are missing from the STT output, judged by `gemini-4-hydrogen`. See [the metric description](semantic_understanding_metric.md).

## Caveats

- Runs are from different dates. Providers tested in May may have improved since.
- The Speechmatics `enhanced` run set `input_volume_threshold` to 0.2, sent as Speechmatics `audio_filtering_config.volume_threshold` on its 0-100 scale. See [the threshold sweep](Speechmatics%20Echo%20Fix.md).

`python -m helpers.report_summary` reproduces the table when given the run TSVs. Benchmark output is not tracked in git, so the TSVs stay with whoever ran the benchmark.

[PN]: https://www.pametnaroda.cz/cs/pribehy-20-stoleti
