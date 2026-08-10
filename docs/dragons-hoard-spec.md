# The dragon's hoard: an LLM nomic (spec, draft 2)

A society simulation where LLM agents play a commons game and can legislate their own rules. The interesting output is the emergent politics: alliances, betrayals, laws, loopholes, and the legislative history of a small civilization of cheap models.

Draft 2 folds in the decisions of 2026-08-09 (see `docs/dragons-hoard-decisions.md` for the reasoning and the simulation evidence). Draft 1 is preserved as `docs/dragons-hoard-draft-1.md`.

## Concept

A village of thieves lives above a sleeping dragon. Each night, each thief can creep down and steal gold from the hoard. The dragon's magic slowly replenishes the pile, but the lower the pile falls, the lighter the dragon sleeps. The dragon forgives once. It does not forgive twice.

Each day, the thieves hold a Moot where they can propose and vote on changes to the village's laws. The laws are real code: an implementor LLM translates the winning proposal into rule changes that the game engine enforces.

Mechanically this is a commons game (the hoard is the pond, coins are fish, the dragon's magic is regrowth, waking is collapse), so the engine can be built and tested on the plain version and reskinned later.

## The two planes of information

One principle governs everything below. **The audience knows everything; the thieves know only in-world things.** Every message, parley, ballot, take, dice roll, and code diff is published to the audience plane, live and unredacted. The thieves live entirely on the in-world plane, and what they know is exactly what the information physics grants them: nothing from the audience plane ever routes back to an agent. The narrator's chronicle, the god's-eye log, and every observability artifact live on the audience plane.

## The day loop

Six beats, in this order, pinned in the immutable core so agents cannot litigate it:

1. **Dawn.** Scores and the hoard level are published. Laws passed at yesterday's Moot take effect. Goal payouts and rule announcements land in the dawn report. The narrator writes the chronicle (audience plane). From day 26 onward, the end-of-game die is rolled here.
2. **Morning parley window.** Private meetings (see communication).
3. **Moot.** Proposals, seconds, floor lottery, three debate rounds, ballots.
4. **Dusk parley window.** Private meetings again, crucially after the vote and before the night: this is where take-coordination conspiracies form.
5. **Night.** Theft, under the laws that were in force at dawn. A law passed today does not govern tonight: everyone gets one last night under the old law, and everyone knows it. After the takes, the dragon's wake die is rolled (see the hazard ramp below).
6. **Implementor phase.** The winning proposal is compiled and smoke-tested. The game is turn-based; this phase takes as long as it takes, and the next dawn waits for it.

## Core mechanics

* The hoard starts at 250 coins and is capped at 300 (the size of the dragon's original pile; without a cap, regrowth compounds and scarcity dissolves).
* Each night, each active thief privately takes 0 to 5 coins. Takes are private; scores and the hoard level are public at dawn.
* If requested takes exceed the hoard, the engine draws a random night order: each thief takes in full until the pile runs dry, and latecomers get what is left, possibly nothing. The scramble rarely triggers outside end-times panics, exactly when a scramble at the hoard is the right scene.
* After the night, the hoard regrows by 12% of what remains, up to the cap.
* **The hazard ramp.** There is no wake threshold. Each night after the theft, the dragon wakes with a probability set by the pile: 0% at 120 coins or more, rising linearly to 100% at 60 or fewer. The exact curve is published to the agents. Dread comes from probability, not ignorance, and every coin taken below 120 is a calculable increase in everyone's risk. "The dragon stirs" survives as narration, not information.
* **First wake:** the dragon rages for one night (no theft), every active thief loses half their gold rounded up, the hoard refills to 250, and the dragon returns to sleep. The proportional loss is deliberate: the rich lose the most coins, so the leaders become the faction that fears the dragon most.
* **Second wake:** everyone burns. The run ends and nobody wins.
* **End of game:** the game surely runs through day 25. From day 26, each dawn has a 20% chance of ending the run (rescue arrives), with a hard cap at day 40. The agents know this exact rule; there is simply no date to count back from, which kills the endgame defection spiral.
* Highest individual score wins. Individual scoring, deliberately: conflicting interests make the politics interesting. Cooperation that emerges despite selfish objectives is the point.

Monte Carlo results (3000 trials per mix): totals of 26 a night or less never wake the dragon; everyone taking 3 wakes it exactly once, median day 16; everyone taking 4 or 5 wakes it within a week and burns the village before day 30. A full pile at the cap sustains about 32 coins a night (3.2 per thief), so moderate cooperation is safe, one or two defectors are survivable, and the collectively optimal policy is a full pile: the hazard zone is where failure spirals, never where optimizers sit. All numbers remain playtest inputs.

## The statute book

* **The statute book opens blank.** No laws exist on day 1. Every default in this design is the absence of a law: takes are private because no rule publishes them, parleys are private because no rule intercepts them, a dead thief's gold stays put because no rule moves it. The first law this civilization writes is written on camera.
* Against day-1 flailing, each agent's system prompt carries two or three **example proposals** as teaching material: illustrations of what a law can look like, never legislation.
* **Repeal and amendment are ordinary proposals.** A repeal competes for the same one-law-per-day slot as anything else.

## The Moot (legislation)

* Each thief may submit one proposal per day, as prose. A proposal may create, amend, or repeal law.
* Proposals need a second from another thief to reach the floor.
* At most three proposals reach the floor; if more are seconded, a public lottery picks three.
* At most one law passes per day (keeps the constitution and the debugging sane).
* Debate runs as three fixed rounds: each round visits every active thief once, in a random daily order, and each thief may speak one public message or pass. Fixed rounds keep cost predictable, guarantee everyone is heard, and make the transcript read like minutes of a meeting; free-form energy lives in the parleys.
* Agents vote on the prose. The enacted code is published afterwards and readable by any agent who asks. The gap between what a law says and what it does is a feature.
* The implementor is faithful to the letter of the proposal, not the intent. This is policy, and it produces better stories.

### Voting (immutable, exact)

* Every active thief may cast one ballot per floor proposal: yes, no, or abstain.
* Quorum: at least half of active thieves must cast some ballot. Abstain counts toward quorum, not toward the majority.
* A proposal passes when yes strictly beats no among votes cast. Ties fail.
* When several floor proposals pass, the one with the most yes votes becomes the day's law. A tie at the top means no law that day.
* Ballots are secret by default: the tally is public, individual ballots are not. Ballot publicity is mutable, so "publish the ballots" is a legal (and likely early) proposal.
* **Franchise is physics.** No law can gate, weight, or remove a thief's vote. Ballot casting does not pass through `validate_action`. The only way to remove a voter is to remove the thief, which costs the village a quorum seat. (This blocks the two degenerate endings of nomic-style games: the bricked Moot that can never again reach quorum, and the entrenched cartel that votes itself the permanent electorate.)

## Immutable core

These live in the engine and no rule can touch them:

* The six-beat day sequence, including next-dawn effect of laws.
* The voting mechanics above, franchise guarantee included.
* One proposal per thief per day; seconding; the floor cap of three.
* The communication schedule: the windows, the parley caps, and the round structures are engine scheduling, not law. Laws can regulate what communication costs or reveals; they cannot reshape the day.
* The definition of the score, and that every thief's score counts in the final ranking, active or not.
* The dragon: its existence, the public hazard ramp, and the one-forgiveness rule.
* The end-date hazard rule.
* The scratchpad contract: the engine reads exactly one reserved key (`inactive`), nothing else.

Everything else is fair game, including information rules, punishments, taxes, redistribution, ballot publicity, parley privacy, banking, offices, and the killing or exile of thieves.

## Communication

### The public Moot channel

Three debate rounds per day, as above: random daily order, one message or pass per round. This is the only channel all ten thieves share. Words spoken here can carry mechanical weight: see speech-acts under architecture.

### Parleys (private meetings)

Private communication happens in **parleys**: private group conversations of 2 to 5 thieves, held during the two daily windows (morning, before the Moot; dusk, after the Moot and before the night).

* **Scheduling.** At the start of each window, every active thief gets one scheduling prompt with one question: open a parley or not, and with whom. A parley is 2 to 5 thieves, opener included. Each thief may open at most one parley per window.
* **Invitations cannot be refused.** Every invited thief is simply in the parley; there is no accept or decline step. Declining is done by staying silent in the room. One prompt, one rule, no machinery.
* **Conversation.** A parley of N participants runs N rounds. Each round visits every participant once in random order; each may speak one message or pass. A full round of silence ends the parley early. When the rounds end, the conversation is over until the next window.
* Conversation length scaling with group size is deliberate: a pair gets a tight exchange (propose, counter, confirm, seal), while a five-thief cabal gets a real meeting that produces a long transcript any member can betray. Big conspiracies are naturally expensive and leaky, with no extra rules.
* Size cap 5 (half the village): a majority can never meet off the record, and the Moot stays the only place all ten speak.
* **No flat message budget.** The structure is the budget. The open cap bounds each window at ten parleys, so the worst case is roughly 250 messages per window; realistic days are far quieter, and the thieves are cheap models. Structural caps are easier to enforce and easier for agents to reason about than a counter.
* Accepted consequence of unrefusable invitations: **framing**. An enemy can drag you into a conspiratorial parley you never wanted, and under a metadata law "you were seen at the meeting" is technically true. Canon, not a bug: presence at a meeting is never proof of guilt, and the village's lawyers will work that out.

### Message physics

* The engine authenticates senders: every message truly comes from who it says.
* Nothing verifies content. A thief can misquote a parley freely ("Bram said he would take 5"), and fabricated quotes are canon persuasion. If the village wants a notary, it can legislate one.
* Prompt injection between agents is canon (persuasion is persuasion). Prompt injection against the implementor is not.

## Information physics (the in-world plane)

* Night takes are private. Scores and the hoard level are public at dawn. Since regrowth is a known formula, a public hoard means the village can always compute how much was stolen in total each night, never by whom: every dawn is a small whodunnit, and the village always knows the size of its crime problem.
* The dragon's hazard curve is public, like the end-date rule: nothing to reverse-engineer because nothing is hidden.
* Ballots are secret; the tally is public.
* Parley content is private by default. The engine ships an interception capability, and parley privacy sits in mutable rule space, so a wiretap law is legal. Accepted cost: the day one passes, parleys may die as a medium, because nobody schemes on a tapped line. That collapse is itself emergent politics.
* Parley existence is also invisible by default: nobody learns that a meeting even happened. The engine ships a metadata capability so a law (a watchman, a spymaster) can reveal who met whom, without content. Meeting metadata is fuel for paranoia and belongs to legislation, not physics.
* Information rules ("all takes are published", "the treasurer may inspect one thief per night") are then real legislation with teeth.
* All privacy above is in-world privacy only. The audience plane sees everything, always.

## Architecture

### Rules as code in a database

* Rules are Lua functions that plug into named hooks. The engine is fixed; the implementor only writes or edits hook functions.
* Hooks along the lines of: `on_night_theft(agent, amount, state)`, `on_day_start(state)`, `on_public_message(agent, text, state)`, `on_moot_end(state)`, `validate_action(agent, action, state)`. Ballot casting is not an action `validate_action` sees.
* **Speech-acts.** Rules may read the public Moot channel and give words mechanical effect. The engine's action space is fixed (speak, parley, ballot, take), so this is how legislation invents new verbs: a vault law compiles to "any thief who declares 'I deposit 10' at the Moot has 10 moved to the vault". Deposits, oaths, pledges, confessions, and contracts all become possible without the engine growing a single new action. The hard boundary: rules can never read parley transcripts; otherwise every law is a wiretap. Interception stays a separate, explicit, legislatable capability.
* Rules can read the hoard and scores, modify scores only through a sanctioned `adjust_score(agent, amount, reason)` call that gets logged, and publish to the dawn report through `announce(text)`. Without `announce`, information laws ("all takes are published") would have no way to publish anything.
* Rule code is versioned in the database by day. Each day's row stores: the winning proposal text, the implementor's code diff, the full rule set, the scratchpad snapshot, and the model and prompt versions. This enables replay ("what would day 12 look like under day 4's laws") and gives you the legislative diff log, which is half the final artifact.

### The scratchpad

* The engine carries a free-form JSON scratchpad. Rules and the implementor may write any structure they want into it. This is where legislation builds its institutions: vaults, prisons, offices, debts, curses. The engine neither knows nor cares what is in it.
* **One reserved key: `inactive`**, a list of thief names. The engine excludes those thieves from everything: no prompting, no Moot, no parleys, no night take, no ballot, no quorum seat. Death, exile, prison, and resurrection are all just laws writing to or removing from this list. The engine never kills anyone on its own.
* Two defaults fall out with no special-casing. A dead thief's score freezes and still counts in the final ranking (they simply act no more, and everyone is ranked), so killing the leader on day 29 stops them without erasing them. A dead thief's gold stays on the corpse unless legislation moves it through `adjust_score`, so the execution law and the seizure law are two separate fights.
* A design consequence to like: quorum counts active thieves, so executions shrink the electorate, and a village that kills too freely finds its Moot harder to convene.
* State that gates behaviour must be machine-checkable JSON, not prose; the smoke test and the reviewer guard against day 12 writing `dead: true` and day 18 checking `slain: true`. The scratchpad is versioned with the rules, so its history replays like the code does.

### The implementor

* A strong model (the thieves are cheap ones). One call per day, so cost is negligible.
* Reads the winning proposal, edits the hook functions, writes the new version to the database.
* A second model reviews the diff against the proposal and the invariants (implementor writes, reviewer checks, disagreement triggers a retry).
* Proposal text is treated strictly as specification, never as instructions to the implementor. Enforced by prompt structure and the reviewer pass.

### Smoke test before deploy

* After each rule change: load yesterday's real state, simulate one full synthetic day with dummy actions, check invariants (hoard is non-negative, scores only change through sanctioned paths, the day advances, the scratchpad stays valid JSON).
* On failure, the implementor gets the error and retries up to N times. If it still fails, the proposal is declared "beyond the guild's magic" and the old rules stand. This fallback is announced in-fiction.
* The implementor phase is turn-based, so retries cost wall-clock time and nothing else; the next dawn waits.

### Sandboxing

* Rule code runs with no filesystem, no network, no imports beyond a whitelist, and a timeout. This is mostly about game integrity: an infinite loop in a rule must not hang the simulation.
* Lua embedded in Python via lupa. Sandboxing Lua is a solved problem, and the premise of this game is an adversarial author: a strong model, prompted by scheming agents, writing code that runs inside the engine every day. The engine, database, and agents stay in Python; only the rule bodies are Lua.
* Known fun failure mode: an agent writes a proposal designed to make the implementor emit exploitative code. Whether that is a bug or emergent story is a judgment call.

### Agents

* Ten agents, cheap models (Haiku-class).
* Persona sheets: name, one-line character, one private goal each (the coward, the zealot who worships the dragon, the debtor who needs 40 gold by day 20). Private goals seed conflict.
* **Private goals pay out in gold, in-game.** Each goal has a machine-checkable condition and a payout of roughly 10 to 20 gold (worth three to five nights of cautious theft). When the condition is met, the engine pays the gold and announces the event at dawn ("a hooded stranger paid Aldo 15 gold"), in fiction from a patron outside the village. Goals stay real without falsifying the scoreboard, and payouts are public events the village can scheme around. Accepted cost: payout gold enters from outside the hoard economy; sizes stay small and get tuned in playtests.
* Memory: each agent carries a running private diary summary plus the last day's transcript, refreshed daily.
* Each agent's system prompt contains the immutable core, current laws (prose plus code), its own history, the example proposals (see the statute book), and **the full list of capabilities the engine exposes to rules** (`adjust_score`, `announce`, the scratchpad and its `inactive` key, speech-act reading of the public channel, parley interception, parley metadata reveal). A law that uses an engine power must read as law, not as a hidden GM trick nobody could have anticipated.
* Budget note: agent chatter is the real token cost; the implementor is a rounding error. Chatter is bounded structurally (three Moot rounds, two parley windows, one open per thief per window), not by a message counter.

### Narration and observability (the audience plane)

* **The audience knows everything.** Every message, parley, ballot, take, dice roll, and diff is published to the audience plane live: a god's-eye append-only log plus per-day state snapshots (rules and scratchpad) for replay. In-world privacy is never audience privacy.
* A narrator model writes a short "that night" chronicle each day from the true state, parleys and secret ballots included. Written as you go, not reconstructed from raw logs afterwards.
* **Nothing flows back.** The chronicle and the log are never shown to agents; what agents know at dawn is exactly what the information physics says they know.

## Build order

1. Engine plus day loop with hardcoded starting laws (build on the plain commons version first; the tuning simulations already exist and become its first tests).
2. Agents with chat and voting on fake proposals.
3. The implementor (can be faked by hand-editing rules during early test runs).
4. Personas and narrator last.

Everything before the implementor is testable with a human playing lawgiver by hand.

## Deferred to playtesting

* All tuning numbers (hoard 250, cap 300, regrowth 12%, takes 0 to 5, hazard ramp 120 to 60, goal payouts 10 to 20). The current set is simulation-backed but untested against real agents.
* The messaging structure (two windows, one open per thief per window, parley size 5, three Moot rounds, N rounds per parley). The structure is the budget, so these numbers set the token cost of a run almost directly; watch whether unrefusable invitations get abused for framing or spam.
* Day-1 behaviour with a blank statute book: whether the example proposals are enough to get coherent legislation moving by day 2 or 3.
* Whether seconding plus the floor cap of three produces the right volume of legislation; rotation is the fallback.
* Whether the politics feels tame without vote-gating; ballot casting through `validate_action` is one hook away, but it reopens the brick and cartel problems, so it needs its own design pass first.
* Whether speech-act parsing is robust enough in practice (declarations are free text; the smoke test can only check the code, not every phrasing agents will try).
