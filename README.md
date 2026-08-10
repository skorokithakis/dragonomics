# Dragonomics

A society simulation where LLM agents play a commons game and can legislate
their own rules. Ten thieves live above a sleeping dragon: each night they
steal from its hoard, each day they hold a Moot where they propose and vote
on laws. The interesting output is the emergent politics — alliances,
betrayals, laws, loopholes, and the legislative history of a small
civilization of cheap models.

The full game design lives in [docs/dragons-hoard-spec.md](docs/dragons-hoard-spec.md).
The reasoning behind every design decision (with simulation evidence) is in
[docs/dragons-hoard-decisions.md](docs/dragons-hoard-decisions.md).

## Current state

- **Engine** (`main/engine.py`): pure game math — hoard regrowth, the hazard
  ramp, wake handling, night scrambles, Moot vote tallying.
- **Day loop** (`main/beats.py`): the six-beat day — dawn, morning parley,
  Moot, dusk parley, night, implementor.
- **Agents**: LLM thieves (DeepSeek via the OpenAI SDK) play parleys, the
  full Moot pipeline (proposals, seconds, floor lottery, debate, secret
  ballots), pick their night takes, and keep private diaries. Enacted laws
  are announced at dawn as prose in the law book.
- **Not yet built**: the implementor (laws are prose and enforce nothing),
  the narrator/audience frontend, deterministic replay.

Games also run in a no-LLM policy mode (each thief follows a fixed take
policy), which is what the test suite uses — tests never touch the network.

## Running it

The project uses [uv](https://docs.astral.sh/uv/). For agent games, export
`LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` (e.g. via an `.envrc`).

```bash
# Policy-mode game (no LLM needed)
uv run manage.py new_game
uv run manage.py advance        # runs the next beat; repeat 6x for a day

# Agent game (LLM thieves)
uv run manage.py new_game --agents
uv run manage.py advance
```

`advance` prints a terse per-beat summary; the full record (transcripts,
ballots, takes, every LLM call) is stored in the `Event` and `LlmCall`
tables.

```bash
uv run manage.py test main      # test suite, no network
```

## Repo map

- `main/engine.py` — pure mechanics, all game constants
- `main/beats.py` — the six-beat day loop, agent decision points
- `main/content.py` — the rules prose, example proposals, the ten personas
- `main/prompts.py` — prompt assembly and in-world information physics
- `main/llm.py` — LLM client and call log
- `docs/` — design spec, decision log, drafts, and the original simulation
  scripts (`sim-hazard.py`, `sim-threshold.py`)

## License

Copyright © Stavros Korokithakis. Licensed under the [MIT license](/LICENSE).
