
Apply the same architecture can absolutely be applied to mid-cap and large-cap day trading, but I would not use the exact same thresholds or weighting.
The framework is actually more useful if you make it market-cap agnostic and have different profiles for small-, mid-, and large-cap stocks.

What should stay the same
Your core pipeline works across all three:
Universe → Opportunity → Setup → Entry Quality → Risk → Position Size → Trade Management

The concepts remain:

Catalyst
Relative volume
Relative strength
VWAP
ORB
PDH/PMH
Breakout/reclaim
Volume confirmation
Room to target
R:R
Entry extension
Spread/liquidity
Stop distance
Risk-based sizing
Trade management
Your current scanner already has most of these components.
But the weighting should change
Small-cap profile
Your current scanner is optimized toward:
Momentum + volatility + scarcity of shares + catalyst

Example:

Float       VERY important
RVOL        VERY important
Gap         VERY important
ATR         VERY important
Catalyst    VERY important
Halt risk   VERY important
Dilution    VERY important
Spread      VERY important
This is appropriate for the type of stocks you're scanning.
Mid-cap profile
For mid-caps, I'd reduce the importance of float and increase:
Market regime
Sector strength
Relative strength
Institutional volume
VWAP structure
Trend
Catalyst quality
Liquidity
A mid-cap doesn't need a tiny float to make a good day trade.
Instead of asking:

"Can this squeeze?"
you increasingly ask:
"Is institutional money consistently pushing this in one direction?"
Large-cap profile
For large caps, I'd shift even further toward:
Market regime
Sector/industry strength
Catalyst
Relative strength
Volume acceleration
VWAP
Opening range
Trend structure
Key levels
Options activity
Liquidity
Float becomes almost irrelevant.
For example, a stock can have hundreds of millions/billions of shares outstanding and still produce an excellent intraday setup.

I'd create 3 scanner modes
Instead of maintaining three completely different scanners:
SMALL_CAP
MID_CAP
LARGE_CAP
build one Day Trading Decision Engine with profiles.
Profile 1 — Small Cap
Catalyst             20%
RVOL                 15%
Float/Supply         15%
Volatility            15%
Setup                 15%
Relative Strength     10%
Liquidity              5%
Market                 5%
Profile 2 — Mid Cap
Catalyst             15%
Relative Strength     15%
Volume/RVOL           15%
Setup                 20%
Market Regime         10%
Sector Strength       10%
Volatility             5%
Liquidity              5%
Key Levels             5%
Profile 3 — Large Cap
Market Regime         15%
Sector Strength       15%
Catalyst              15%
Relative Strength     15%
Setup                 15%
Volume Acceleration   10%
Key Levels             5%
Volatility              5%
Liquidity              5%
Those are starting weights, not something I'd hard-code permanently. You should eventually optimize them from your own trade history.
The biggest change: different definitions of "momentum"
This is important.
For small caps:

Momentum = explosive price + volume + float rotation
For mid caps:
Momentum = sustained directional movement + volume + relative strength
For large caps:
Momentum = institutional participation + sector/market confirmation + directional persistence
So your scanner shouldn't simply say:
RVOL > 2
for everyone.
Instead:

Small cap
RVOL > 3
might be interesting.
Mid cap
RVOL > 1.5–2
can be meaningful.
Large cap
Even:
RVOL = 1.3
can be significant if the stock is moving strongly with a catalyst.
Your "float" module should become "supply/liquidity"
This is one architectural change I'd make.
Currently you have:

FLOAT 46.7M, 61.2M, 48.7M, etc.
For small caps:
FLOAT
FLOAT ROTATION
SHARES OUTSTANDING
ATM/OFFERING
WARRANTS
For large caps:
FLOAT → low importance

Instead:

