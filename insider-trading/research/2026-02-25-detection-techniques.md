# Detection Techniques

## Core Insight

Polymarket has no enforcement mechanism. No SEC, no subpoenas. Insiders have no reason to hide — they can use their main wallet, keep their username, go all-in without creating fresh wallets or splitting across accounts. Detection that relies on evasion behavior (new wallets, funding clusters, sybil patterns) will miss the most blatant cases.

Detection should focus on **whose results are statistically implausible**, not who is trying to hide.

## Three Core Signals

### 1. Repeated Correct Contrarian Calls
Taking positions against market consensus that consistently prove correct. A wallet buying YES at 10% that resolves YES is interesting once. Doing it repeatedly across markets is a pattern that luck doesn't explain.

### 2. Timing Relative to Information Release
Consistently entering positions shortly before news breaks. The window in known cases is typically 30 minutes to 4 hours before announcements.

### 3. Profit Concentration / Statistical Implausibility
Returns that no reasonable model of skill or luck can explain. Every known Polymarket insider case was caught because someone noticed a record that was too perfect.

These signals are strong independently and get stronger together. They don't depend on assumptions about evasion behavior.

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
