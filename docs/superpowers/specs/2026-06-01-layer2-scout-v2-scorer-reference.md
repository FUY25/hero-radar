# Layer 2 Scout V2 Scorer Reference

This note preserves the Scout v2 prompt shape, architecture, and real Kimi eval
lessons before Scout is simplified into a wide rough triage gate.

## Why Preserve This

Scout v2 became too close to scoring. It asked Kimi to judge:

- `workflow_shift`
- `technical_substance`
- `product_market_fit`

Those axes are still useful, but they belong primarily in Layer 2 scoring. The
wide Scout should only decide whether an Edge Watch item is worth spending a
scoring call on.

## Scout V2 Production Shape

Prompt version:

```text
layer2-edge-scout-v2
```

Core system prompt:

```text
You are the Edge Watch Scout for Hero Radar.
Decide whether each edge_watch candidate is a concrete product, repo, package,
tool, or workflow worth Layer 2 scoring.

Evaluate candidates independently. Do not rank, compare, or enforce a quota.
Return strict JSON with top-level decisions array. Each decision must include:
group_id, is_concrete_product boolean, object_type string,
workflow_shift, technical_substance, product_market_fit, confidence number 0..1,
and reason string.

Use novelty values only: none, weak, medium, strong.
Medium is not enough for inclusion; at least one novelty axis must be strong.
Do not require an academic breakthrough for a strong axis. Strong technical
substance can be an unusual system combination, local runtime, validation or
release-evidence harness, memory/tool protocol, multi-agent runtime, or
inspectable reliability mechanism.
News, articles, tutorials, discussions, standalone model releases, and unknown
objects are not concrete products unless the candidate is actually about a
linked product/repo/package/workflow.
```

Application gate:

```text
include_in_l2_scoring =
  is_concrete_product
  AND one or more of:
    workflow_shift == strong
    technical_substance == strong
    product_market_fit == strong
```

Important lesson: this is useful scorer calibration, but too strict and too
expensive for wide Edge Watch scout.

## Real Kimi Eval Lessons

Positive anchors:

- HeyClicky passed as `workflow_shift=strong`.
- Hermes Agent passed as a stateful, memory/skills agent.
- OpenClaw initially failed because Kimi treated it as a crowded local assistant
  category with all axes `medium`.

Prompt/context refinement that fixed OpenClaw:

- Say strong technical substance does not require an academic breakthrough.
- Explicitly call out validation/release-evidence harnesses as strong technical
  substance.
- Include concrete evidence language in the candidate context:
  `release evidence`, `validation evidence`, durable CI, performance, memory,
  install, and reliability checks.

Negative anchors:

- Generic chatbot should fail.
- AI/model news should fail.
- Tutorial/resource list should fail.
- Standalone model release should fail.
- Medium-only repo should fail under v2.

## Scorer Guidance

The scorer should reuse these axes, but score them on 0-100 scales:

```text
workflow_shift
  Does this change interaction, workflow, or operating mode?

technical_substance
  Does this have a meaningful system mechanism, unusual combination,
  reliability/eval/release-evidence harness, memory/tool protocol, runtime, or
  integration depth?

product_market_fit
  Is there a clear user, pain, workflow entry point, or product wedge?
```

Scoring should be allowed to distinguish `medium` from `strong`. Wide Scout
should not.

## Wide Scout Replacement Direction

The replacement Scout should be a rough triage gate:

```text
Edge Watch candidates -> Wide Scout -> Scoring -> Deepdive
```

Wide Scout should see thin candidate cards in large batches and return only the
items worth scoring. It should not output a row for every candidate, and it
should not assign axis strengths.

Output target:

```json
{
  "promotions": [
    {
      "group_id": "group:abc",
      "reason_code": "possible_workflow_shift",
      "reason": "Short reason."
    }
  ]
}
```

Unreturned candidates are treated as filtered by the application.
