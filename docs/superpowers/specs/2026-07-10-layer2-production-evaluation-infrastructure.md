## Problem Statement

Hero Radar cannot currently make a release-quality comparison between Layer 2 Scoring Investigator versions. The existing deterministic evaluator can report all twenty scoring cases as passing because each Python fixture contains the response and axes that are later graded. It therefore measures fixture consistency rather than the behavior of the production Preflight, Scoring Context Builder, request contract, bounded tool loop, repair/finalization path, or Chinese Brief Writer.

The existing real-provider smoke is useful only as a schema and provider handshake: it covers a small hand-picked subset, disables tools, and injects evaluation-only calibration. It is not production-equivalent. The historical `l2_20260709T044146` run, with no first-turn finals and many rejected or failed GitHub file calls, motivates trajectory evaluation but is not v2 ground truth.

As a result, prompt, context-policy, schema, model, and tool-registry changes cannot be compared on identical inputs with deterministic tool evidence. Maintainers cannot reliably see whether a new version improves score calibration while worsening route choice, invalid tool use, stopping, grounding, telemetry, or brief quality. They also cannot run the twenty long-lived cases offline through the real production seams, reproduce a paired run, or use its result as a CI gate.

## Solution

Build a versioned, network-free Layer 2 component-evaluation system around the two existing production seams.

The primary seam is the real `score_with_investigator()` entry point. Each evaluation trial constructs a production Candidate Group, invokes the real Preflight and Scoring Context Builder, supplies the production request contract and prompt version, and executes the full bounded tool loop against deterministic replay ToolSpecs. The second seam invokes the existing production Chinese Brief Writer entry point with its compact brief packet for cases that require a brief.

Move the twenty embedded scoring cases into a strict JSONL dataset. Separate model-facing case input and replay recordings from gold labels, grader configuration, and human-review metadata. Parse and validate every case before execution, and prove that no expected value or grader-only field can enter a scorer or brief request.

Support paired v1/v2 experiments with identical case inputs, recording version, provider/model settings, context and tool budgets, output budgets, and at least three uncached trials per version. Repetition is represented by an explicit trial identity and an isolated provider/cache execution context; production request semantics are not mutated to force cache misses.

Persist versioned per-case machine-readable artifacts and a human-readable comparison report. Grade actual production outputs and traces for score, route, tool trajectory, stopping and repair behavior, evidence references, grounding, known gaps, telemetry, cost, and—when selected—the Chinese Brief Writer packet and output. Missing telemetry remains distinguishable from measured zero. Return non-zero exit codes when dataset validation, execution, or configured grading thresholds fail.

Keep the existing lightweight static evaluator and real-provider handshake clearly named as smoke checks. They may remain useful, but neither is presented as the production-equivalent Agent evaluation.

## User Stories

