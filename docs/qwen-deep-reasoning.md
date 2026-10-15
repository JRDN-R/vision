# Qwen Ultra Deep reasoning

Deep is the default for newly accepted Ultra requests after this update. Select
**Venture → Settings → Qwen Ultra → Reasoning mode** to choose Deep or Standard.
Existing accepted jobs retain their saved mode. Standard keeps the previous
response flow. The existing native-thinking toggle and sampling values are
preserved; no sampling setting is presented as an accuracy guarantee.

Deep uses three fresh inference requests on the same loaded model:

1. Produce a concise provisional answer, checking assumptions and uncertainty.
2. Critique that answer against the original request and supplied evidence.
3. Write the final response, accepting only supported corrections.

The original prepared source evidence remains available in every stage. Private
reasoning is discarded. Draft and critique answer text exist only in bounded
worker memory and are never saved as the conversation's answer or sent to the
browser. Only final-answer text streams to the conversation. Progress shows
Drafting, Reviewing, and Writing final answer, without inventing an ETA or
treating these three stages as percentages of elapsed work.

This is same-model self-review, not independent factual verification. It cannot
add missing knowledge, browse, execute code, or guarantee better answers. A
critique can itself be wrong. The final prompt explicitly preserves correct
claims, rejects unsupported corrections, and asks for uncertainty where needed.
The actual accuracy difference needs a paired benchmark on the installed model.

## Budgets and limits

| Stage | Answer space reserved | Native thinking cap |
| --- | --- | --- |
| Draft | 2,048 tokens | 4,096 tokens |
| Review | 1,024 tokens | 2,048 tokens |
| Final | Selected output reserve, 512–4,096 | 4,096 tokens |

The deployed tokenizer counts each complete stage prompt. Every call leaves the
existing context margin and final-output space; native thinking shrinks when
necessary. A small closing allowance is included. The limits are caps, not
mandatory token consumption or artificial delays. Native thinking can be turned
off without disabling the three-stage cycle. Sampling controls apply to all
three stages, including a saved seed.

If full evidence and intermediate artifacts cannot fit, the job reports a context
error instead of silently dropping them or publishing the draft. If draft/review
output hits its token cap, that artifact is explicitly marked partial for the
next stage and the UI displays a review-coverage note. A failed, empty or
disconnected intermediate stage stops the job. Explicit refusal/filter results
are not retried through a different stage.

Long responses applies to the **final answer**: it may continue across bounded
passes using the existing continuation logic. Turning it off still runs all
three Deep stages, with one final-answer pass. All stages share the same
cancellation callback, resource lock and five-hour maximum processing deadline.
Closing the browser does not cancel accepted work. Restarting the server
interrupts the job; it does not replay hidden stages automatically. Only any
already-streamed final-answer text is retained. Deep timings are excluded from
the existing single-pass ETA calibration.

## Update the existing FUPCJ installation

The normal `vision-pc/Update-Venture.ps1` updater installs `local_reasoning.py`
with the other server files from one resolved commit. No model download or
hardware retuning is required if Ultra is already installed and validated.
Run the updater in Administrator PowerShell when no local response is running,
then refresh the hosted app or reopen the current portable HTML. A new client
connected to an old server disables the Deep selector and explains that the
server update is required.

## Validation

Protocol fixtures exercise three distinct inference calls, evidence retention,
hidden-artifact isolation, usage totals, bounded native thinking, partial review
labels, failure, cancellation, context overflow and final-answer continuations.
Flask integration tests exercise accepted background jobs, reopened snapshots,
account isolation and exclusion from single-pass timing calibration. Browser
tests cover the selector, preference restoration and mobile/desktop progress.
These tests validate orchestration, not the accuracy of generated answers.
