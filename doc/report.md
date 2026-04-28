# Realtime STT Provider Test Report

This reports describes the result of testing realtime STT on authentic elderly Czech voices (male/female).

It is from 2026-02-19, using the most recent versions of the STT models available at that time.

## Results

Average per provider results across 12 files listed in [Assets](#Assets). 

| Provider                        | WER | CER | SER |
|---------------------------------|-----|-----|-----|
| Cartesia (ink-whisper)          | 37% | 28% | 31% |
| Deepgram (nova-3)               | 38% | 27% | 40% |
| ElevenLabs (scribe_v2_realtime) | 26% | 17% | 29% |
| Google (GCP Speech-to-Text)     | 37% | 24% | 36% |
| Speechmatics (v2 api)           | 23% | 13% | 19% |

**SER:** Semantic error rate = LLM was used to extract facts from the 
text and compare these to mask potentially irrelevant misspells and 
evaluate how the STT result would be understood in a voice pipeline.

---

## Assets

**Note:** Files `witness_2134_8442-audio` and `witness_1719_14584-audio`
were excluded from the report processing due to their overall unintelligibility.

### Assets Origin

All sample sound assets used for quality report were retrieved from
["Paměť Národa"](https://www.pametnaroda.cz/cs/pribehy-20-stoleti) (Memory of Nations). 
Only assets belonging to: Příběhy 20. století (Post Bellum). Were selected.

### Asset List

**Quality Scale**

- Good: Voice is easily understandable in detail, all is clearly distinguishable.
- OK: Voice is easy to understand in general, but small details like word endings cannot be definitely discerned even by a human listener.
- Poor: Even human needs to exert great effort to figure out the general message, with many specific words being unintelligible.
- Unintelligible: Recording cannot be understood at all (excluded from test).

| Person                    | Event                                         | Quality | Link                                                                                                                                       |
|---------------------------|-----------------------------------------------|---------|--------------------------------------------------------------------------------------------------------------------------------------------|
| Zlata Bednářová           | Nesměli bychom se vzít                        | Poor    | [link](https://www.pametnaroda.cz/system/files/2022-10/Nesm%C4%9Bli%20bychom%20se%20vz%C3%ADt.mp3)                                         |
| RNDr., CSc. Adolf Absolon | Nový domov v pohraničí                        | Good    | [klip_0.mp3](https://www.pametnaroda.cz/system/files/2022-12/klip_0.mp3)                                                                   |
| Josef Adam                | Sovětští vojáci se ve stodole prali o chleba  | Good    | [Adam3.mp3](https://www.pametnaroda.cz/system/files/2023-02/Adam3.mp3)                                                                     |
| plukovník Josef Balejka   | Jak ho Poláci málem pověsili                  | OK      | [540-audio.mp3](https://www.pametnaroda.cz/system/files/witness/16/540-audio.mp3)                                                          |
| kapitán Adolf Vodička     | Motivace k odchodu                            | OK      | [87-audio.mp3](https://www.pametnaroda.cz/system/files/witness/44/87-audio.mp3)                                                            |
| Staša Fleischmannová      | Fučík v ateliéru                              | Poor    | [14584-audio.mp3](https://www.pametnaroda.cz/system/files/witness/1719/14584-audio.mp3)                                                    |
| dtto                      | dtto                                          | OK      | louder by 30dB                                                                                                                             |
| Benjamin Abeles           | U pozemního personálu v Royal Air Force       | Poor    | [12938-audio.mp3](https://www.pametnaroda.cz/system/files/witness/1994/12938-audio.mp3)                                                    |
| dtto                      | dtto                                          | OK      | louder by 22db                                                                                                                             |
| František Adamec          | Zastřelili faráře z Olomouce a tvrdili, že... | Unint.  | [witness_2134_8442-audio.mp3](https://www.pametnaroda.cz/system/files/witness/2134/8442-audio.mp3)                                         |
| Hilda Arnsteinová, ...    | Život v Terezíně                              | Poor    | [7922-audio.mp3](https://www.pametnaroda.cz/system/files/witness/2148/7922-audio.mp3)                                                      |
| Zdeněk Adamec             | Protest proti maďarským událostem v roce 1956 | OK      | [12898-video.mov](https://www.pametnaroda.cz/system/files/witness/4364/12898-video.mov)                                                    |
| Iva Bejčková              | Kapající voda na samotce                      | Good    | [link](https://www.pametnaroda.cz/system/files/witness/by-date/2020-08/nejhor%C5%A1%C3%AD%20byla%20kapaj%C3%ADc%C3%AD%20voda.mp3)          |
| Mgr. Květa Běhalová       | Výslech kvůli Černé knize vydané v roce 1968  | Good    | [link](https://www.pametnaroda.cz/system/files/witness/by-date/2020-11/Kv%C5%AFli%20%C4%8Cern%C3%A9%20knize%20vysl%C3%BDch%C3%A1ni%20.mp3) |