AVERAGE DAILY $ VOLUME
INTRADAY $ VOLUME
SPREAD
DEPTH
SLIPPAGE
OPTIONS LIQUIDITY
So create:
SUPPLY/LIQUIDITY ENGINE
rather than a simple float score.
Your catalyst engine should also change
Small-cap:
FDA
earnings
contract
offering
merger
partnership
clinical data
guidance
Large-cap:
earnings
guidance
analyst revisions
economic data
Fed
product launch
M&A
regulatory news
sector news
CEO/CFO news
And importantly:
Catalyst quality
Don't just use:
CATALYST = Earnings
Add:
CATALYST SURPRISE
For earnings:
EPS surprise
Revenue surprise
Guidance change
Margin change
Analyst reaction
Pre-market reaction
This becomes much more powerful for mid/large caps.
Market regime becomes MUCH more important
For your small-cap scanner, an individual stock can sometimes trade independently of the broader market.
For large caps, I would heavily incorporate:

SPY
QQQ
IWM
Sector ETF
Industry group
VIX
Market breadth
For example:
AAPL
   ↓
XLK
   ↓
QQQ
   ↓
SPY
If all four are bullish, a long AAPL setup gets a substantial confirmation boost.
If AAPL is bullish but:

QQQ ↓
XLK ↓
SPY ↓
then your scanner should downgrade it.
I'd add a "Relative Strength Hierarchy"
This could be one of your best improvements.
Instead of only:

RS vs SPY
which you're already calculating,
calculate:

Stock vs SPY
Stock vs QQQ
Stock vs IWM
Stock vs Sector ETF
Stock vs Industry ETF
Then calculate:
Multi-level RS
RS Score =
Stock vs Market
+
Stock vs Sector
+
Stock vs Industry
For example:
AAPL

vs SPY       +2.1%
vs QQQ       +1.4%
vs XLK       +1.8%
vs Semis     +3.2%
That's a much stronger signal than simply:
RS vs SPY = +2.1%
Large caps need "institutional volume"
This is another major addition.
For small caps you're interested in:

RVOL
Float rotation
Volume spikes
For large caps:
Relative dollar volume
Volume acceleration
Block activity
Opening auction volume
VWAP volume
Breakout volume
The key question becomes:
Is volume increasing as price moves in the desired direction?
rather than simply:
Is RVOL high?
Options activity becomes much more useful
Since you trade 0DTE options on large caps, I'd make your large-cap profile options-aware.
For example:

Options Volume
Options Volume / Average
Call/Put Volume
Call/Put OI
ATM volume
Near-ATM volume
IV
IV change
Expected move
Price vs expected move
Then your stock scanner can say:
Example
NVDA

Stock setup       91
Market regime     88
Sector            94
Relative strength 92
Options activity  96
Entry quality     84
Risk              82

FINAL = 90
🔥 A TRADE
That's much more useful for your large-cap options trading than a small-cap float score.
Your expected-move concept becomes extremely valuable
You already calculate things such as:
distance to next level / expected move.
For large-cap options, I'd make this a first-class metric:
Expected Move
Current Move
% Expected Move Used
Room / Expected Move
Example:
Expected move = $6

Stock already moved = $5

Expected move consumed = 83%
Then:
🔴 Do not chase
Even if every other indicator is bullish.
This is particularly useful for 0DTE.

I would therefore build one universal engine
Something like:
                    DAY TRADE ENGINE
                           │
              ┌────────────┴────────────┐
              │     MARKET PROFILE       │
              └────────────┬────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ↓                  ↓                  ↓
    SMALL CAP          MID CAP           LARGE CAP
        │                  │                  │
    Float/Supply       Liquidity          Options
    RVOL               Sector RS         Market Regime
    Catalyst           Volume            Sector
    Volatility          Trend             Institutional Flow
        │                  │                  │
        └──────────────────┼──────────────────┘
                           ↓
                    SETUP ENGINE
                           ↓
                    ENTRY ENGINE
                           ↓
                    RISK ENGINE
                           ↓
                 POSITION SIZE ENGINE
                           ↓
                  TRADE MANAGEMENT
The key principle:
Don't build three scanners.
Build:

ONE decision engine + three market-cap profiles.
That will make your system much easier to maintain and backtest.
And I would keep your existing small-cap scanner as Profile A, rather than replacing it. Your current structure already contains the right building blocks; the main upgrade is making the scoring, tradeability, liquidity, and risk logic adapt to the stock's market-cap/liquidity regime.