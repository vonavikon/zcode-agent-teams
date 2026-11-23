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
per successful task; the Langfuse trace carries the total.
