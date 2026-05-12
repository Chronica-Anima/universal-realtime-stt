# Analysis of Speechmatics Transcript Extraction

## Output Structure

Each AddTranscript message has two ways to get the text:
1. metadata.transcript - Pre-formatted transcript string (what you're using)
2. results[].alternatives[].content - Individual word/punctuation tokens

## Sample Output Patterns
```
┌───────┬──────────────────┬─────────────────────┬───────────────────────────────────────┐
│ Line  │     results      │ metadata.transcript │                 Notes                 │
├───────┼──────────────────┼─────────────────────┼───────────────────────────────────────┤
│ 1     │ []               │ ''                  │ Empty - silence/gap                   │
├───────┼──────────────────┼─────────────────────┼───────────────────────────────────────┤
│ 2     │ 2 words          │ 'Potom jsem '       │ Normal text with trailing space       │
├───────┼──────────────────┼─────────────────────┼───────────────────────────────────────┤
│ 6     │ word + punct     │ 'cenu. '            │ Word with end-of-sentence punctuation │
├───────┼──────────────────┼─────────────────────┼───────────────────────────────────────┤
│ 17    │ punct + word     │ '. Samozřejmě '     │ Punctuation from previous + new word  │
├───────┼──────────────────┼─────────────────────┼───────────────────────────────────────┤
│ 93-95 │ [] or punct only │ '' or '. '          │ Trailing empty/punct-only messages    │
└───────┴──────────────────┴─────────────────────┴───────────────────────────────────────┘
```

## Current Approach Assessment

The current code using metadata.transcript is actually the correct approach:

Why it's right:
- metadata.transcript is properly formatted by Speechmatics with correct spacing
- Punctuation attachment (attaches_to: 'previous') is already handled
- The if text: check filters empty strings

Why it might feel off:
1. Trailing whitespace (e.g., 'Potom jsem ') - but stt.py:48 already strips this
2. Occasional punctuation-only events (. ) get through

The alternative approach (building from results) would be more complex and unnecessary since Speechmatics already does the work of concatenating and spacing correctly.