1. As a Hero Radar maintainer, I want the twenty scoring cases stored in versioned JSONL, so that changes to inputs and expectations are reviewable without reading Python fixture construction.
2. As a Hero Radar maintainer, I want each dataset line validated against a strict case contract, so that malformed or ambiguous cases fail before a model call.
3. As a prompt maintainer, I want model-facing inputs separated from gold labels, so that expected answers can never calibrate the model under evaluation.
4. As a prompt maintainer, I want replay tool recordings separated from gold labels, so that recorded evidence is usable without exposing expected outcomes.
5. As an evaluator, I want grader configuration separated from human-review metadata, so that automatic pass/fail policy and blind-review instructions have distinct owners.
6. As a maintainer, I want stable case IDs and a dataset version, so that reports and baselines remain comparable after names or descriptions change.
7. As an evaluator, I want the offline run to call the real production scoring entry point, so that the result includes Preflight, Context Builder, request contract, tool loop, validation, repair, and finalization behavior.
8. As an evaluator, I want production Candidate Groups built from dataset inputs, so that no eval-only scorer payload bypasses the production context boundary.
9. As an evaluator, I want prompt v1 and v2 selected through the production prompt-version contract, so that paired results measure the versions actually shipped.
10. As an evaluator, I want deterministic replay implementations of evidence-row retrieval, GitHub README, GitHub file, homepage/docs, and web search, so that offline runs exercise every production tool family without network access.
11. As a security-conscious maintainer, I want replay recordings sanitized and secret-free, so that fixtures and artifacts are safe to commit.
12. As an evaluator, I want replay recordings to include successful observations, so that grounding and score behavior can be tested against useful evidence.
13. As an evaluator, I want fixed 403 and 404 recordings, so that failed first-party retrieval and known-gap behavior are reproducible.
14. As an evaluator, I want rate-limit and timeout recordings, so that retry, stopping, and caveat behavior can be graded.
15. As an evaluator, I want unavailable and generic error recordings, so that the agent cannot assume every tool succeeds.
16. As an evaluator, I want tool-budget scenarios, so that budget-exceeded behavior and must-finalize transitions are inspectable.
17. As a maintainer, I want replay tools exposed through production ToolSpecs, so that model schemas, argument validation, candidate authorization, family limits, and projection are exercised.
18. As a maintainer, I want every replay lookup deterministic, so that the same case, trial, and recording version produce the same tool result.
19. As an evaluator, I want an unexpected replay request recorded as an error rather than sent to the network, so that fixture gaps are visible and offline safety is preserved.
20. As an evaluator, I want v1 and v2 to receive exactly the same case inputs and replay evidence, so that differences are attributable to the evaluated version.
21. As an evaluator, I want paired versions to share provider/model settings and budgets, so that comparison variables remain controlled.
22. As an evaluator, I want at least three trials per version, so that nondeterministic provider behavior is visible rather than hidden by one sample.
23. As an evaluator, I want every trial to be uncached, so that three rows cannot be three reads of the same response.
24. As a maintainer, I want cache isolation implemented outside production semantics, so that evaluation support cannot alter shipped request meaning.
25. As an evaluator, I want score bands and allowed numeric intervals graded, so that both coarse and boundary calibration failures are visible.
26. As an evaluator, I want the actual Preflight mode graded, so that direct-final, investigate, and cannot-score behavior are evaluated independently of the final score.
27. As an evaluator, I want actual feed route behavior graded where the case specifies it, so that high scores do not silently produce the wrong downstream classification.
28. As an evaluator, I want tool necessity and allowed or forbidden tool families graded, so that correct scores reached through wasteful or unsafe investigation can fail.
29. As an evaluator, I want invalid, rejected, unauthorized, repeated, duplicate, unnecessary, error, and budget-related calls counted separately, so that trajectory regressions are diagnosable.
30. As an evaluator, I want must-finalize and stopping behavior graded, so that the agent cannot consume all turns without returning a valid decision.
31. As an evaluator, I want repair count and final-output validity graded, so that schema fragility is visible even when repair eventually succeeds.
32. As an evaluator, I want evidence references checked against evidence rows and replay observations actually supplied to the model, so that fabricated citations fail.
33. As an evaluator, I want supporting and negative claims grounded against cited recorded evidence, so that a syntactically valid reference is not enough.
34. As an evaluator, I want failed-tool cases to retain explicit known gaps, so that unavailable evidence is not silently converted into certainty.
35. As an evaluator, I want turn count and per-turn trace persisted, so that first-turn finalization and investigation depth can be compared.
36. As an evaluator, I want input and output tokens recorded when supplied by the provider, so that context and output costs can be measured.
37. As an evaluator, I want missing token telemetry represented as missing rather than zero, so that absent instrumentation is not misreported as free usage.
38. As an evaluator, I want latency and estimated or provider-reported cost persisted per trial, so that quality changes can be weighed against runtime cost.
39. As a product owner, I want cases that require a brief to invoke the production Chinese Brief Writer, so that scoring quality and user-facing writing quality are evaluated together.
40. As an evaluator, I want the compact brief input persisted, so that I can verify it contains project facts and decisions without internal investigation noise.
41. As an evaluator, I want brief prompt, schema, context-policy, provider, and model versions persisted, so that writing artifacts are reproducible.
42. As an evaluator, I want brief latency, tokens, and cost reported separately from scoring, so that component tuning remains independent.
43. As an evaluator, I want deterministic brief structure and Chinese-content checks, so that missing fields or unusable outputs fail automatically.
44. As an evaluator, I want internal-process leakage checks, so that raw traces, tool errors, cache internals, and evaluation labels do not appear in the brief.
45. As an evaluator, I want evidence-grounding and caveat checks or explicit grader hooks, so that unsupported fluent briefs are flagged for review.
46. As a human reviewer, I want blind v1/v2 brief artifacts without version-identifying content, so that writing comparisons are not biased.
47. As a maintainer, I want a machine-readable result per case, version, and trial, so that downstream analysis does not scrape prose.
48. As a maintainer, I want aggregate summaries by version, case, grader, tool family, and failure type, so that regressions are easy to locate.
49. As a human reviewer, I want a report that places v1 and v2 differences side by side, so that score, trajectory, grounding, telemetry, and brief changes are inspectable.
50. As a human reviewer, I want case- and trial-level failures listed, so that aggregate pass rates cannot hide unstable behavior.
51. As an evaluator, I want dataset, grader, git, prompt, model, schema, context-policy, tool-registry, recording, and budget versions persisted, so that every result states what produced it.
52. As an evaluator, I want scorer and brief request fingerprints persisted, so that paired-input equality and cache isolation can be audited.
53. As an evaluator, I want trial numbers and run IDs persisted, so that repeated executions never overwrite one another accidentally.
54. As a CI operator, I want distinct non-zero exit codes for invalid data, execution failure, and grading failure, so that automation can identify the failure class.
55. As a maintainer, I want the legacy static evaluator explicitly labeled a schema smoke, so that a fixture-authored response cannot be mistaken for production-equivalent quality.
56. As a maintainer, I want the small live-provider path explicitly labeled a provider smoke, so that disabled tools and eval-only calibration are visible limitations.
57. As a maintainer, I want focused tests proving gold labels never enter scorer or brief requests, so that leakage prevention remains a regression invariant.
58. As a maintainer, I want focused tests proving both production entry points are invoked, so that future refactors cannot substitute eval-only implementations.
59. As a maintainer, I want replay/offline tests to remain network-free and deterministic, so that the full component evaluation can run locally and in CI.
60. As a maintainer, I want a clean explicit evaluation API, so that compatibility behavior is visible rather than hidden behind production dual paths.

