# Layer 2 Agent Context Rollout Evaluation

Date: 2026-07-10

## Decision

Keep the production/default Layer 2 scoring prompt on `v1`. Both prompt
candidates use the same `layer2-scoring-output-v2` contract so comparison does
not conflate prompt quality with output-shape changes.

The `v2` prompt, strict response contract, context builder, tool registry, rate policies,
observation projection, and claim attribution are implemented and selectable, but `v2`
must not become the default until the complete fixed evaluation corpus and human review
gate pass. Direct-final mode and Edge Scout remain disabled.

## Fixed Corpus

The repository evaluation corpus contains 20 normalized candidate cases covering high,
medium, and low expected outcomes, mixed evidence quality, missing fields, tool-use
decisions, and attribution requirements. The static contract evaluation passed 20/20.

The versioned cases and labels are embedded in the deterministic evaluation
runner and exercised by its test module:

- `pipeline/decision/run_layer2_evals.py`
- `tests/test_layer2_evals.py`

## Small Real-API Check

Three fixed candidates were selected before execution to cover the main score bands:

| Candidate | Expected band | v1 result | v2 result at 3,000 output tokens | Contract result |
|---|---:|---:|---:|---|
| OpenClaw | High | 76.50 | 79.25 | Pass |
| Generic AI chatbot | Low | 0.00 | 0.00 | Pass |
| Screen-aware spreadsheet assistant | Medium/gray-zone | 73.50 | 71.95 | Pass |

Labels were not included in model requests. At 1,800 output tokens, the v2 responses
were empty or truncated before producing valid JSON. Re-running the same three cases
with a 3,000-token output cap produced complete responses that passed the expected
band/contract checks. The scorer cap and output reserve are therefore set to 3,000;
the independent Chinese Brief cap remains 1,000.

This is a smoke test, not sufficient evidence to enable v2 by default.

## Enablement Gate

Before changing `prompt_version` to `v2`, run and persist all of the following:

1. The complete fixed corpus through both v1 and v2 using the same normalized inputs.
2. Human comparison of score quality, evidence attribution, uncertainty handling, and
   tool-use necessity without exposing expected labels to either model request.
3. Contract, latency, token, tool-call, rate-limit, and failure-isolation measurements.
4. Confirmation that v2 has no material quality regression and stays within the agreed
   latency/cost envelope.
5. A rollback drill proving that configuration can return the default to v1 without a
   schema or persistence migration.

## Current Safe Rollout State

- Default scoring prompt: `v1`
- Selectable scoring prompt: `v2`
- Scorer output cap/reserve: 3,000 tokens
- Brief output cap: 1,000 tokens
- Direct-final mode: disabled
- Edge Scout: disabled
- Legacy path: disabled
- Strict v2 output-contract host validation and one repair attempt: enabled when
  the v2 prompt candidate is selected; v1 retains its compatibility normalizer
- Candidate-bound tool authorization, per-tool rate limits, and bounded parallel tool
  execution: enabled
- Full sanitized raw tool results: persisted separately from the bounded model-facing
  trace
