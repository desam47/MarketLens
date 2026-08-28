# PHASE 18 — FUTURE DATA PROVIDER ARCHITECTURE

Add provider abstractions for:

NewsProvider
FundamentalProvider
OptionsProvider

Do not make these required.

NEWS

Support:

headline
source
timestamp
symbol
relevance

FUNDAMENTALS

Support future fields:

market cap
revenue
EPS
growth
P/E
debt
cash
institutional ownership
insider ownership

OPTIONS

Support:

calls
puts
volume
open interest
IV
IV rank
Greeks
put/call ratio
unusual activity

Keep these completely separate from the core trend engine.

Do not implement trading execution.

Add provider configuration.

Then STOP.
