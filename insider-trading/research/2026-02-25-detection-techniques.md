# Detection Techniques

## Core Insight

Polymarket has no enforcement mechanism. No SEC, no subpoenas. Insiders have no reason to hide — they can use their main wallet, keep their username, go all-in without creating fresh wallets or splitting across accounts. Detection that relies on evasion behavior (new wallets, funding clusters, sybil patterns) will miss the most blatant cases.

Detection should focus on **whose results are statistically implausible**, not who is trying to hide.

## Two Detection Signals

### Signal 1: Statistical Implausibility

A suite of per-wallet metrics that each measure a different dimension of "too good to be true." Each metric stands alone as a flag. Optionally, combine them into a weighted aggregate score later.

**Individual metrics:**

1. **Contrarian win rate** — Fraction of bets placed against consensus (<20% or >80% wrong side) that resolved correctly. Even great forecasters hit ~30-40% on low-probability bets. Sustained 80%+ across 10+ bets is astronomically unlikely.

2. **Niche market accuracy** — Win rate specifically on low-volume/obscure markets where no one should have high confidence. Separates genuine skill (which shows on popular, well-researched markets) from insider knowledge (which shows on niche markets no one is paying attention to).

3. **Profit factor** — Total winnings / total losses. Skilled traders might hit 2-3x. Insiders in known cases were 10x+.

4. **Brier score vs market consensus** — How much a wallet's positions outperform the market-implied probabilities. Measures edge over the crowd. Compute p-value against a null model of random trading at market odds.

5. **Position concentration** — Fraction of total capital deployed into single bets. Skilled traders diversify. Insiders put 50%+ into one niche bet because they know the outcome. Kelly criterion violation.

6. **Win streak length** — Longest consecutive correct bet streak, especially on resolved markets. Useful as a simple filter.

7. **ROI on resolved markets** — Net profit / total capital deployed. Context-dependent (a 5x return on 3 bets is more suspicious than 5x on 500 bets).

8. **Bet size vs odds asymmetry** — Placing large bets at extreme odds (e.g., $50K at 5% implied probability). Normal bettors scale down at extreme odds. Insiders scale up because they know.

Each metric produces a per-wallet score. No weighting or combination required upfront — analyze each independently, then optionally create an aggregate later.

### Signal 2: Timing Relative to Information Release

Consistently entering positions shortly before news breaks or price moves. The window in known cases is typically 30 minutes to 4 hours before announcements. This signal is independent of accuracy — it measures whether a wallet repeatedly shows up right before the market moves, regardless of their overall win rate.

**Approach A (self-contained):** Use price spikes as a proxy for information release. A jump from 20% to 80% in 30 minutes = information entered the market. Reconstruct price history from the trade stream, detect spikes, look backward for which wallets entered positions in the pre-spike window, flag wallets that appear repeatedly.

**Approach B (external data, later):** Correlate with actual news timestamps from GDELT, NewsAPI, or Twitter/X. Requires NLP/LLM layer to match markets to news events.

These two signals are independent and complementary. Signal 1 catches wallets with implausible results. Signal 2 catches wallets with suspicious timing. The most damning cases (like the known ones) trigger both.

## Known Cases

**AlphaRacoon (Google, Dec 2025)**
- Deposited $3M, bet on 23 Google "Year in Search" outcomes, got 22 correct, netted $1.15M in 24 hours
- Bets were on extremely niche questions (e.g., whether the singer d4vd would top the list) at ~5% odds
- Previously made $150K+ predicting the exact Gemini 3.0 launch day
- Used main wallet, had a username, no evasion at all
- Caught by a blockchain engineer who noticed the accuracy

**ricosuave666 (Israeli military, Feb 2026)**
- IDF reservist won 7/7 bets on Israel-Iran strike timing, earning ~$152K
- First criminal prosecution globally for prediction market insider trading
- Caught by a community member who noticed a perfect record on war-related markets

**Burdensome-Mix (Venezuela/Maduro, Dec 2025)**
- New account, wagered $34K across 13 bets on Maduro removal, returned $437K
- Final bet placed less than 1 hour before Trump ordered military strike
- Three related wallets profited a combined $630K+
- On-chain analyst traced funding chain back through Coinbase; CFTC opened investigation

### Pattern Across Cases
- Near-perfect accuracy on niche/obscure markets where even skilled forecasters would have uncertainty
- Large concentrated positions (not diversified)
- All discovered after the fact by humans noticing implausible records
