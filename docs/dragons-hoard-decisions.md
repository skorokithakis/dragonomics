# The dragon's hoard: decisions on the draft 1 open questions

Date: 2026-08-09. Resolves the nine open questions from draft 1, agreed in discussion, plus five decision sets that came out of that discussion: the state scratchpad (section 10), the franchise (section 11), messaging (section 12), the night and the public hoard (section 13), and the blank statute book and private goals (section 14). Each section gives the decision first, then the reasons. The numbers in sections 7 and 13 come from simulations of the commons loop.

## 1. Day loop timing

**Decision: laws take effect at the next dawn.** A law passed at today's Moot does not govern tonight's theft.

The pinned day sequence, which goes in the immutable core so agents cannot litigate it (parley windows added by decision 12):

1. **Dawn**: scores and the hoard level are published, new laws take effect, the narrator writes the chronicle.
2. **Morning parley window**: private meetings.
3. **Moot**: proposals, seconds, floor lottery, three debate rounds, ballots.
4. **Dusk parley window**: private meetings, after the vote and before the night.
5. **Night**: theft, under the laws that were in force at dawn.
6. **Implementor phase**: the winning proposal is compiled and smoke-tested. The game is turn-based, so this phase takes as long as it takes; the next dawn simply waits for it.

The reason is drama: everyone gets one last night to loot under the old law, and everyone knows it ("the tax passes tomorrow, so tonight we feast"). Note that timing is not an engineering constraint: the game is not real time, so the implementor never blocks anything regardless of when laws apply. Next-dawn stands on story value alone.

## 2. Voting details

**Decision: secret ballots by default, strict majority of votes cast, with a quorum.** Only the tally is published.

The full mechanics (immutable, so this must be exact):

* Every active thief may cast one ballot per floor proposal: yes, no, or abstain.
* Quorum: at least half of active thieves must cast some ballot (abstain counts toward quorum, not toward the majority).
* A proposal passes when yes strictly beats no among votes cast. Ties fail.
* When several floor proposals pass their votes, the one with the most yes votes becomes the day's law. A tie at the top means no law that day (scarcity of laws is fine).
* Ballots are secret by default: the tally is public, individual ballots are not.

Secret ballots generate intrigue: agents can promise one vote and cast another, and accusations of betrayal can never quite be proven. Ballot publicity stays in mutable rule space, so "publish the ballots" is itself a natural early proposal, and a transparency faction is a plausible storyline. Majority of votes cast, rather than of all agents, makes abstention a real political act and stops dead or silent agents from freezing legislation; the quorum stops a small faction passing laws while the rest are silent. "Active" means not on the scratchpad's `inactive` list (see section 10).

## 3. Death and exile

**Decision: yes, from run one, built in rule space on the scratchpad rather than as engine mechanics.** There is no engine-level `alive` flag. A law that kills or exiles a thief is compiled by the implementor into rules that write the victim onto the scratchpad's reserved `inactive` list; the engine reads that one key and skips inactive agents entirely (no prompting, no Moot, no parleys, no night take, no ballots, no quorum seat).

How death happens: the engine never kills anyone on its own, and neither does any built-in mechanic. A law like "any thief who takes more than 3 coins shall be put to death" compiles into a hook that appends the offender to `inactive`. Exile, prison, suspension, and resurrection are all just laws writing to or removing from the same list; none of them needs new engine work. Without the reserved key the implementor would have no sanctioned way to remove an agent, and such proposals would be declared beyond the guild's magic.

The old "engine defaults" now fall out naturally instead of being special-cased:

* **Death freezes the score, and the frozen score still counts in the final ranking**, simply because an inactive thief can take no actions and the final ranking counts every thief. Killing the leader on day 29 does not erase them from the podium; it only stops them accumulating, which blocks the degenerate "regicide wins" strategy.
* **A dead thief's gold stays on the corpse** simply because nothing moves it. Only legislation can move it, through `adjust_score`. So the execution law and the seizure law are two separate fights, which is exactly the drama draft 1 predicted.