## Implementation Decisions

- Add a Layer 2 evaluation package with explicit dataset models, replay tools, provider adapters, composable graders, artifact models, reporting, and a command-line runner. The runner is a component evaluation, not a feed, source, or end-to-end pipeline evaluation.
- Use a versioned JSONL dataset as the source of the twenty cases. Each line has four top-level ownership areas: model input, replay scenario references, gold/grader expectations, and human-review metadata. Parsing rejects unknown top-level and contract fields where forward compatibility is not explicitly versioned.
- Remove fixture-authored model responses from the release-quality dataset. If legacy smoke compatibility still needs response examples, keep them in smoke-only code or derive them from a clearly named smoke fixture; the production-equivalent runner never grades those authored responses as actual behavior.
- Convert dataset candidate input to the existing Candidate Group contract. Evidence IDs are stable and attributable. Gold values are held by the runner and graders and are never passed to the group, provider adapter, tool registry, Context Builder, scorer, or brief packet.
- Invoke the existing production scoring function directly with a real in-memory decision database, production context budgets, production InvestigatorLimits, production prompt versions, and replay ToolSpecs. Do not introduce an eval-only system prompt or scoring loop.
- Capture production requests through a provider adapter that delegates to a configured response source while recording sanitized requests, response telemetry, latency, and usage. Offline deterministic tests may use scripted behavioral responses, but those responses are trial inputs owned by the provider adapter rather than gold labels embedded in cases.
- Treat an offline replay run and a live-provider run as two provider modes over the same component runner. The offline mode is mandatory, deterministic, and network-free. A live-provider artifact is optional and must be reported as intentionally absent when credentials or authorization are unavailable.
- Build replay executors for all five production tool names and expose them with the production candidate-aware ToolSpec contract. Recording matching uses normalized tool name and arguments plus a recording version. An unmatched call returns a deterministic unavailable/error observation and never falls through to a network client.
- Record success, forbidden/403, missing/404, rate-limited, timeout, unavailable/error, and budget-related behavior in the dataset recording area. Budget exhaustion produced by the production host remains distinct from a recorded remote error.
- A paired experiment has one immutable configuration shared by both versions except the explicitly varied version identity. It includes dataset and recording versions, provider/model settings, prompt versions, schema/context/tool-registry versions, context and tool limits, output limits, trial count, and cost rates.
- Require at least three trials for paired release comparison. Each trial receives an isolated in-memory database and provider instance. Cache-capable provider adapters use a trial-scoped namespace or disable their own response cache explicitly; no production payload field or scoring behavior is changed to create a miss.
- Compose graders around persisted production output and trace: score interval/band, Preflight mode and route, tool necessity/family/trajectory, stopping/repair/final validity, evidence-reference validity, lexical claim grounding, failed-tool known gaps, turns, usage/latency/cost, and brief quality.
- Treat grounding as deterministic evidence-overlap validation plus a grader hook for semantic review. The automatic result states when semantic grading is unavailable rather than silently passing it.
- Derive invalid, rejected, candidate-boundary-rejected, duplicate, repeated, unnecessary, remote-error, and budget-exceeded metrics from actual tool traces and production state. Fixture-authored trajectories are expectations only.
- Query production model-call telemetry from the decision database when available and combine it with provider adapter timing/usage. Nullable telemetry fields remain nullable. Cost records whether it is provider-reported, estimated from configured rates, or missing.
- Select brief-required cases from explicit case metadata and invoke the existing production Brief Writer function on the actual scored row. Persist its exact compact input as captured at the provider boundary, normalized output, request fingerprint, component versions, timing, usage, and grading results.
- Generate blind brief artifacts using stable opaque labels whose mapping to v1/v2 is stored separately from reviewer-facing files. Never place expected labels, version names, or grader results inside the blind brief content.
- Write artifacts beneath a versioned run directory: immutable run metadata, JSONL per-case results, aggregate JSON, Markdown comparison report, and optional blind-review packets. Artifact payloads are sanitized before writing.
- The report compares v1/v2 at case and trial level, reports pass rates and deltas by grader, lists failures and tool statuses, displays nullable telemetry as `missing`, and links each summary row to its machine-readable identifiers.
- Preserve the existing evaluator under a name and CLI description that identifies it as static/schema smoke. Preserve the small real-provider path as provider smoke. The new component runner is the only path described as production-equivalent replay evaluation.
- Provide exit code zero only when parsing, execution, artifact persistence, and configured grading thresholds succeed. Use separate codes for dataset/configuration errors, execution/artifact errors, and completed runs with grading failures.
- Add only minimal compatibility wiring needed for the scorer and brief seams. Do not add CI workflow files unless an existing workflow requires a one-line command integration; the CLI exit contract is sufficient for this scope.

