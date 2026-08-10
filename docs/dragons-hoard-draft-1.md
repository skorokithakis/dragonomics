# The dragon's hoard: an LLM nomic (draft 1, 2026-08-09)

A society simulation where LLM agents play a commons game and can legislate their own rules. The interesting output is the emergent politics: alliances, betrayals, laws, loopholes, and the legislative history of a small civilization of cheap models.

## Concept

A village of thieves lives above a sleeping dragon. Each night, each thief can creep down and steal gold from the hoard. The dragon's magic slowly replenishes the pile, but if the hoard ever drops below a threshold, the dragon wakes and burns everything.

Each day, the thieves hold a Moot where they can propose and vote on changes to the village's laws. The laws are real code: an implementor LLM translates the winning proposal into rule changes that the game engine enforces.

Mechanically this is a commons game (the hoard is the pond, coins are fish, the dragon's magic is regrowth, waking is collapse), so the engine can be built and tested on the plain version and reskinned later.

## Core mechanics

* The hoard starts at 100 coins (tune this).
* Each night, each thief privately takes 0 to 5 coins. Takes are private; scores are public at dawn.
* After the night, the hoard regrows by 15% of what remains (tune this).
* If the hoard drops below the wake threshold, the dragon wakes: catastrophic loss for everyone (exact consequences TBD, see open questions).
* The game runs about 30 days. Highest individual score wins.
* Individual scoring, deliberately: conflicting interests make the politics interesting. Cooperation that emerges despite selfish objectives is the point.

## The Moot (legislation)

* Each thief may submit one proposal per day, as prose.
* Proposals need a second from another thief to reach the floor.
* At most one law passes per day (keeps the constitution and the debugging sane).
* Agents vote on the prose. The enacted code is published afterwards and readable by any agent who asks. The gap between what a law says and what it does is a feature.
* The implementor is faithful to the letter of the proposal, not the intent. This is policy, and it produces better stories.

## Immutable core

These live in the engine and no rule can touch them:

* How voting works (mechanics of proposing, seconding, majority)
* One proposal per thief per day
* The definition of the score
* The dragon's fundamental existence

Everything else is fair game, including information rules, punishments, taxes, redistribution, ballot secrecy, DM privacy, and possibly execution or exile of agents.

## Architecture

### Rules as code in a database

* Rules are Python or Lua functions that plug into named hooks. The engine is fixed; the implementor only writes or edits hook functions.
* Hooks along the lines of: `on_night_theft(agent, amount, state)`, `on_day_start(state)`, `on_moot_end(state)`, `validate_action(agent, action, state)`.
* Rules can read and write `state["extras"]` (their own keys), read the hoard and scores, and modify scores only through a sanctioned `adjust_score(agent, amount, reason)` call that gets logged.
* Rule code is versioned in the database by day. Each day's row stores: the winning proposal text, the implementor's code diff, the full rule set, and the model and prompt versions. This enables replay ("what would day 12 look like under day 4's laws") and gives you the legislative diff log, which is half the final artifact.

### The implementor

* A strong model (the thieves are cheap ones). One call per day, so cost is negligible.
* Reads the winning proposal, edits the hook functions, writes the new version to the database.
* A second model reviews the diff against the proposal and the invariants (TLH pattern: implementor writes, reviewer checks, disagreement triggers a retry).
* Proposal text is treated strictly as specification, never as instructions to the implementor. Enforced by prompt structure and the reviewer pass.

### Smoke test before deploy

* After each rule change: load yesterday's real state, simulate one full synthetic day with dummy actions, check invariants (hoard is non-negative, scores only change through sanctioned paths, the day advances).
* On failure, the implementor gets the error and retries up to N times. If it still fails, the proposal is declared "beyond the guild's magic" and the old rules stand. This fallback is announced in-fiction.

### Sandboxing

* Rule code runs with no filesystem, no network, no imports beyond a whitelist, and a timeout. This is mostly about game integrity: an infinite loop in a rule must not hang the simulation.
* Lua embedded in Python is the nicer fit (sandboxing Lua is a solved problem; restricting Python is famously leaky). If staying pure Python, use restricted exec with an AST check and accept some risk.
* Known fun failure mode: an agent writes a proposal designed to make the implementor emit exploitative code. Whether that is a bug or emergent story is a judgment call.

### Agents

* Ten agents, cheap models (Haiku-class).
* Persona sheets: name, one-line character, one private goal each (the coward, the zealot who worships the dragon, the debtor who needs 40 gold by day 20). Private goals seed conflict.
* Memory: each agent carries a running private diary summary plus the last day's transcript, refreshed daily.
* Each agent's system prompt contains the immutable core, current laws (prose plus code), and its own history.
* Budget note: agent chatter is the real token cost; the implementor is a rounding error.

### Communication

* One public Moot channel plus pairwise private DMs.
* Per-day message budget per agent (roughly 20) so chatter is a resource and the logs stay readable.
* Prompt injection between agents is canon (persuasion is persuasion). Prompt injection against the implementor is not.

### Information physics

* Night takes are private. Scores are public at dawn (everyone can see someone is getting rich but not how).
* The dragon's wake threshold is known only via warnings ("the dragon stirs" when within 20%). Creates dread without unfair blindness.
* Information rules ("all takes are published", "the treasurer may inspect one thief per night") are then real legislation with teeth.

### Narration and observability

* A god's-eye append-only log: every message, vote, take, diff, and dice roll, plus per-day state snapshots for replay.
* A narrator model writes a short "that night" chronicle each day from the true state. Written as you go, not reconstructed from raw logs afterwards.

## Build order

1. Engine plus day loop with hardcoded starting laws (build on the plain commons version first).
2. Agents with chat and voting on fake proposals.
3. The implementor (can be faked by hand-editing rules during early test runs).
4. Personas and narrator last.

Everything before the implementor is testable with a human playing lawgiver by hand.

## Open questions

**Day loop timing.** Do laws passed today apply to tonight's theft, or start tomorrow? Same-night makes votes urgent; next-day gives agents one last night to loot under the old law, which is dramatically excellent. Pick one and make it explicit, or the agents will litigate it.

**Voting details.** Majority of all agents or of votes cast? Are abstentions allowed? Public vote or secret ballot? Leaning: start public (accountability and revenge make better stories), and put ballot secrecy in mutable rule space so "make ballots secret" can itself be an early proposal.

**Death and exile.** Can a rule kill or exile an agent? Leaning yes (an `alive` flag the engine respects), because execution laws are peak nomic drama. But then define what happens to a dead agent's gold, because the first execution will be motivated by exactly that.

**Collapse consequences.** When the dragon wakes: does everyone die, or does everyone lose unbanked gold and the game continues? Leaning: continue to day 30 with survivors, because post-apocalypse Moots are content. Depends on whether banking exists as a mechanic or is left to legislation.

**Endgame defection.** On day 30 there is no reason not to take 5, and everyone will realise it around day 27. Either accept the defection spiral as realism, or hide the exact end date (rescue "sometime after day 25"), which kills the backward induction. Leaning: hidden end date.

**DM privacy.** Are DMs invisible to other agents forever, or can a rule legalise interception? If the latter, DM privacy belongs in mutable rule space.

**Tuning numbers.** Hoard size, take limit, regrowth rate, wake threshold, number of days, message budget. All need playtesting. The wake threshold could also be secret or noisy rather than warning-based.

**Proposal selection alternative.** Seconding is the current pick, but alternatives exist (random three to the floor, or one proposer per day by rotation) if seconding turns out to produce too much or too little legislation.

**Language choice.** Lua (easier sandbox) versus Python (easier implementor output, leakier sandbox).