One design consequence to like: because quorum counts active thieves, executions shrink the electorate, and a village that kills too freely finds its Moot harder to convene.

## 4. Collapse consequences

**Decision: the first wake is survivable; the second is fatal.** "The dragon does not forgive twice." (How a wake is triggered is the hazard ramp, section 13.)

On the first wake:

* The dragon rages for one night: no theft that night.
* Every living thief loses half their gold, rounded up.
* The hoard resets to full and the dragon goes back to sleep, once.

On a second wake, everyone burns and nobody wins.

Why survivable at all: post-collapse Moots among the singed are content, and one bad night should not end a 30-day run on day 4. Why not always survivable: a dragon that can be woken repeatedly becomes a manageable cost instead of a terror. One forgiveness gives a full post-collapse act while keeping a real doomsday on the table. The rage night, the refill, and the loss are the minimum mechanics that make "survivable" coherent: survivors must lose something or the hazard is toothless, and the hoard must refill or the dragon stays in the danger zone the next night.

The proportional loss is a deliberate choice: half your gold costs the richest thieves the most coins, so the rich become the most dragon-fearing faction and will pay for enforcement. Banking stays out of the engine; a vault law is natural legislation built from the scratchpad plus `adjust_score`.

## 5. Endgame defection

**Decision: hidden end date with a public hazard rule.** The game surely runs through day 25. From day 26 on, each dawn has a 20% chance of being the last (rescue arrives). Hard cap at day 40 to bound cost.

Tell the agents the exact rule. A fixed day 30 guarantees a defection spiral from about day 27, because everyone can count backwards. A fully secret date kills the induction but reads as GM fiat and gives agents nothing to reason about. The public hazard rule is the middle: no date to count back from, but nothing hidden or unfair. Expected end is near day 30, and only about 4% of runs reach the day-40 cap, so the cap barely reintroduces the induction. Late-game greed becomes a bet on the hazard instead of a certainty.

## 6. Parley privacy (formerly DM privacy)

**Decision: mutable rule space.** Parleys are private by default, not because a law says so but because no law intercepts them (see the blank statute book, section 14). The engine ships an interception capability that laws can switch on.

This buys surveillance politics, with one accepted cost: the day a wiretap law passes, parleys may die as a medium, because nobody schemes on a tapped line. That collapse is itself emergent politics.

One requirement this creates: the immutable core prompt must list every capability the engine exposes to rules (interception, metadata reveal, `adjust_score`, `announce`, the scratchpad and its reserved `inactive` key). A wiretap law then reads as law, not as a hidden GM power nobody could have anticipated.

## 7. Tuning numbers

**Decision: hoard 250, hard cap 300, regrowth 12% of the remainder, takes 0 to 5, ten thieves.** The wake mechanism is the hazard ramp of section 13, replacing the original fixed threshold and its warnings.

Draft 1's numbers (100 start, 15% regrowth) are too brittle for a 0-to-5 take range. At hoard 100 the sustainable total take is about 13 coins a night, or 1.3 per thief; in simulation, "everyone takes 2" wakes the dragon every six nights. The commons would burn constantly and the politics would never get room to develop.

The sweep also exposed a mechanic draft 1 does not have: **a cap on the hoard**. Without one, regrowth compounds without limit, scarcity vanishes by mid-game, and the commons problem dissolves. In fiction the cap is simply the size of the dragon's original pile.

Monte Carlo results at the chosen numbers (3000 trials per row; hazard ramp 0% at 120 to 100% at 60; first wake survivable, second fatal):

| Strategy mix (total take per night) | No wake | One wake | Fatal | Median first wake |
|---|---|---|---|---|
| All take 1 (10) | 100% | 0% | 0% | never |
| All take 2 (20) | 100% | 0% | 0% | never |
| Eight take 1, two take 5 (18) | 100% | 0% | 0% | never |
| Eight take 2, two take 5 (26) | 100% | 0% | 0% | never |
| All take 3 (30) | 0% | 100% | 0% | day 16 |
| All take 4 (40) | 0% | 0% | 100% | day 7 |
| All take 5 (50) | 0% | 0% | 100% | day 5 |