## Testing Decisions

- Tests assert externally visible evaluation behavior: validated dataset contracts, requests observed at the production provider boundary, replayed tool calls and outputs, production result/trace artifacts, grader outcomes, reports, and exit codes. They do not assert private helper call order.
- The highest scoring seam is the existing production `score_with_investigator()` entry point. A focused integration test patches or spies on that symbol, runs a dataset case, and verifies Preflight, Context Builder, production request contract, and full tool-loop fields are present in the captured request.
- The second seam is the existing production Chinese Brief Writer entry point. A focused integration test runs a brief-required scored case and verifies the compact packet, request contract, output, and separate telemetry artifact.
- Add red tests first for strict JSONL parsing, unknown fields, duplicate IDs, model/gold separation, request leakage, deterministic recording lookup, unmatched-call network prevention, and recorded failure statuses.
- Add trajectory tests for allowed and forbidden families, invalid arguments, candidate authorization, duplicate signatures, repeated calls, unnecessary calls, tool budgets, must-finalize, repair count, and invalid final output.
- Add grading tests for score intervals/bands, route, citation validity, lexical grounding, known gaps after failed tools, nullable telemetry, cost provenance, brief structure, Chinese content, process leakage, caveats, and semantic-grader hook states.
- Add paired-run tests proving the two versions receive identical model-facing case inputs, recordings, provider/model settings, budgets, and output limits across three isolated trials, while request fingerprints differ only where the production versioned contract differs.
- Add artifact tests for deterministic JSONL, aggregate JSON, Markdown diff content, case/trial failure visibility, `missing` versus zero telemetry, reproducibility metadata, blind brief packet separation, and sanitization.
- Add CLI tests for successful, dataset-invalid, execution-failed, and grading-failed exit codes.
- Reuse the repository's existing in-memory SQLite initialization, fake provider patterns, Layer 2 scorer tests, request-contract assertions, tool-registry tests, context-builder tests, and brief-packet tests as prior art.
- Run focused evaluation tests throughout implementation, then the Layer 2 scorer/context/brief/tool suites, any available static or compile checks, and the complete Python test suite once at the end.

