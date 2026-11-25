# Cost model

Cost is computed per usage record from a small price table
(`observability.PRICE_TABLE_USD_1M`): USD per 1M input, output and
cache-read tokens for the common models. The table is a fallback, not a
contract: prices change and providers differ.

Every cost carries a quality flag:

- `estimated`: computed from the table (either the exact input/output
  split, or an explicit 1:3 assumption when only the total is known),
- `unknown`: no model, no token numbers, or an unknown model. Never a
  guessed number.

`cost` breaks the run down per task; `metrics` adds cost per task and
per successful task; the Langfuse trace carries the total; the board
shows the total and says "not less than" when some records are unknown.

The cost is computed once, when the record is written (from a
notification or by the `tokens` command), and stored with it as
`cost_usd` and `cost_quality`. It is the price of the day the work was
done: a later change of the table does not rewrite the history, and
every reader reports the stored value. A record from before costs were
stored has none and is estimated from the current table.

Of the table's names, the longest one that matches the model wins:
`glm-5.3-flash` is priced as itself, not as `glm-5.3` (whose name is a
substring of it).