That is the intended shape. A full pile at the cap sustains about 32 coins a night (3.2 per thief), so moderate cooperation is safe and one or two defectors are survivable: a villain persists long enough to be legislated against instead of instantly torching the game. Collective greed at 3 is a slow doom that lands mid-game, right when the politics has matured. Greed at 4 or 5 burns the village: the first wake comes within a week, and continuing at that pace makes the second, fatal wake near-certain. All numbers remain starting points for playtests, not final answers.

## 8. Proposal selection

**Decision: keep seconding, and cap the floor at three proposals.** If more than three get seconds, a public lottery picks the three that reach the floor.

Seconding already filters solo cranks. The cap protects the vote from drowning: with ten agents, a ten-proposal ballot would eat the whole Moot. The lottery is fairer than first-come and needs no new mechanics. If seconding produces too little legislation in playtests, rotation is the fallback, but do not build it yet.

## 9. Language choice

**Decision: Lua, embedded in the Python engine via lupa.**

The sandbox argument settles it. This game's premise is an adversarial author: a strong model, prompted by scheming agents, writing code that runs inside the engine every day. Sandboxing Lua is a solved problem; restricted Python is a famous tarpit. The counterargument, that models write better Python, mattered more when the implementor was going to be a weak model; a strong implementor writes fine Lua, and the hook API is about four functions, so language ergonomics barely bite. The engine, database, and agents all stay in Python; only the rule bodies are Lua.

## 10. The scratchpad

**Decision: the engine carries a free-form JSON scratchpad, replacing draft 1's keyed `state["extras"]`. Rules and the implementor may write any structure they want into it. The engine reads exactly one reserved key: `inactive`, a list of thief names it excludes from all phases.**

This shrinks the engine and moves institutions into legislation, which is the nomic spirit: banking, prison, curses, offices, and death are all just laws that write state and rules that check it. The defaults of section 3 stop being special cases; they are simply what happens when no law says otherwise.

Why exactly one reserved key and not zero: the engine owns the loop, so only the engine can decide who gets prompted, who holds a quorum seat, and who costs tokens each day. With zero reserved keys, a scratchpad-dead thief would still wake, chat, and vote. With `inactive`, removal from the game is enforced in one place, and every flavour of removal is still legislation. Anything beyond that one key stays invisible to the engine.

Why JSON and not prose: state that gates behaviour must be machine-checkable, because tomorrow's law has to read yesterday's entry. The implementor is free to invent its own keys and shapes; the smoke test and the reviewer are the guard against day 12 writing `dead: true` and day 18 checking `slain: true`. The scratchpad is versioned with the rules in the daily database row, so its whole history replays like the code does.

## 11. The franchise

**Decision: franchise is physics. Ballot casting does not pass through `validate_action`, and no law can gate, weight, or remove a thief's vote.** The only way to remove a voter is to remove the thief (the `inactive` list), which costs the village a quorum seat.

The alternative (ballots through `validate_action`) would allow censure, prison, and vote-suspension laws, which is real politics. But it opens two failure modes, both born of an immutable counting rule sitting on top of a mutable electorate. The brick: ban six of ten from voting and quorum (half of active thieves) becomes unreachable, so no law, including the repeal, can ever pass again. The cartel: patch the brick by shrinking the quorum denominator, and a majority can pass "only we six may vote" and entrench itself for the rest of the run at zero cost, then tax the disenfranchised to nothing through `adjust_score`. Entrenchment is the classic degenerate ending of nomic-style games, and secret ballots make it easier to assemble unseen.

Franchise-as-physics keeps coalition instability alive, which is most of the game: a robbed minority still votes, so it can still bargain, defect, and flip coalitions. If run one feels tame, ballot gating is one hook away.

## 12. Messaging

**Decision: fixed debate rounds in the Moot, and private "parleys" in two daily windows. The structure is the budget; the flat 20-messages-a-day counter is dropped.**