## Out of Scope

- Source ingestion, source classifiers, HN/X/npm behavior, resolver behavior, entity grouping, and Candidate Pool admission.
- Edge Scout, legacy deepdive, feed ranking, feed router quality, dashboard/UI work, and unrelated architecture.
- Changing the Layer 2 scoring rubric, aggregate formula, product policy, or gold outcomes based on the historical run.
- Treating `l2_20260709T044146` as v2 truth; it is motivation and a baseline hypothesis only.
- Network access from replay tools or offline tests.
- Building a general-purpose LLM evaluation framework, judge-model service, annotation UI, or experiment database.
- Adding write-capable model tools, unbounded browsing, recursive agents, or provider-managed persistent sessions.
- Building new CI workflows beyond the evaluation command and failure exit codes.
- Requiring a live-provider run when credentials, budget, or separate authorization are unavailable.

## Further Notes

- Published implementation issue: [#3 — Build production-equivalent Layer 2 evaluation infrastructure](https://github.com/FUY25/hero-radar/issues/3), labeled `ready-for-agent`.
- Current HEAD already defaults to the v2 Layer 2 architecture. Implementation must inspect and use the current production contracts rather than reconstructing an earlier architecture.
- The old twenty-case evaluator remains useful as a quick score-schema smoke, but its authored axes and responses make it unsuitable as release evidence.
- The old small Kimi run remains useful as a live provider handshake, but disabled tools and eval-only calibration make it unsuitable as a production-equivalent Agent evaluation.
- The historical run's zero first-turn finals and rejected/error GitHub-file calls motivate explicit route, trajectory, authorization, stopping, and failure graders.
- The release artifact does not need to include a live-provider result. When no live run is performed, the final implementation report and generated report must say so explicitly and still demonstrate the full production-equivalent offline replay path.
- Definition of done is: the published issue carries `ready-for-agent`; all twenty cases load from JSONL and execute through the production scorer; replay tools cover the required success and failure classes; paired v1/v2 by three isolated trials is supported; score, route, trajectory, grounding, telemetry, and brief behavior are graded and reported; focused and full tests pass; code review findings are addressed; and the in-scope changes are committed without pushing.
