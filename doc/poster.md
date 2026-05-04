# Realtime Speech-to-Text for Czech: Benchmark of Major Providers

Authors: Tomáš Kubeš, Iveta Zimmerová, Ondřej Ulrich — Chronic Anima:
www.chronicaanima.com, tomas.kubes@chronicaanima.com

## Abstract

We evaluate six commercial realtime STT providers on 91 recordings (~2 hours) of elderly Czech speakers from the Pamet Naroda oral history archive. Average Word Error Rate across providers is XX%, with the best provider achieving XX%. We propose Semantic Error Rate (SER), an LLM-based metric that measures information preservation rather than surface accuracy, and show that it better reflects transcript usability for morphologically rich languages. All code and data are released as an open-source toolkit.

## Introduction

Commercial STT providers report accuracy figures above 90% in their marketing materials. However, these benchmarks typically reflect offline processing of clean English speech by younger speakers.

Realtime STT operates under fundamentally harder constraints: the model must commit to output incrementally, without access to future context, and under strict latency budgets. For underrepresented languages like Czech, the gap between marketed and real-world performance widens further.

Czech presents specific challenges for STT systems: rich morphology (7 grammatical cases, complex verb conjugation), frequent dialectal variation, and limited representation in training data. Elderly speakers compound these difficulties through dialectal speech patterns, age-related voice changes, and informal conversational style.

We built a comprehensive benchmark to quantify this gap for our own product needs. Finding no comparable evaluation for Czech realtime STT, we release both the results and the evaluation toolkit.

## Methodology

### Dataset

91 audio recordings (~2 hours total) sourced from Pamet Naroda, a Czech oral history project. Recordings feature elderly speakers in home interview settings — natural conversational speech with background noise, and varied recording quality. Files were randomly selected from the archive; recordings in other languages or deemed unintelligible by human listeners were excluded.

Audio format: 16 kHz, mono, 16-bit PCM. Ground-truth transcripts verified by a human annotator (this resulted in significant alteration compared to published transcripts which are stylistically cleaned up).

### Evaluation Protocol

All providers receive identical input: audio streamed in 200 ms chunks at 1x realtime speed via WebSocket (or provider SDK where WebSocket is unavailable). A 2-second silence padding ensures final-utterance VAD commit. No provider-specific tuning beyond selecting the best available realtime model and Czech language setting.

8 recordings with low original volume were tested both in original form and after sound leveling, to assess provider robustness to quiet input. Both variants are included in the results and clearly marked in the dataset.

Providers and models tested:

| Provider | Model |
|----------|-------|
| Speechmatics | Enhanced (operating point) |
| Google Cloud STT | v1 API, default model |
| Cartesia | ink-whisper |
| ElevenLabs | scribe_v2_realtime |
| Deepgram | nova-3 |
| Gemini Flash Live | gemini-3.1-flash-live-preview |

All models represent the best available realtime offering from each provider as of May 2026, with one exception: Google Cloud STT uses the v1 streaming API with its default model. Google's newer Chirp 3 model (v2 API) was not tested due to an incompatible API migration; Google's results may therefore understate their current best capability.

### Text Normalization

Before comparison, both ground truth and STT output are normalized: whitespace unified, text lowercased, typographic variants (smart quotes, dashes, ellipses) mapped to ASCII, punctuation removed. This ensures metrics reflect transcription accuracy rather than formatting differences.

### Metrics

**Word Error Rate (WER):** Industry standard. Levenshtein distance at word level, divided by reference word count. Penalizes insertions, deletions, and substitutions equally.

**Character Error Rate (CER):** Levenshtein distance at character level. More granular for agglutinative/morphologically rich languages where a single inflectional error counts as a full word substitution in WER.

**Semantic Error Rate (SER):** Our proposed metric. Described below.

## Semantic Error Rate

WER treats all word errors equally. In Czech, a single wrong case suffix is a full word substitution, yet meaning is usually preserved. Conversely, a single misrecognized named entity may destroy the key information in a sentence. WER cannot distinguish these cases.

SER measures whether the *information content* of a transcript survived, regardless of surface form. We find this metric to be representative of actual real world performance in live conversation settings.

### Method

An LLM (Gemini) receives both the reference transcript and the STT output. It extracts atomic semantic facts (subject-predicate-object triples) from each text, then classifies each fact:

- **both** — fact present in both texts (information preserved)
- **expected** — fact only in reference (information lost by STT)
- **got** — fact only in STT output (possible hallucination)

### Formula

```
SER = facts_expected / (facts_both + facts_expected) x 100
```

Lower is better. A SER of 0% means all semantic information from the reference was preserved in the STT output.

### Extraction Prompt

The LLM is instructed (in Czech) to:
- Extract facts as subject + predicate + object triples
- Focus on named entities, events, quotes, numbers, dates, attributions
- Select only information essential for overall comprehension; ignore filler words, repetitions, trivialities
- Ignore punctuation, word order differences, spelling errors, and morphological variations (Czech declension/conjugation)
- Return structured JSON with verdict classification

[Full prompt published in repository]

### Why SER Matters

Our results show that while WER is XX% on average (nearly every second word wrong), SER is XX% — meaning roughly two-thirds of semantic information survives. For downstream tasks, especially our conversational agent, SER better predicts actual transcript utility.

## Results

| Provider       | CER (%) | WER (%) | SER (%) |
|----------------|---------|---------|---------|
| Speechmatics   | 19.2    | 27.7    | 20.5    |
| Google         | 30.3    | 41.5    | 36.1    |
| Cartesia       | 34.5    | 45.0    | 28.4    |
| ElevenLabs     | 36.0    | 47.8    | 28.2    |
| Deepgram       | 36.7    | 46.5    | 34.2    |
| Gemini Live    | 61.7    | 64.1    | 52.9    |
| **Average**    | **36.4**| **45.4**| **33.3**|

*Note: Results based on v1 benchmark run. Numbers are subject to marginal improvement in final version.*

### Key Findings

1. **Speechmatics is the clear leader**, outperforming all others on every metric by a wide margin.

2. **Average WER of XX%** means nearly every second word is incorrect — far from marketed accuracy claims.

3. **SER is consistently lower than WER** across all providers, confirming that semantic information is more robust than surface-level word accuracy suggests for Czech language.

4. **WER and SER rankings diverge.** Google ranks 2nd on WER (XX%) but 5th on SER (XX%), while ElevenLabs ranks 4th on WER (XX%) but 1st-equal on SER (XX%). This demonstrates that word accuracy and information preservation are distinct qualities — and that SER captures a dimension WER misses.

5. **Gemini Flash Live** performs worst across all metrics. As a multimodal model where STT is one capability among many, this is not unexpected, but it quantifies the gap versus dedicated STT systems.

### Qualitative Example

File: jandasek-miroslav-1923.wav (125 words, speaker born 1923). The recording discusses how Sokol gymnastics members self-funded their halls without state subsidies, and how 80% of the population participated, with children training twice a week.

**Ground truth (excerpt):**
> „...když jim řekli, že ne, nikdo, že tam chodí sami, ale že si ze svých prostředků postavili sokolovnu, jo, aniž by dostali korunu dotací od státu nebo města."

**Speechmatics** (WER 20.8%) — preserves meaning, minor morphological slips:
> „...když jim řekli že ne nikdo že tam chodí sami ale že si ze svých prostředků postavili sokolovnu jo aniž by dostali korunu dotací od státu města"

**Google** (WER 36.8%) — meaning partly preserved, some words garbled:
> „...když jí řekne vyženeме nikdo že tam chodí sami ale že si ze svých prostředků postavili sokolovnou jo aniž by dostali dotaci od státu města"

**ElevenLabs** (WER 81.6%) — heavy distortion, yet key facts (Sokol, self-funded, no state support) remain partially recognizable.

[NOTE: Exact transcripts to be replaced with v2 results]

## Discussion

This benchmark represents a snapshot of provider capabilities as of [DATE]. Provider models improve continuously; results may shift with future updates.

The authors have received no incentives or benefits from any of the tested providers. All accounts except ElevenLabs used standard free trial credits available at sign-up.

We release the complete evaluation toolkit as open source: audio streaming library, normalization pipeline, WER/CER/SER computation, and HTML diff report generator. Adding a new provider requires implementing a single async protocol class (~100 lines). We invite the community to:

- Test additional providers or models we did not cover
- Apply the toolkit to other languages and speaker demographics
- Validate and extend the SER methodology

[QR CODE / REPO LINK]