The Moot: three debate rounds per day. Each round visits every active thief once, in a random daily order; each thief may speak one public message or pass. Predictable cost, everyone is heard, the transcript reads like minutes. Free-form energy lives in the parleys.

Parleys, the private channel (replacing draft 1's pairwise DMs):

* Two windows a day: morning (before the Moot) and dusk (after the Moot, before the night). Dusk matters most: take-coordination conspiracies ("everyone take 5 tonight") form there.
* At each window, each active thief gets one scheduling prompt with one question: open a parley or not, and with whom. A parley is 2 to 5 thieves, opener included.
* **Invitations cannot be refused: every invited thief is simply in the parley.** Declining is done by staying silent. This was chosen deliberately over an attend cap plus accept/decline machinery, for simplicity: one prompt, one rule.
* A parley of N participants runs N rounds; each round visits every participant once in random order; speak one message or pass. A round where at most one participant speaks ends the parley early. Conversation length scaling with group size is deliberate: a pair gets a tight exchange, a five-thief cabal gets a real meeting that produces a long transcript any member can betray. Big conspiracies are naturally expensive and leaky.
* Size cap 5 (half the village): a majority can never meet off the record, and the Moot stays the only place all ten speak.
* Cost bound comes from the open cap: at most ten parleys per window. Worst case is roughly 250 messages per window (versus about 100 with an attend cap); accepted for the simplicity, since the thieves are cheap models.
* Accepted consequence of unrefusable invitations: framing. An enemy can drag you into a conspiratorial parley you never wanted, and under a metadata law "you were seen at the meeting" is technically true. Canon, not a bug: presence at a meeting is never proof of guilt.

Message physics:

* The engine authenticates senders: every message truly comes from who it says.
* Nothing verifies content: fabricated quotes ("Bram said he would take 5") are canon persuasion. If the village wants a notary, it can legislate one.
* Parley content is private by default and interceptable by law (section 6). Parley existence is also invisible by default, with an engine metadata capability so a law (a watchman, a spymaster) can reveal who met whom without content.
* The narrator's chronicle is audience-only, never shown to agents; otherwise it leaks parleys and secret ballots the moment it narrates them.
* The communication schedule itself (windows, caps, round counts) is engine scheduling, not law: legislation can regulate what communication costs or reveals, but cannot reshape the day.

## 13. The night and the public hoard

Four decisions that define the night phase and the information economy around it.

**The hoard level is public at dawn.** With regrowth a known formula, a public hoard means everyone can compute exactly how much was stolen in total each night, never by whom. "Forty coins vanished and only ten of us live here" is the fuel of every dawn. The alternatives lose more than they add: a fuzzy report invites cheap models to hallucinate numbers, and a hidden hoard makes the commons ungovernable, since quota laws have nothing to bite on.

**The dragon wakes on a hazard ramp, not a threshold.** A public hoard would let the village reverse-engineer a fixed wake line after its first scare (the old warning fired at threshold-times-1.2, so one warning brackets the line), turning the dragon into a known floor and brinkmanship into engineering. So there is no line. Each night after the theft, the dragon wakes with a probability set by the pile: 0% at 120 coins or more, rising linearly to 100% at 60 or fewer. The exact curve is published to the agents, like the end-date rule: nothing to reverse-engineer because nothing is hidden, and the dread comes from probability rather than ignorance. Every coin taken below 120 is a calculable increase in everyone's risk, which gives quota laws a precise moral currency. "The dragon stirs" survives as narration, not information. A pleasing economic side effect: the collectively optimal policy is a full pile (the cap sustains about 32 coins a night, versus 13 near the danger zone), so the hazard zone is where failure spirals, never where optimizers sit. Monte Carlo results for the curve are in section 7.

**Overdraw resolves as a random scramble.** If requested takes exceed the hoard, the engine draws a random night order; each thief takes in full until the pile runs dry, and latecomers get what is left, possibly nothing. Pro-rata scaling would be fairer and duller, and it breaks whole-coin takes. The scramble almost never triggers outside end-times panics, exactly when a scramble at the hoard is the right scene, and the narrator gets it for free.

**Speech-acts: rules may read the public Moot channel and give words mechanical effect.** The engine's action space is fixed (speak, parley, ballot, take), so this is how legislation invents new verbs: a vault law compiles to "any thief who declares 'I deposit 10' at the Moot has 10 moved to the vault". Deposits, oaths, pledges, confessions, and contracts all become possible without the engine growing a single new action, and words at the Moot acquire mechanical teeth, which is deeply nomic. The hard boundary: rules can never read parley transcripts. Otherwise every law is a wiretap and parley privacy means nothing; interception stays a separate, explicit, legislatable capability (section 6).

## 14. The blank statute book and private goals

**The statute book opens blank.** Every "default" in this design is really the absence of a law: takes are private because no rule publishes them, parleys are private because no rule intercepts them, a dead thief's gold stays put because no rule moves it. So no starting laws are needed, and the constitutional history (half the final artifact) starts from a true blank page: the first law this civilization ever writes is written on camera, day 1. Against day-1 flailing by cheap models, the system prompt carries two or three example proposals as teaching material; examples are illustrations of what a law can look like, never legislation. Seeded starter laws were rejected because every law we author dilutes the artifact.

Two engine details this decision surfaced:

* **Repeal and amendment are ordinary proposals.** A repeal competes for the same one-law-per-day slot as anything else. No special mechanics.
* **Rules get an `announce(text)` capability** that posts to the public dawn report. Without it, information laws ("all takes are published") would have no way to publish anything. It joins the capability list in every agent's prompt.

**Private goals pay out in gold, in-game.** Each persona sheet carries one private goal with a machine-checkable condition and a payout (roughly 10 to 20 gold). When the condition is met, the engine pays the gold and announces the event at dawn ("a hooded stranger paid Aldo 15 gold"), in fiction from a patron outside the village. Why this over the alternatives: pure-roleplay goals get ignored by cheap models the moment gold is on the table, and a hidden end-of-game bonus makes the public scoreboard quietly false all game, which breaks both the audience's view and the agents' reasoning. Paid-in-gold keeps goals real and the scoreboard honest, and payouts become public events the village can see and scheme around ("why did a stranger pay Aldo?"). The accepted cost: payout gold is injected from outside the hoard economy, slightly opening the closed commons; sizes stay small (a payout is worth roughly three to five nights of cautious theft) and get tuned in playtests.

## Changes these decisions force back into the main design

* Add the hoard cap (300) to core mechanics.
* Pin the six-beat day sequence (dawn, morning window, Moot, dusk window, night, implementor) in the immutable core, with the implementor phase explicitly turn-based.
* Move the exact voting mechanics of section 2 into the immutable core text, with ballots secret by default and publicity in mutable rule space (replacing draft 1's lean toward public ballots).
* Add the franchise guarantee (section 11) to the immutable core.
* Replace `state["extras"]` with the free-form JSON scratchpad and its single reserved `inactive` key (section 10); drop the planned `alive` flag.
* Replace draft 1's pairwise DMs and 20-message budget with the parley system and structural caps (section 12).
* Publish the hoard level at dawn, and replace the fixed wake threshold and its warnings with the public hazard ramp (section 13).
* Add the random-scramble overdraw rule and the speech-act boundary (rules read the public channel only) to the engine spec (section 13).
* Start with a blank statute book and example proposals in the prompt; add repeal-as-ordinary-proposal and the `announce` capability (section 14).
* Give private goals machine-checkable conditions and in-game gold payouts announced at dawn (section 14).
* Add the engine capability list (interception, metadata reveal, `adjust_score`, `announce`, scratchpad, `inactive`) to every agent's immutable core prompt.
* Add the collapse rule (rage night, half gold, refill, second wake fatal) to core mechanics.
* Add the end-date hazard rule (20% per dawn from day 26, cap day 40) to core mechanics.
