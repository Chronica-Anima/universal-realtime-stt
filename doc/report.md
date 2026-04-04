# Realtime STT Provider Test Report

From 2026-02-19, using the most recent versions of the STT models available at that time.

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

**Note:** Files `witness_2134_8442-audio` and `witness_1719_14584-audio`
were excluded from the total due to their unintelligibility.

---

## Assets

### Assets Origin

All sample sound assets used for quality report were retrieved from
["Paměť Národa"](https://www.pametnaroda.cz/cs/pribehy-20-stoleti) (Memory of Nations). 
Only assets belonging to: Příběhy 20. století (Post Bellum). Were selected.

### Asset Details

**Quality**

- Good: Voice is easily understandable in detail, all is clearly distinguishable.
- OK: Voice is easy to understand in general, but small details like word endings cannot be definitely discerned even by a human listener.
- Poor: Even human needs to exert great effort to figure out the general message, with many specific words being unintelligible.

| File                               | Person                            | Event                                                                 | Quality        | Link                                                                                                                                       |
|------------------------------------|-----------------------------------|-----------------------------------------------------------------------|----------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| 2022-10_Nesm%C4%9Bli...            | Zlata Bednářová                   | Nesměli bychom se vzít                                                | Poor           | [link](https://www.pametnaroda.cz/system/files/2022-10/Nesm%C4%9Bli%20bychom%20se%20vz%C3%ADt.mp3)                                         |
| 2022-12_klip_0                     | RNDr., CSc. Adolf Absolon         | Nový domov v pohraničí                                                | Good           | [klip_0.mp3](https://www.pametnaroda.cz/system/files/2022-12/klip_0.mp3)                                                                   |
| 2023-02_Adam3                      | Josef Adam                        | Sovětští vojáci se ve stodole prali o chleba                          | Good           | [Adam3.mp3](https://www.pametnaroda.cz/system/files/2023-02/Adam3.mp3)                                                                     |
| witness_16_540-audio               | plukovník Josef Balejka           | Jak ho Poláci málem pověsili                                          | OK             | [540-audio.mp3](https://www.pametnaroda.cz/system/files/witness/16/540-audio.mp3)                                                          |
| witness_44_87-audio                | kapitán Adolf Vodička             | Motivace k odchodu                                                    | OK             | [87-audio.mp3](https://www.pametnaroda.cz/system/files/witness/44/87-audio.mp3)                                                            |
| witness_1719_14584-audio           | Staša Fleischmannová              | Fučík v ateliéru                                                      | Poor           | [14584-audio.mp3](https://www.pametnaroda.cz/system/files/witness/1719/14584-audio.mp3)                                                    |
| witness_1719_14584-audio-leveled   | dtto                              | dtto                                                                  | OK             | louder by 30dB                                                                                                                             |
| witness_1994_12938-audio           | Benjamin Abeles                   | U pozemního personálu v Royal Air Force                               | Poor           | [12938-audio.mp3](https://www.pametnaroda.cz/system/files/witness/1994/12938-audio.mp3)                                                    |
| witness_1994_12938-audio-leveled   | dtto                              | dtto                                                                  | OK             | louder by 22db                                                                                                                             |
| witness_2134_8442-audio.mp3        | František Adamec                  | Zastřelili faráře z Olomouce a tvrdili, že to bylo při útěku z vězení | Unintelligible | [witness_2134_8442-audio.mp3](https://www.pametnaroda.cz/system/files/witness/2134/8442-audio.mp3)                                         |
| witness_2148_7922-audio            | Hilda Arnsteinová, roz. Sommerová | Život v Terezíně                                                      | Poor           | [7922-audio.mp3](https://www.pametnaroda.cz/system/files/witness/2148/7922-audio.mp3)                                                      |
| witness_4364_12898-video           | Zdeněk Adamec                     | Protest proti maďarským událostem v roce 1956                         | OK             | [12898-video.mov](https://www.pametnaroda.cz/system/files/witness/4364/12898-video.mov)                                                    |
| witness_by-date_2020-08_nejhor_... | Iva Bejčková                      | Kapající voda na samotce                                              | Good           | [link](https://www.pametnaroda.cz/system/files/witness/by-date/2020-08/nejhor%C5%A1%C3%AD%20byla%20kapaj%C3%ADc%C3%AD%20voda.mp3)          |
| witness_by-date_2020-11_Kv_...     | Mgr. Květa Běhalová               | Výslech kvůli Černé knize vydané v roce 1968                          | Good           | [link](https://www.pametnaroda.cz/system/files/witness/by-date/2020-11/Kv%C5%AFli%20%C4%8Cern%C3%A9%20knize%20vysl%C3%BDch%C3%A1ni%20.mp3) |


## Asset Preprocessing

```
Last login: Thu Feb 19 09:57:30 on ttys003

# conversion to vaw
for f in *.mp3; do
  afconvert "$f" "${f%.mp3}.wav" -f WAVE -d LEI16@16000 -c 1 -v
done
...

# volume level
for f in *.wav; do
  echo "File: $f"
  ffmpeg -i "$f" -af volumedetect -f null - 2>&1 | egrep "mean_volume|max_volume"
  echo ""
done

File: 2022-10_Nesm%C4%9Bli%20bychom%20se%20vz%C3%ADt.wav
[Parsed_volumedetect_0 @ 0x7f97cb704e40] mean_volume: -27.3 dB
[Parsed_volumedetect_0 @ 0x7f97cb704e40] max_volume: -1.6 dB

File: 2022-12_klip_0.wav
[Parsed_volumedetect_0 @ 0x7f8064304840] mean_volume: -20.4 dB
[Parsed_volumedetect_0 @ 0x7f8064304840] max_volume: -1.1 dB

File: 2023-02_Adam3.wav
[Parsed_volumedetect_0 @ 0x7ff412704f40] mean_volume: -22.5 dB
[Parsed_volumedetect_0 @ 0x7ff412704f40] max_volume: -0.4 dB

File: test1.wav
[Parsed_volumedetect_0 @ 0x7fa78f704340] mean_volume: -26.6 dB
[Parsed_volumedetect_0 @ 0x7fa78f704340] max_volume: -2.5 dB

File: test2.wav
[Parsed_volumedetect_0 @ 0x7fddbbf05180] mean_volume: -43.1 dB
[Parsed_volumedetect_0 @ 0x7fddbbf05180] max_volume: -23.4 dB

File: test3.wav
[Parsed_volumedetect_0 @ 0x7faa8f604080] mean_volume: -13.8 dB
[Parsed_volumedetect_0 @ 0x7faa8f604080] max_volume: 0.0 dB

File: witness_16_540-audio.wav
[Parsed_volumedetect_0 @ 0x7fa12b406f80] mean_volume: -22.8 dB
[Parsed_volumedetect_0 @ 0x7fa12b406f80] max_volume: -0.1 dB

File: witness_1994_12938-audio.wav
[Parsed_volumedetect_0 @ 0x7fd14af08200] mean_volume: -43.4 dB
[Parsed_volumedetect_0 @ 0x7fd14af08200] max_volume: -21.4 dB

File: witness_2134_8442-audio.wav
[Parsed_volumedetect_0 @ 0x7f91d8404080] mean_volume: -35.4 dB
[Parsed_volumedetect_0 @ 0x7f91d8404080] max_volume: -15.8 dB

File: witness_2148_7922-audio.wav
[Parsed_volumedetect_0 @ 0x7f9c23007240] mean_volume: -24.8 dB
[Parsed_volumedetect_0 @ 0x7f9c23007240] max_volume: -0.7 dB

File: witness_by-date_2020-08_nejhor_C5_A1_C3_AD_20byla_20kapaj_C3_ADc_C3_AD_20voda.wav
[Parsed_volumedetect_0 @ 0x7ff50f705940] mean_volume: -28.5 dB
[Parsed_volumedetect_0 @ 0x7ff50f705940] max_volume: -8.9 dB

File: witness_by-date_2020-11_Kv_C5_AFli_20_C4_8Cern_C3_A9_20knize_20vysl_C3_BDch_C3_A1ni_20.wav
[Parsed_volumedetect_0 @ 0x7fb06a309d00] mean_volume: -28.2 dB
[Parsed_volumedetect_0 @ 0x7fb06a309d00] max_volume: -9.7 dB

# Volume adjust
ffmpeg -i witness_1994_12938-audio.wav -filter:a "volume=22dB,alimiter=limit=1.0" witness_1994_12938-audio-levelled.wav
```

---

## Technical

### Converting Test Assets to WAV (on Mac)

Most sources are in MP3 or other compressed format. Most STT expect PCM16000 mono.
Following commands can convert assets to acceptable format to run the test suite.

```
for f in *.mp3; do
  afconvert "$f" "${f%.mp3}.wav" -f WAVE -d LEI16@16000 -c 1 -v
done
```
or using ffmpeg (which should work on any system with ffmpeg installed)
```
ffmpeg -i input.mp3 -ac 1 -ar 16000 -c:a pcm_s16le output.wav
```

###  Manual Volume Leveling

Test the file loudness (mark the max):
```
for f in *.wav; do
  echo "File: $f"
  ffmpeg -i "$f" -af volumedetect -f null - 2>&1 | egrep "mean_volume|max_volume"
  echo ""
done
```

Increase the loudness but prevent oversaturation (select increase amount based on result above):
```
ffmpeg -i assets/test2.wav -filter:a "volume=30dB,alimiter=limit=1.0" assets/test3.wav
ffmpeg -i witness_1994_12938-audio.wav -filter:a "volume=22dB,alimiter=limit=1.0" witness_1994_12938-audio-leveled.wav
```
