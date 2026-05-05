# Benchmark of Major Providers of Realtime Speech to Text for Czech: Every Third Word Is Wrong

## Authors
Tomáš Kubeš, Iveta Zimmerová, Ondřej Ulrich; Chronica Anima: www.chronicaanima.com, tomas.kubes@chronicaanima.com

## Abstract
We evaluate six commercial realtime STT providers on 91 voices (~2 hours) of elderly Czech speakers from an open dataset. Average Word Error Rate across providers is 38%, with the best provider achieving 17%. We propose Semantic Error Rate (SER), an LLM-based metric that measures information preservation rather than surface accuracy, and show that it better reflects transcript usability for morphologically rich languages. All code and benchmarking tools are released as open-source.

---

## Introduction

Commercial STT providers claim accuracy figures above 90% in their marketing materials. However, these typically reflect processing of clean English by younger speakers.

Realtime STT operates under fundamentally harder constraints: the model must commit to output incrementally, without access to future context, and under strict latency budgets.

Czech presents specific challenges for STT systems: rich morphology (7 grammatical cases, complex verb conjugation), frequent dialectal variation, and limited representation in training data. Elderly speakers compound these difficulties through dialectal speech patterns, age-related voice changes, and informal conversational style.

We built a comprehensive benchmark to quantify this gap. Finding no comparable evaluation for Czech realtime STT, we release both the results and the evaluation toolkit.

## Methodology

### Dataset

91 audio recordings (~2 hours total) were sourced from Pamet Naroda, a Czech oral history project. Recordings feature elderly speakers in home interview settings: natural conversational speech with background noise, and varied recording quality. Files were randomly selected from the archive; recordings in other languages or deemed unintelligible by human listeners were excluded. Full list of recordings used is available in the repo.

Audio format: all was converted to 16 kHz, mono, 16-bit PCM. Ground-truth transcripts were verified by a human annotator (this resulted in significant alteration compared to published transcripts which are stylistically cleaned up).

### Evaluation Protocol

All providers receive identical input: audio streamed in 200 ms chunks at 1x realtime speed via WebSocket (or provider SDK where WebSocket is unavailable). A 2-second silence padding ensures final-utterance VAD commit. No provider-specific tuning beyond selecting the best available realtime model and Czech language setting.

8 recordings with low original volume were tested both in original form and after sound leveling, to assess provider robustness to quiet input. Both variants are included in the results and clearly marked in the dataset.

### Providers and models tested

| Provider          |  Model                          |
|-------------------|---------------------------------|
| Cartesia          | 					ink-whisper                |
| Deepgram          | 				nova-3                      |
| ElevenLabs	       | 			scribe_v2_realtime           |
| Google Gemini	    | 		gemini-3.1-flash-live-preview |
| Google Cloud STT	 | 	v1 API, default model          |
| Speechmatics	     | 		Enhanced (operating point)    |

All models represent the best available realtime offering from each provider as of May 2026, with one exception: Google Cloud STT uses the v1 streaming API with its default model. Google's newer Chirp 3 model (v2 API) was not tested due to an incompatible API migration; Google's results may therefore understate their current best capability.

### Text Normalization

Before comparison, both ground truth and STT output are normalized: whitespace unified, text lowercased, typographic variants (smart quotes, dashes, ellipses) mapped to ASCII, punctuation removed. This ensures metrics reflect transcription accuracy rather than formatting differences.

## Qualitative Example
[ removed from github ]



## Metrics

**Word Error Rate (WER):** Industry standard. Levenshtein distance at word level, divided by reference word count. Penalizes insertions, deletions, and substitutions equally.

**Character Error Rate (CER):** Levenshtein distance at character level. More granular for agglutinative/morphologically rich languages where a single inflectional error counts as a full word substitution in WER.

**Semantic Error Rate (SER):** Our proposed novel metric. Described below.

In Czech, a single wrong case suffix is a full word substitution, yet meaning is usually preserved. Conversely, a single misrecognized named entity may destroy the key information in a sentence. WER cannot distinguish these cases.

SER measures whether the information content of a transcript survived, regardless of surface form. We find this metric to be representative of actual real world performance in live conversation settings.

An LLM (Gemini) receives both the reference transcript and the STT output. It extracts atomic semantic facts (subject-predicate-object triples) from each text, then classifies each fact as present in both, expected only (omission) or stt only (hallucination).

`SER = facts_expected / (facts_both + facts_expected) x 100`

Lower is better. A SER of 0% means all semantic information from the reference was preserved in the STT output.

### Extraction Prompt 
[Full prompt published in repository]

> - Extract facts as subject + predicate + object triples
> - Focus on named entities, events, quotes, numbers, dates, attributions
> - Select only information essential for overall comprehension;   ignore filler words, repetitions, trivialities.
> - Ignore punctuation, word order differences, spelling errors,   and morphological variations (Czech declension/conjugation).
> - Return structured JSON with verdict classification.


## Results

| Provider      | Model                          | WER | CER | SER |
|---------------|--------------------------------|-----|-----|-----|
| Cartesia	     | ink-whisper                    | 36% | 25% | 27% |
| Deepgram	     | nova-3                         | 41% | 30% | 34% |
| ElevenLabs    | 	scribe_v2_realtime            | 37% | 26% | 29% |
| Google Gemini | 	gemini-3.1-flash-live-preview | 61% | 58% | 56% |
| Google Cloud  | 	v1 API, default	              | 33% | 21% | 35% |
| Speechmatics  | 	Enhanced                      | 17% | 8%  | 16% |
| Average       |                                | 38% | 28% | 33% |

### Key Findings

- Speechmatics is the clear leader, outperforming all others on every metric.
- Average WER of 38% means more then every third word is incorrect on average, far from marketed accuracy claims.
- SER is mostly lower than WER across all providers, confirming that semantic information is more robust than surface-level word accuracy suggests for Czech.
- WER and SER rankings diverge slightly. This demonstrates that word accuracy and information preservation are distinct qualities, but that given simplicity in its computation, WER is a reasonable, albeit not perfect proxy even for Czech.


## Discussion + Disclaimer

This benchmark represents a snapshot of provider capabilities as of 2026-05-04. Provider models improve continuously; results may shift with future updates.

The authors have received no incentives or benefits from any of the tested providers. All accounts except ElevenLabs used standard free trial credits available at sign-up.

We release the complete evaluation toolkit as open source: audio streaming library, normalization pipeline, WER/CER/SER computation, and HTML diff report generator. Adding a new provider requires implementing a single async protocol class (~100 lines). We invite the community to:

- Test additional providers or models we did not cover
- Apply the toolkit to other languages and speaker demographics
- Validate and extend the SER methodology

Repo (tooling, file list): https://github.com/Chronica-Anima/universal-realtime-stt

---

**This work is a part of Chronica Anima project: Everyone deserves to be heard.Try our conversation at www.chronicaanima.com or at 296 21 21 01.**
