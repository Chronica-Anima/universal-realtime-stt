# Contributing to universal-realtime-stt-tts

Bug reports, new providers and fixes are welcome. Open an issue first for larger changes.

## Before submitting

- Run the offline tests: `pytest tests/test_unit.py`. They need no API keys or assets. Never run bare `pytest`: it also runs the live provider tests, which need keys and are billed by each provider.
- Record user-visible changes under `[Unreleased]` in `CHANGELOG.md` ([Keep a Changelog](https://keepachangelog.com/)).
- Record architecture decisions as MADR records in `doc/adr/`.
- Write commit messages as Conventional Commits: `type: description`, lowercase.

## Adding a provider

See [Implementing a new provider](README.md#implementing-a-new-provider) in the README.

## License

Contributions are licensed under the MIT License.
