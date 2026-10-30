# Speechmatics Echo Fix

Setting Speechmatics `audio_filtering_config.volume_threshold` to **0.2** (on a 0-100 scale) removes the agent's own TTS from user transcripts in both reported calls. On the benchmark it costs nothing measurable for normal-level speech. On the quiet phone line it changes about 6% of real user words, mostly fillers and clipped word onsets. It does not stop diarization from splitting one real user into several speakers, which is a separate issue.

## Cause

On affected setups the agent's TTS leaks into the user's mic far below speech level. Humans can't hear it, but Speechmatics transcribes it and diarization gives it its own speaker label. The gate works on the RMS level of each 10 ms chunk on a 0-100 scale and silences chunks below the threshold ([SM-AF]). The level is absolute, so the right value depends on the input's gain. Per-word `volume` measured on the user channel with filtering on and the threshold at 0:

| Call     | Echo median | Echo 90th per | Speech median | Speech 5th percentile | Speech minimum |
|----------|------------:|--------------:|--------------:|----------------------:|---------------:|
| EN phone |        0.13 |          0.24 |           5.2 |                  0.72 |           0.44 |
| CS web   |        0.01 |          0.02 |           9.3 |                   4.2 |           0.05 |

The medians are far apart, but a gate acts on the tails. On the phone call the margin between loud echo and quiet real speech is only about 3x.

## Threshold sweep

Both calls were streamed with the exact production config. An echo sentence is an utterance of 3 or more words, mostly overlapping agent speech, with median volume below 1. CS faint echo words are words overlapping agent speech with volume below 0.3. Real-speech change is word edits against the unfiltered run, counted only outside agent speech.

| Threshold | EN echo sentences | EN echo words | EN real words Δ (of 148) | CS echo sentences | CS echo words | CS real-speech Δ |
|----------:|------------------:|--------------:|-------------------------:|------------------:|--------------:|-----------------:|
|       off |                35 |           568 |                        0 |                 6 |            55 |    1.0% (2 runs) |
|       0.1 |                21 |           225 |                        8 |                 0 |             2 |             3.7% |
|      0.15 |                11 |            70 |                        9 |                 0 |             1 |             4.2% |
|   **0.2** |             **2** |        **19** |                    **9** |             **0** |         **0** |         **3.6%** |
|      0.25 |                 0 |             5 |                       11 |                 0 |             0 |             4.4% |
|       0.3 |                 0 |             4 |                        9 |                 0 |             1 |             4.0% |

- **Echo:** CS is clean from 0.1. EN needs 0.2: at 0.15 whole agent questions still come through, and at 0.2 two clause fragments remain.
- **Real speech:** the change is flat from 0.1 to 0.3, so enabling the gate costs a few words and raising it within this range adds almost nothing. On EN the edits are fillers, clipped onsets and one short question from a faint second person in the room (volume 0.58). On CS most are diacritic and number-format changes that keep the meaning.
- **Fitted on one call:** 0.2 is the lowest value that clears the EN call. A setup with louder echo will need more.

Speaker labels, threshold off vs 0.2:

| Call     | Off                                                   | 0.2                                        |
|----------|-------------------------------------------------------|--------------------------------------------|
| EN phone | S1 echo (564 words), S2 and S3 real, S4 echo (1 word) | S1 echo residue (19 words), S2 and S3 real |
| CS web   | S1 real, S2 echo (52 words), S3 real                  | S1, S2 and S3 all real speech              |

The EN call has two real people on the user end, which is why S2 and S3 stay. On CS the echo speaker disappears, but diarization still splits one user into three labels, mostly where the user talks over the agent. Diarization settings (`speaker_sensitivity`, `prefer_current_speaker`) might address that and were not tested.

## Benchmark

All 91 assets were run through Speechmatics with the repo default config, comparing each threshold with a threshold-off run from the same day. SER is the semantic error rate ([SER]). Two near-silent files are excluded here and covered below.

| Threshold | WER worse / better files | WER median Δ | SER worse / better files | SER median Δ |
|----------:|-------------------------:|-------------:|-------------------------:|-------------:|
|      0.15 |                  34 / 30 |          0.0 |                  48 / 24 |         +0.9 |
|   **0.2** |              **34 / 28** |      **0.0** |              **45 / 31** |     **+0.1** |
|       0.3 |                  45 / 24 |         +0.3 |                  43 / 32 |          0.0 |

- **Normal speech:** at 0.2 the median file is unchanged on WER and SER. There is no same-day repeat of the threshold-off run, so run-to-run noise is not quantified.
- **Near-silent input fails:** `vulehla-bohuslav-1927` (mean -59 dBFS) goes from WER 29.8 to 100 at every threshold. `yannakakis-ilios-1931` (mean -50 dBFS) goes from WER 40.7 to 62.6 at 0.15, 70.8 at 0.2 and 83.5 at 0.3. Their levelled copies and archive files at -34 to -40 dBFS are unaffected. Both are inaudible to a human without levelling, so their failure does not count against the threshold.
- **Production headroom:** the two problem calls average -34 dBFS (EN phone) and -27 dBFS (CS web) on the user channel, 16 to 32 dB above the files that fail. These are whole-file means, which don't show per-chunk onset clipping.

## Method

- **Audio:** production recordings are stereo 24 kHz, user on channel 0 and agent on channel 1. Only the user channel was streamed, at native 24 kHz, in real time.
- **Config:** production settings for both users: `enhanced` model, `max_delay` 0.8 s `flexible`, end-of-utterance trigger 0.6 s, speaker diarization.
- **Labelling:** words are flagged as echo by their overlap with agent activity on channel 1.

[SM-AF]: https://docs.speechmatics.com/features/audio-filtering
[SER]: semantic_understanding_metric.md
