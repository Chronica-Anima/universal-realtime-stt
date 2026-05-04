# Realtime Speech-to-Text for Czech: A Benchmark of Major Providers on Elderly Speech

## Abstract

We evaluate six commercial realtime STT providers on 91 recordings (~2 hours) of elderly Czech speakers from the Pamet Naroda oral history archive. Average Word Error Rate across providers is 45%, with the best provider achieving 28%. We propose Semantic Error Rate (SER), an LLM-based metric that measures information preservation rather than surface accuracy, and show that it better reflects transcript usability for morphologically rich languages. All code and data are released as an open-source toolkit.

## Introduction

Commercial STT providers report accuracy figures above 90% in their marketing materials. However, these benchmarks typically reflect offline processing of clean English speech by younger speakers.

Realtime STT operates under fundamentally harder constraints: the model must commit to output incrementally, without access to future context, and under strict latency budgets. For underrepresented languages like Czech, the gap between marketed and real-world performance widens further.

Czech presents specific challenges for STT systems: rich morphology (7 grammatical cases, complex verb conjugation), frequent dialectal variation, and limited representation in training data. Elderly speakers compound these difficulties through dialectal speech patterns, age-related voice changes, and informal conversational style.

We built a comprehensive benchmark to quantify this gap for our own product needs. Finding no comparable evaluation for Czech realtime STT, we release both the results and the evaluation toolkit.

## Methodology

### Dataset

91 audio recordings (~2 hours total) sourced from Pamet Naroda, a Czech oral history project. Recordings feature elderly speakers in home interview settings — natural conversational speech with background noise, cross-talk, and varied recording quality. Files were randomly selected from the archive; recordings in other languages or deemed unintelligible by human listeners were excluded.

Audio format: 16 kHz, mono, 16-bit PCM. Ground-truth transcripts verified by a human annotator.

### Evaluation Protocol

All providers receive identical input: audio streamed in 200 ms chunks at 1x realtime speed via WebSocket (or provider SDK where WebSocket is unavailable). A 2-second silence padding ensures final-utterance VAD commit. No provider-specific tuning beyond selecting the best available realtime model and Czech language setting.

Providers tested: Speechmatics, Google Cloud STT, Cartesia, ElevenLabs, Deepgram, Gemini Flash Live.

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

Our results show that while WER is 45% on average (nearly every second word wrong), SER is 33% — meaning roughly two-thirds of semantic information survives. For downstream tasks, especially our conversational agent, SER better predicts actual transcript utility.

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

2. **Average WER of 45%** means nearly every second word is incorrect — far from marketed accuracy claims.

3. **SER is consistently lower than WER** across all providers, confirming that semantic information is more robust than surface-level word accuracy suggests for Czech language.

4. **WER and SER rankings diverge.** Google ranks 2nd on WER (41.5%) but 5th on SER (36.1%), while ElevenLabs ranks 4th on WER (47.8%) but 1st-equal on SER (28.2%). This demonstrates that word accuracy and information preservation are distinct qualities — and that SER captures a dimension WER misses.

5. **Gemini Flash Live** performs worst across all metrics. As a multimodal model where STT is one capability among many, this is not unexpected, but it quantifies the gap versus dedicated STT systems.

## Discussion

This benchmark represents a snapshot of provider capabilities as of [DATE]. Provider models improve continuously; results may shift with future updates.

We release the complete evaluation toolkit as open source: audio streaming library, normalization pipeline, WER/CER/SER computation, and HTML diff reports. Adding a new provider requires implementing a single async protocol class (~100 lines). We invite the community to:

- Test additional providers or models we did not cover
- Apply the toolkit to other languages and speaker demographics
- Validate and extend the SER methodology

[QR CODE / REPO LINK]