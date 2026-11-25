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
shows the total, the spend per stage and per specialist, and names
under every sum how many of its records have no price. A stage with no
priced record is "not defined", never a zero.

The cost is computed once, when the record is written (from a
notification or by the `tokens` command), and stored with it as
`cost_usd` and `cost_quality`. It is the price of the day the work was
done: a later change of the table does not rewrite the history, and
every reader reports the stored value. A record from before costs were
stored has none and is estimated from the current table.

Of the table's names, the longest one that matches the model wins:
`glm-5.3-flash` is priced as itself, not as `glm-5.3` (whose name is a
substring of it).

The technical level of the board adds a block per model: input and
output tokens, cache read and cache write, the price per 1M tokens the
cost was priced by (`observability.price_per_1m`, the same row of the
table `compute_cost` uses), the spend and the quality of its price.

## Budget

A team may agree a budget: `budget_usd` for the team,
`budget_per_task_usd` for one task, `budget_warn_ratio` for the share
from which the board warns (0.8 by default). The lead sets or changes
it with `team.py budget` and the lead token; it is an audited decision.

The engine enforces it at one place, `claim`, before a workspace is
made. When the known spend reached the budget, or the known spend of
the task reached its limit, the claim is refused (`BUDGET_LIMIT`, exit
code 2). An attempt that is already running is never stopped, and a
refused claim leaves nothing behind. Records without a price do not add
to the spend: the limit counts what is known, and the board says so.
Both sides are compared at the precision of the price table, so a
budget that is exactly spent is spent.
