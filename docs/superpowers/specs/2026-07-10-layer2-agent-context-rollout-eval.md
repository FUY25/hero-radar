# Layer 2 Agent Context Rollout Evaluation

Date: 2026-07-10

## Decision

Use `v2` as the production/default Layer 2 scoring prompt. Both prompt
candidates use the same `layer2-scoring-output-v2` contract so comparison does
not conflate prompt quality with output-shape changes.

The `v2` prompt, strict response contract, context builder, tool registry, rate policies,
observation projection, and claim attribution are implemented and enabled by default.
The version registry selects `v1` or `v2` exactly and rejects unknown versions; `v1`
remains the explicit rollback option. Direct-final mode and Edge Scout remain disabled.

## Fixed Corpus

The repository evaluation corpus contains 20 normalized candidate cases covering high,
medium, and low expected outcomes, mixed evidence quality, missing fields, tool-use
decisions, and attribution requirements. All 20 deterministic fixtures now use the
production `layer2-scoring-output-v2` schema, attributable claim objects, and the same
host validator as the runtime; the static contract evaluation passed 20/20.

The versioned cases and labels are embedded in the deterministic evaluation
runner and exercised by its test module:

- `pipeline/decision/run_layer2_evals.py`
- `tests/test_layer2_evals.py`

## Historical Real-API Smoke

Three fixed candidates were selected before execution to cover the main score bands:

| Candidate | Expected band | v1 result | v2 result at 3,000 output tokens | Validation used |
|---|---:|---:|---:|---|
| OpenClaw | High | 76.50 | 79.25 | Legacy simplified eval shape |
| Generic AI chatbot | Low | 0.00 | 0.00 | Legacy simplified eval shape |
| Screen-aware spreadsheet assistant | Medium/gray-zone | 73.50 | 71.95 | Legacy simplified eval shape |

Labels were not included in model requests. At 1,800 output tokens, the v2 responses
were empty or truncated before producing valid JSON. Re-running the same three cases
with a 3,000-token output cap produced complete responses that matched the expected
score bands. Those historical calls predated production-schema validation and do not
count as production contract passes. The real-provider eval now sends the production
schema, requires attributable claims against the supplied candidate evidence reference,
and runs the production host validator. The scorer cap and output reserve remain 3,000;
the independent Chinese Brief cap is 3,000.

This remains a smoke test rather than a substitute for complete corpus and human
comparison. The default changed to `v2` after its tool-failure evidence policy,
stopping policy, output wording, and fail-closed version selection were tightened.

## Ongoing Validation and Rollback Gate

With `prompt_version` on `v2`, run and persist all of the following:

1. The complete fixed corpus through both v1 and v2 using the same normalized inputs.
2. Human comparison of score quality, evidence attribution, uncertainty handling, and
   tool-use necessity without exposing expected labels to either model request.
3. Contract, latency, token, tool-call, rate-limit, and failure-isolation measurements.
4. Confirmation that v2 has no material quality regression and stays within the agreed
   latency/cost envelope.
5. A rollback drill proving that configuration can return the default to v1 without a
   schema or persistence migration.

## Current Rollout State

- Default scoring prompt: `v2`
- Explicit rollback prompt: `v1`
- Scorer output cap/reserve: 3,000 tokens
- Brief output cap: 3,000 tokens
- Direct-final mode: disabled
- Edge Scout: disabled
- Legacy path: disabled
- Strict v2 output-contract host validation and one repair attempt: enabled when
  the v2 prompt candidate is selected; v1 retains its compatibility normalizer
- Candidate-bound tool authorization, per-tool rate limits, and bounded parallel tool
  execution: enabled
- Full sanitized raw tool results: persisted separately from the bounded model-facing
  trace
