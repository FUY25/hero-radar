# Hero Radar Layer 2 Agent Context, Tool Contract, and System Prompt

## Problem Statement

Hero Radar's Layer 2 Scoring Investigator is already a bounded local agent, but its model-facing contract has not yet caught up with the sophistication of its host runtime.

The host currently owns candidate scheduling, tool execution, budgets, rate limits, cache access, SQLite persistence, validation, repair, and failure isolation. The model, however, receives a comparatively loose request: a large candidate payload, duplicated identity fields, a list of tool names without complete argument schemas, static placeholder state, cumulative raw tool traces, and a monolithic system prompt that mixes stable policy with runtime details.

This mismatch causes concrete product and engineering problems:

- The model cannot reliably select valid tool arguments because model-facing tool documentation and executable validation are not derived from one source of truth.
- The latest observed run produced many rejected or failed GitHub file requests, including paths the host would never allow.
- Every observed candidate used tools before finalizing, including candidates that may already have had sufficient first-party context.
- Per-turn request size grows monotonically because the complete candidate packet and cumulative raw tool trace are resent.
- Repair requests duplicate state that is already present elsewhere in the same payload.
- Several model-facing state fields are permanently empty or initialized to misleading weak values, which may bias the model toward unnecessary investigation.
- Cache correctness depends on manually maintained version strings rather than the complete request contract. A prompt, schema, sampling, or tool-definition change can incorrectly reuse an old response.
- Repository content, webpages, search results, source notes, and tool output are not explicitly labeled as untrusted external evidence.
- Scoring claims use free-form evidence strings that cannot be validated against the exact evidence rows or tool observations supplied to the model.
- The Chinese Brief Writer is a separate LLM component in practice, but its normal provider/model configuration and payload are still coupled to the scorer.
- Resolver-approved GitHub aliases improve grouping and canonical links, but they do not consistently participate in the earlier README enrichment path.
- Provider token usage and context composition are not captured with enough fidelity to calibrate a real token budget.

The latest observed complete Layer 2 run produced 194 candidate groups and scored 104 candidates. No candidate finalized on turn 1. Average serialized request size grew from roughly 5.9k characters on turn 1 to 11.8k on turn 2 and 13.9k on turn 3; the largest repair request was roughly 43.9k characters. The persisted tool trace averaged roughly 8.2k characters and reached roughly 20.3k characters.

The GitHub file tool is the clearest contract failure. The observed run contained 25 successful calls, 64 host-side path rejections, 52 execution errors, and 16 budget-exceeded calls. The model saw the tool name and a few prose examples, while the actual path allowlist remained hidden in host code.

The user wants a smaller, more truthful, bounded, and auditable Layer 2 context system. They do not want a general-purpose coding-agent harness, a provider-managed persistent thread, or a monolithic agent that merges Resolver, scoring, brief writing, Edge Scout, and legacy deepdive. Hero Radar should retain local ownership of the agent loop while making configuration, prompts, tools, context, cache, evidence attribution, and telemetry explicit.

The source review for this spec is the Layer 2 Agent Configuration, Context, Tools, and System Prompt Review dated 2026-07-10. Its architecture comparison used an unofficial third-party Claude Code sourcemap mirror only for clean-room architectural archaeology. No source code or prompt text from that mirror may be copied into Hero Radar.

## Solution

Keep the existing locally managed, stateless-reassembly agent model, but replace the repeated JSON dump with an explicit context policy and component architecture.

The target workflow is:

~~~text
Deterministic Decision
    └── Link Resolver Agent
            owns identity and canonical-link resolution only

Layer 2
    ├── Deterministic Router
    │       owns admission, selection, and feed routing
    ├── Scoring Context Builder
    │       owns bounded model-facing context
    ├── Candidate-Aware Tool Registry
    │       owns model schemas and host execution policy
    ├── Scoring Investigator Agent
    │       owns bounded investigation and scoring judgment
    └── Chinese Brief Writer
            owns one-shot Chinese writing from a compact brief packet
~~~

Full raw requests, responses, tool results, errors, and traces remain persisted for audit and debugging. The active model context becomes a bounded, attributable projection over that persisted state.

The solution has sixteen linked requirements:

1. Split Layer 2 configuration into routing, scoring agent, brief writer, tool runtime, Edge Scout, and legacy deepdive ownership boundaries.
2. Give the Brief Writer an explicit provider, model, timeout, output-token limit, prompt identity, schema version, and concurrency.
3. Replace the model-facing tool-name list and separately maintained executor dictionary with one structured ToolSpec registry.
4. Compute the LLM response cache key from the complete sanitized request contract rather than only provider, model, task, prompt version, and input payload.
5. Add an explicit untrusted-external-evidence boundary to scorer and brief prompts and to tool observations.
6. Remove duplicated identity, duplicated repair trace, permanently empty state, and hard-coded misleading sufficiency fields.
7. Introduce a Scoring Context Builder with a component-specific token budget and an inspectable context manifest.
8. Preserve raw tool traces in storage while projecting older results into structured ToolObservation records for active context.
9. Carry previous-turn intent, used normalized tool signatures, real information sufficiency, and remaining host-computed budget into later turns.
10. Replace free-form evidence claims with claims that cite validated evidence and observation IDs.
11. Filter tools by candidate availability and add a deterministic direct-final mode that remains disabled until evaluation proves it safe.
12. Assemble the Scoring Investigator v2 system prompt from stable named policy sections and move dynamic values into runtime context and schemas.
13. Give the Brief Writer a compact project-facts packet rather than raw investigation and tool traces.
14. Allow approved Resolver GitHub aliases to make non-GitHub canonical candidates eligible for README enrichment.
15. Capture actual provider usage and context-composition metrics for scorer, repair, brief, and web-search calls.
16. Preserve module boundaries, keep Edge Scout and legacy deepdive disabled, and avoid provider-managed or cross-candidate memory.

Delivery will be phased so that configuration migration, tool contracts, cache semantics, context compaction, prompt changes, and brief changes can be evaluated independently. Prompt v2 and direct-final behavior must not be enabled before baseline fixtures and fixed evaluation cases exist.

## User Stories

1. As a Hero Radar product owner, I want Layer 2 to remain a locally owned bounded agent, so that product behavior is not dependent on opaque provider-managed session state.
2. As a Hero Radar product owner, I want Resolver, Scoring Investigator, Brief Writer, Edge Scout, and legacy deepdive to remain separate components, so that each component has one objective and one failure owner.
3. As a Hero Radar product owner, I want Edge Scout to remain disabled, so that this architecture project does not change candidate admission policy.
4. As a Hero Radar product owner, I want legacy deepdive to remain disabled, so that deprecated prompts and tools do not enter active scoring requests.
5. As a Hero Radar operator, I want every Layer 2 configuration key to have one component owner, so that I can tell which runtime behavior a setting controls.
6. As a Hero Radar operator, I want scorer and Brief Writer provider/model settings to be independent, so that I can optimize judgment and Chinese writing separately.
7. As a Hero Radar operator, I want the recorded model profile to identify scorer, brief, scout, and legacy deepdive independently, so that run metadata is not ambiguous.
8. As a Hero Radar operator, I want the new nested configuration to be the canonical configuration, so that obsolete flat aliases do not create silent precedence rules.
9. As a Hero Radar operator, I want CLI and dashboard run overrides to map to the correct nested component, so that an override cannot accidentally configure another agent.
10. As a maintainer, I want configuration migration to preserve current production behavior before prompt/context changes, so that structural refactoring can be reviewed independently.
11. As a maintainer, I want one immutable ToolSpec to define each primitive tool, so that the model schema and executable tool cannot drift apart.
12. As a Scoring Investigator, I want complete tool descriptions and JSON input schemas, so that I can request valid arguments without guessing from examples.
13. As a Scoring Investigator, I want the GitHub file schema to communicate the executable path allowlist, so that I do not spend budget on paths the host will reject.
14. As a maintainer, I want model-visible tools and executable tools to be derived from the same registry, so that adding or removing a tool is an atomic change.
15. As a maintainer, I want schemas to reject unknown arguments by default, so that accidental or injected fields do not reach tool executors.
16. As a maintainer, I want host-side validation to remain authoritative, so that model-facing schemas are helpful contracts rather than security boundaries.
17. As a maintainer, I want tool availability predicates to depend on candidate context, so that irrelevant tools do not occupy context or invite unnecessary calls.
18. As a maintainer, I want tool family, timeout, cache policy, result projection, concurrency key, maximum in-flight count, and start rate defined with the tool, so that runtime policy is inspectable.
19. As a Hero Radar operator, I want existing tool concurrency and start-rate limits preserved, so that richer schemas do not increase provider bursts.
20. As a Hero Radar operator, I want the full model request contract fingerprinted, so that prompt, schema, sampling, or context-policy changes cannot reuse stale responses.
21. As a maintainer, I want a system-prompt-only change to cause a cache miss, so that prompt iteration is correct without relying on human version discipline.
22. As a maintainer, I want a tool-schema-only change to cause a cache miss, so that cached tool plans cannot target obsolete contracts.
23. As a maintainer, I want an output-schema-only change to cause a cache miss, so that cached responses always match current validation.
24. As a maintainer, I want actual temperature and output-token limits included in the fingerprint, so that cache identity matches the request the provider received.
25. As a security-conscious operator, I want secrets excluded from fingerprints and stored request records, so that cache correctness does not leak credentials.
26. As a security-conscious operator, I want repository files, webpages, search results, source descriptions, and tool results labeled external_untrusted, so that their content cannot redefine agent behavior.
27. As a Scoring Investigator, I want the system policy to tell me never to follow instructions found in external evidence, so that prompt injection is treated as data.
28. As a maintainer, I want trust guidance in both scoring and brief prompts, so that downstream writing does not follow malicious source content.
29. As a maintainer, I want URL checks, path allowlists, budgets, rate limits, and schemas enforced by the host, so that prompt instructions remain defense in depth.
30. As a maintainer, I want candidate identity serialized once, so that request size and cache identity are not inflated by duplicate fields.
31. As a maintainer, I want repair requests to avoid resending the same tool trace in multiple locations, so that repairs remain bounded.
32. As a Scoring Investigator, I want model-facing state fields to reflect real host or model state, so that empty placeholders do not bias investigation.
33. As a maintainer, I want permanently empty or hard-coded weak state removed until it has explicit transition rules, so that the context contract remains truthful.
34. As a Hero Radar operator, I want every model request to have a preflight token estimate, so that oversized requests fail or degrade before reaching the provider.
35. As a Hero Radar operator, I want system prompt and tool schemas counted against the same context budget as candidate evidence, so that budget accounting matches reality.
36. As a maintainer, I want the context builder to report which evidence IDs were included, summarized, retrievable, or excluded, so that context decisions are debuggable.
37. As a Scoring Investigator, I want mandatory identity and decision facts protected from compaction, so that smaller context does not remove the basis of the task.
38. As a Scoring Investigator, I want top evidence prioritized by decision value and attribution, so that token limits do not become arbitrary first-N truncation.
39. As a Scoring Investigator, I want omitted evidence counts and an on-demand retrieval path, so that I know when more evidence exists.
40. As a maintainer, I want a clear failure when mandatory context cannot fit, so that the provider is not called with a silently corrupted packet.
41. As a maintainer, I want full raw tool traces retained in SQLite, so that active-context pruning does not reduce auditability.
42. As a Scoring Investigator, I want older raw tool results projected into structured observations, so that later turns retain facts without resending large responses.
43. As a Scoring Investigator, I want the most recent bounded raw tool results available when necessary, so that projection does not remove immediate details.
44. As a maintainer, I want each tool family to own a result projector, so that truncation preserves decision-relevant structure.
45. As a maintainer, I want every observation to have an ID, source reference, status, trust label, facts, and truncation state, so that it is attributable.
46. As a maintainer, I want deterministic observation ordering, so that completion timing cannot change model input or cache identity.
47. As a Scoring Investigator, I want turn 2 and turn 3 to receive the previous investigation question, so that tool output remains connected to the reason it was requested.
48. As a Scoring Investigator, I want the prior question to identify target axes and expected decision impact, so that additional calls remain purposeful.
49. As a Scoring Investigator, I want host-computed remaining turn and tool budgets, so that I do not reason from stale configured maxima.
50. As a maintainer, I want admitted calls to reduce remaining budgets before execution, so that concurrent work cannot oversubscribe limits.
51. As a maintainer, I want normalized used tool signatures persisted in working state, so that duplicate calls are rejected across turns.
52. As a Scoring Investigator, I want information sufficiency updated from real turn output or deterministic preflight, so that it can guide stopping.
53. As a Scoring Investigator, I want a direct-final mode for candidates with sufficient first-party context, so that unnecessary tool calls are avoided.
54. As a product owner, I want direct-final mode disabled until a fixed evaluation proves no scoring-quality regression, so that cost reduction does not suppress important candidates.
55. As a Scoring Investigator, I want only candidate-relevant tools exposed, so that available tools reflect the actual investigation.
56. As a Scoring Investigator, I want evidence retrieval hidden when all evidence is already present, so that initial context and tools are not redundant.
57. As a Scoring Investigator, I want README and file tools exposed only for resolved repositories, so that invalid repository calls are prevented.
58. As a Scoring Investigator, I want homepage/docs exposed only for safe canonical URLs with real information gaps, so that fetching remains bounded.
59. As a Scoring Investigator, I want web search reserved for unresolved identity, missing first-party material, or independent momentum verification, so that browsing does not become the default.
60. As a product owner, I want scoring claims to cite exact evidence or observation IDs, so that Layer 2 conclusions are auditable.
61. As a maintainer, I want unknown evidence references rejected or repaired, so that the scorer cannot cite facts it did not receive.
62. As a maintainer, I want observed facts distinguishable from inference, so that downstream readers can interpret confidence correctly.
63. As a product owner, I want high axis scores to require attributable support under the rubric, so that strong scores cannot rest on unsupported prose.
64. As an API/UI consumer, I want cited claim structures rendered as concise evidence text, so that schema improvements do not break the Feed.
65. As a prompt maintainer, I want scorer policy assembled from named stable sections, so that role, evidence, rubric, tools, stopping, and output rules can be reviewed separately.
66. As a prompt maintainer, I want dynamic candidate data, dates, tools, and numeric budgets outside the system prompt, so that stable policy can be versioned and cached correctly.
67. As a Scoring Investigator, I want calibrated axis definitions and score bands, so that scores are consistent across candidates.
68. As a Scoring Investigator, I want momentum separated from workflow shift and technical substance, so that popularity cannot substitute for product value.
69. As a Scoring Investigator, I want a stopping rule tied to decision impact, so that more evidence is not treated as automatically better.
70. As a Scoring Investigator, I want must_finalize and exhausted budgets to force a valid final response, so that bounded runs terminate predictably.
71. As a prompt maintainer, I want prompt v1 and v2 evaluated on the same candidate set, so that improvements are evidence-based.
72. As a Chinese Brief Writer, I want an independent provider/model identity, so that my writing objective is not implicitly coupled to the scorer.
73. As a Chinese Brief Writer, I want a compact packet of identity, project facts, decision, caveats, gaps, and top evidence references, so that I can write about the project rather than the investigation process.
74. As a Chinese Brief Writer, I want no executable tools, raw tool trace, retry history, or HTTP errors, so that the brief remains a one-shot writing task.
75. As a maintainer, I want brief facts derived from attributable scored claims and observations, so that compact context does not invite invention.
76. As a Hero Radar operator, I want scorer and brief token usage, latency, cache hits, and errors attributed separately, so that the two components can be tuned independently.
77. As a Hero Radar operator, I want Resolver-approved GitHub aliases to participate in README enrichment, so that resolved name-only candidates receive first-party context before scoring.
78. As a maintainer, I want unapproved or low-confidence link proposals excluded from README enrichment, so that uncertain identity does not trigger fetches.
79. As a maintainer, I want canonical and alias repository identities deduplicated before fetch, so that the same README is not fetched twice.
80. As a Hero Radar operator, I want actual provider token usage persisted when available, so that context budgets can be calibrated from real data.
81. As a Hero Radar operator, I want estimated prompt composition persisted even when provider usage is unavailable, so that all runs remain comparable.
82. As a Hero Radar operator, I want actual temperature and output-token policy recorded, so that run behavior is reproducible.
83. As a Hero Radar operator, I want turns, tool outcomes, repair rate, context percentiles, and cache hit rate reported by component, so that regressions are visible.
84. As a product owner, I want score stability and agreement with fixed human labels measured, so that lower latency and token use do not hide quality regressions.
85. As a maintainer, I want full tests, web tests, production build, and a fixed Layer 2 evaluation to pass before rollout, so that architecture changes do not break the Feed.
86. As a maintainer, I want each phase delivered in small reviewable commits, so that configuration, tools, cache, context, prompts, and brief behavior are not mixed into one change.
87. As a maintainer, I want disabled component prompts and schemas excluded from active requests, so that unused modules do not consume tokens or leak instructions.
88. As a product owner, I want the architecture to avoid cross-candidate memory and vector retrieval, so that a maximum-three-turn scorer stays simple and auditable.
89. As a product owner, I want deterministic routing and score aggregation preserved, so that this project improves context engineering without silently redefining product policy.
90. As a maintainer, I want the clean-room reference caveat preserved, so that implementation borrows architectural principles without copying third-party source or prompt text.

## Implementation Decisions

- The review document is supporting evidence; this spec is the implementation contract. If the two conflict, this spec governs unless the product owner explicitly amends it.
- The locally owned agent loop remains the production architecture. Every provider call is independently reconstructible from persisted, sanitized request state. No provider-managed persistent thread is introduced.
- Deterministic routing, candidate selection, host budgets, tool admission, rate limits, validation, score aggregation, and feed assembly remain host-owned.
- The Link Resolver Agent remains in Decision and owns identity/canonical-link resolution only. Its reasoning transcript does not enter Layer 2; only approved aliases, proposals, confidence, and bounded link artifacts do.
- The Scoring Investigator Agent owns semantic scoring and read-only investigation only.
- The Chinese Brief Writer remains a separate one-shot LLM component with no tools.
- Edge Scout and legacy deepdive remain distinct disabled components. Their prompts and schemas are not included in scorer context while disabled.

### Priority and rollout gates

- P0 requirements are configuration ownership, independent Brief Writer identity, ToolSpec, complete request fingerprinting, untrusted-evidence boundaries, and removal of duplicate/fake context.
- P1 requirements are bounded context building, observation projection, real working state, evidence references, candidate-aware tools/direct-final evaluation, modular scorer prompt v2, compact brief packets, and Resolver alias integration.
- P2 requirements are full provider/context telemetry and continued boundary enforcement.
- Minimal baseline telemetry and fixtures are Phase 0 prerequisites even though full telemetry is categorized P2.
- No P1 context compaction or prompt rollout begins until P0 request contracts and cache semantics are stable.
- Direct-final mode remains disabled until the evaluation gate passes.
- Each phase may contain several small commits, but one commit must not combine configuration migration, tool-schema migration, prompt replacement, and context compaction.

### Canonical configuration ownership

- The Layer 2 configuration becomes a nested component schema with these owners:

  - routing: scoring limits, brief selection thresholds/counts, score-only threshold, and known paradigm keys;
  - scoring_agent: provider/model, prompt/schema/context-policy identities, timeout, output limit, concurrency, turn/repair behavior, context budget, and per-candidate tool budgets;
  - brief_writer: enabled state, provider/model, prompt/schema identity, timeout, output limit, and concurrency;
  - tool_runtime: registry version, tool data limits, and provider-family concurrency/start-rate policies;
  - edge_scout: enabled state and independent provider/model;
  - legacy_deepdive: enabled state and independent provider/model.

- The nested schema is canonical. All config readers, command builders, dashboard overrides, fixtures, and run metadata migrate in the configuration phase.
- Indefinite dual-reading of flat and nested keys is not allowed. A short, explicit migration shim is acceptable only if a real external caller cannot migrate atomically; it must emit a deprecation error or warning and have a removal condition.
- The model profile records scorer, brief, scout, and legacy deepdive independently.
- No component silently falls back to another component's model. Scorer and Brief Writer may use the same concrete model only when configured explicitly.
- The initial nested values preserve current production behavior: scorer concurrency five, brief concurrency four, same current model choices, current routing thresholds, current tool budgets, current family concurrency/start rates, Edge Scout false, and legacy deepdive false.

### Brief Writer identity

- Brief Writer configuration includes provider, model, timeout, maximum output tokens, prompt ID/version, output-schema version, and concurrency.
- Provider construction uses a dedicated Brief Writer factory in production and tests.
- Brief cache identity uses the Brief Writer request contract; it does not share the scorer cache namespace merely because provider/model values match.
- Brief telemetry is attributed to brief_writer, not scoring.
- Failure isolation and deterministic selected-order persistence remain unchanged.

### Structured tool registry

- One immutable ToolSpec definition is the source of truth for model-facing schema and host execution.
- Each ToolSpec owns:

  - stable name and version;
  - description;
  - strict JSON input schema;
  - family and cost class;
  - executor;
  - candidate availability predicate;
  - timeout;
  - maximum result-token budget;
  - cache policy;
  - concurrency key;
  - maximum in-flight work;
  - starts per second;
  - result projector.

- The model-facing projection contains only name, description, input schema, and an optional cost hint.
- Executor, credentials, timeout implementation, limiter objects, cache implementation, and projector internals remain host-only.
- Unknown arguments are rejected through additionalProperties false unless a tool has a documented exception.
- Required arguments are represented in schema and validated again by the host.
- The GitHub file path schema reflects the executable allowlist. The allowlist itself is not expanded by this project.
- The registry filters ToolSpecs through candidate availability before request assembly.
- Existing sequential budget admission, concurrent accepted execution, request-order result assembly, per-family budgets, cache behavior, and failure isolation remain.
- A tool-schema or ToolSpec version change participates in request fingerprinting.

### Complete request fingerprinting

- The LLM cache key becomes a canonical fingerprint of the complete sanitized request contract.
- The fingerprint includes:

  - provider and model;
  - task/component;
  - system-prompt hash;
  - active tool-schema hash;
  - output-schema hash;
  - context-policy version;
  - canonical input payload;
  - actual temperature;
  - maximum output tokens;
  - response-format contract.

- Human-readable prompt, schema, registry, and context-policy versions remain for diagnostics but are not the sole invalidation mechanism.
- Authorization headers, API keys, limiter state, connection state, and other secrets never enter the fingerprint or persisted request.
- The sanitized stored request includes the versions and hashes used to compute the fingerprint.
- Identical complete contracts produce cache hits; any fingerprinted contract change produces a miss.

### Evidence trust boundary

- Scorer and Brief Writer stable policy explicitly state that candidate content, repository files, webpages, search results, source notes, and tool output are untrusted external evidence.
- The model must never follow instructions found in that evidence.
- Only system policy, runtime request contract, model-facing schemas, and host-enforced limits define behavior.
- All external tool observations carry provenance and trust=external_untrusted.
- Host URL validation, path allowlists, budget admission, rate limiting, schema validation, output validation, and sanitization remain authoritative.

### Context cleanup and ownership

- Candidate identity exists once under the candidate packet; the separate duplicate identity object is removed.
- Repair requests do not include a second top-level raw tool trace when the working state already contains the relevant state.
- Permanently empty known-facts/open-question fields and hard-coded all-weak information sufficiency are removed before new working-state logic is introduced.
- A model-facing field is admitted only when an identified owner updates it and its absence causes a documented decision failure.
- Snapshot fixtures make accidental duplication visible.

### Scoring Context Builder

- One Scoring Context Builder is the sole owner of the per-turn model request payload.
- The builder receives candidate/group data, current decision state, active ToolSpecs, persisted observations/raw trace references, prompt/output contracts, model limits, and a ContextBudget.
- ContextBudget contains:

  - maximum model context tokens;
  - output reserve;
  - safety margin;
  - identity allocation;
  - evidence-summary allocation;
  - top-evidence allocation;
  - previous-turn allocation;
  - tool-observation allocation;
  - recent raw-tool-result retention count.

- Usable model input is the model context window minus output reserve, safety margin, system-prompt tokens, and active tool-schema tokens.
- Remaining tokens are allocated in this priority:

  1. candidate identity and current decision metadata;
  2. hard scoring/routing facts;
  3. compact evidence summary;
  4. top attributable evidence;
  5. previous-turn delta;
  6. cumulative structured observations;
  7. bounded most-recent raw tool results.

- A provider tokenizer is preferred. A conservative estimator is permitted until provider usage calibrates it.
- Every request produces a context manifest containing estimated totals and included, summarized, retrievable, and excluded evidence/observation IDs.
- Mandatory context that cannot fit causes a clear pre-provider failure.
- The context builder is component-specific; Brief Writer uses a separate packet builder and budget.

### Persisted trace versus active observations

- Full raw tool trace remains persisted and queryable for audit.
- Active context uses ToolObservation records plus a configurable number of recent bounded raw results.
- ToolObservation contains a stable observation ID, tool, source reference, status, trust label, projected facts, bounded excerpt, truncation flag, and relevant scoring axes.
- README, GitHub file/manifest, homepage/docs, web-search, evidence-row, and error results use family-specific projectors.
- Generic mid-JSON character truncation is not the primary active-context representation.
- Observation order is deterministic and based on requested turn/index, not completion timing.
- Context pruning never deletes persisted raw results.

### Working state and remaining budget

- Later turns receive a bounded previous_turn object with:

  - the prior information question;
  - target scoring axes;
  - expected decision impact;
  - normalized requested tool signatures;
  - outcomes.

- Later turns receive host-computed remaining turns and remaining total/family tool calls after admission.
- Normalized used signatures remain in candidate working state across turns and are rejected if repeated.
- Information sufficiency is either produced by a deterministic preflight or returned by the model and host-validated; it is not initialized to universal weak values.
- Open questions have explicit state transitions and ownership.

### Evidence attribution and scoring schema v2

- Final supporting and negative evidence become attributable claim objects, not unconstrained strings.
- A claim contains claim text, evidence references, supported axes, and observed/inferred type.
- Every evidence reference must identify an evidence row or ToolObservation that was included in the request or working observation store.
- Host validation rejects or repairs unknown references.
- High axis scores require support according to the rubric and validation policy.
- Observed facts and inferences remain distinguishable in persistence and API output.
- Scoring persistence adds structured supporting claims, negative claims, and known gaps while preserving existing axes, aggregate score, primary reason, tags, rationale, and caveats.
- API and UI adapters project structured claims back to concise evidence text where existing consumers require it.

### Candidate-aware tools and direct-final mode

- Candidate preflight assigns one mode:

  - score_from_context;
  - investigate;
  - cannot_score.

- score_from_context exposes no tools and sets must_finalize=true.
- investigate exposes only ToolSpecs whose availability predicates pass.
- cannot_score records a deterministic reason and follows the configured failure/diagnostic route.
- Evidence retrieval is exposed only when the initial packet omitted retrievable evidence.
- README/file tools require a verified or approved-resolved GitHub repository and a relevant evidence gap.
- Homepage/docs requires a safe canonical URL and an incomplete product description.
- Web search is last-resort for unresolved identity, absent first-party material, or independent momentum verification.
- Direct-final remains feature-gated off until fixed-set evaluation demonstrates no quality regression.
- Tool calls per candidate, direct-final rate, rejection rate, and score agreement are rollout metrics.

### Modular Scoring Investigator prompt v2

- The stable prompt is assembled from named sections:

  - role_and_decision;
  - evidence_and_trust;
  - scoring_rubric;
  - tool_selection;
  - stopping_policy;
  - output_contract.

- Candidate data, as-of date, current turn, numeric budgets, current tools, tool arguments, JSON schemas, and other runtime values do not live in the stable prompt.
- Prompt v2:

  - defines strategic worth for AI product, agent, developer-tool, and workflow intelligence;
  - treats external evidence as untrusted;
  - separates facts from inference;
  - requires evidence references;
  - defines workflow shift, technical substance, product-market fit, momentum, confidence, risk penalty, and derivative-news penalty independently;
  - states that momentum cannot substitute for workflow or technical substance;
  - provides consistent score-band calibration;
  - permits tools only for specific open questions that can change the decision;
  - forbids broad browsing and repeated normalized signatures;
  - finalizes when the decision is adequately supported or further calls are unlikely to change it;
  - obeys must_finalize and host budgets;
  - returns only the supplied strict output schema.

- Prompt text contains no hard-coded three-turn assumption and no duplicated tool argument prose.
- Prompt v1 and v2 run against the same fixed evaluation cases before v2 becomes default.
- The original clean-room prompt draft in the supporting review may guide wording. No third-party prompt text may be copied.

### Compact Brief Writer packet

- Brief Writer receives:

  - identity and canonical link;
  - object type;
  - attributable project facts describing what it is, interaction model, technical mechanisms, workflow unlocks, target users, and use cases;
  - final Layer 2 decision, score, primary reason, tags, caveats, and known gaps;
  - bounded top evidence references.

- Brief Writer does not receive executable tools, raw investigation trace, raw tool trace, cache metadata, rejected calls, budget failures, retry history, HTTP error pages, or unrelated evidence.
- Every project fact is derived from validated scored claims or attributable observations.
- Existing Chinese brief output semantics remain: category, headline, core highlights, end-user use cases, and optional caveat.

### Resolver alias to README enrichment

- README candidate selection uses the same approved-alias policy as candidate context and Layer 2 grouping.
- A non-GitHub canonical entity with an approved GitHub alias becomes eligible.
- Unapproved proposals and low-confidence links do not trigger enrichment.
- Canonical and alias repository keys are normalized and deduplicated before cache lookup/fetch.
- The cached README preview reaches initial Layer 2 context so the scorer can avoid an unnecessary README tool call.

### Telemetry and persistence

- Existing raw LLM request/response cache and raw scoring/tool traces remain.
- A per-model-call telemetry record is added or extended to identify:

  - feed run and candidate group;
  - component and turn/attempt;
  - provider/model;
  - request fingerprint and cache key;
  - prompt/schema/registry/context-policy versions;
  - call status and latency;
  - provider prompt, completion, cached-input, and total token usage when available;
  - actual temperature and maximum output tokens;
  - estimated system-prompt, tool-schema, candidate, evidence, previous-turn, observation, and total input tokens;
  - context manifest;
  - sanitized error metadata.

- Scorer, repair, brief, and built-in web-search calls are distinguishable.
- Full raw tool trace remains in the investigation record.
- Structured observation trace and context manifests are persisted separately from the raw trace, either as explicit columns or a dedicated run-scoped record. The implementation must choose one queryable schema and cover migration.
- Structured supporting claims, negative claims, and known gaps are persisted with scores.
- Secrets and authorization headers never enter telemetry.

### Compatibility and migration

- No change is made to deterministic candidate levels, routing formulas, aggregate scoring formula, or feed section thresholds except configuration relocation.
- Existing candidate-level failure isolation, ok_with_errors behavior, deterministic persistence order, caches, concurrent scoring, concurrent tools, and concurrent briefs remain.
- Schema evolution is additive and run-scoped. Existing historical runs remain readable.
- API and UI payloads remain backward-compatible at their current external seam; new attributable fields may be added.
- Tests and fixtures migrate with the canonical nested configuration in the same phase.

### Implementation sequence

1. Phase 0 — Baseline and contract fixtures

   - Freeze a representative candidate evaluation set and human labels.
   - Save normalized scorer request snapshots and current output labels.
   - Record turns, tool statuses, request size, repairs, scores, cache behavior, and brief outcomes without changing behavior.

2. Phase 1 — Configuration-only refactor

   - Introduce canonical nested component configuration.
   - Migrate every reader, CLI/API mapping, fixture, and model profile.
   - Give Brief Writer independent construction.
   - Preserve prompts, payloads, routing, and tool behavior.

3. Phase 2 — Tool contract, cache correctness, and trust

   - Introduce ToolSpec and strict model-facing schemas.
   - Derive schemas and executors from one registry.
   - Add candidate availability without direct-final enablement.
   - Fingerprint the complete request.
   - Add trust labels and prompt-injection fixtures.

4. Phase 3 — Context builder and working state

   - Remove duplicate/fake context.
   - Introduce token preflight and context manifests.
   - Replace older active raw results with observations.
   - Add previous-turn intent, used signatures, real sufficiency, and remaining budget.

5. Phase 4 — Prompt v2 and scoring schema v2

   - Introduce modular prompt sections and attributable claims.
   - Run v1/v2 fixed-set evaluation.
   - Enable direct-final only if the quality gate passes.

6. Phase 5 — Brief packet and Resolver/README integration

   - Introduce the compact Brief Writer packet.
   - Connect approved GitHub aliases to README enrichment.
   - Run end-to-end Feed and API/UI compatibility tests.

7. Phase 6 — Rollout and calibration

   - Compare before/after telemetry.
   - Confirm invalid tool requests and context growth fall without score/brief regressions.
   - Remove any temporary migration shim after its stated condition is met.

## Testing Decisions

- Good tests assert observable contracts at the highest stable seam: canonical configuration propagation, complete provider request payload/fingerprint, model-facing ToolSpec schema plus executor outcome, persisted scoring/brief results, and Resolver alias effects. Tests should not assert thread scheduling, private helper call order, or exact token-estimator internals unless those are the public contract.
- Phase 0 freezes a fixed evaluation corpus before behavior changes. The corpus covers direct-final candidates with rich context, README-dependent repos, manifest/file-dependent repos, unresolved identity, homepage-only products, web-evidence cases, news/model-release negatives, momentum-without-substance, strong-workflow/low-momentum, prompt injection, tool errors, missing files, and rate-limit failures.
- Normalized request snapshot tests are prior art for detecting duplicate fields and contract drift. Snapshots exclude volatile timestamps, secrets, raw connection state, and nondeterministic IDs.

### Configuration tests

- Parse and validate the canonical nested configuration.
- Construct routing, scorer, Brief Writer, tool runtime, Edge Scout, and legacy deepdive independently.
- Prove scorer and Brief Writer can use different fake providers and models in one run.
- Prove disabled Edge Scout and legacy deepdive contribute no prompts, tools, schemas, or provider construction to active scoring.
- Prove CLI and dashboard overrides target the correct nested owner.
- Prove model-profile metadata distinguishes all components.
- Prove any temporary old-key migration behavior is explicit, observable, and removable.

### Tool registry tests

- Validate every model-facing schema with a JSON Schema validator.
- Prove every model-visible ToolSpec has an executor.
- Prove every executable model-callable tool has a model schema unless explicitly host-only.
- Prove unknown fields and missing required fields are rejected.
- Prove GitHub paths outside the current allowlist are rejected before execution.
- Prove valid allowlisted paths execute.
- Prove candidate availability hides irrelevant tools.
- Prove the model projection never includes credentials, limiter objects, cache internals, or executors.
- Preserve existing tests for sequential budget reservation, concurrent execution, family limits, failure isolation, and request-order result assembly.
- Prove normalized signatures cannot execute twice across turns.

### Cache tests

- An identical complete request produces a hit.
- A system-prompt-only change produces a miss.
- A ToolSpec/schema-only change produces a miss.
- An output-schema-only change produces a miss.
- A context-policy-only change produces a miss.
- A temperature-only change produces a miss.
- A maximum-output-token-only change produces a miss.
- Tool filtering changes the schema hash and produces the correct fingerprint.
- Secrets neither affect nor appear in fingerprints or stored request records.
- Existing cached historical rows remain readable or have an explicit invalidation strategy.

### Trust and prompt-injection tests

- A README containing fake system instructions remains external evidence and cannot change the action protocol.
- A repository file asking the model to use an unavailable tool cannot alter available tools.
- A homepage asking the model to ignore the rubric cannot change scoring/output contracts.
- A search result containing prompt injection remains labeled external_untrusted.
- The host rejects out-of-schema actions and arguments regardless of prompt behavior.
- Brief Writer does not reproduce or follow malicious instructions from project facts.

### Context builder tests

- Mandatory identity and decision facts are always present.
- System prompt and active ToolSpecs count against the total budget.
- Included top evidence remains attributable.
- Omitted evidence reports count and retrieval availability.
- Context manifests list included, summarized, retrievable, and excluded IDs.
- Older raw tool results become observations in active context while persisted raw traces remain unchanged.
- The configured recent raw-result count is respected.
- Turn 2 sees turn-1 intent, outcomes, observations, used signatures, and remaining budget.
- Information sufficiency changes only through its defined owner.
- Repair does not serialize identity or tool trace twice.
- Mandatory context that cannot fit fails before provider invocation.
- Conservative token estimation is deterministic; provider usage may differ without making tests flaky.

### Scoring schema and evidence tests

- action=use_tools requires a meaningful information need and at least one valid tool request.
- action=final requires a complete score and no tool requests.
- Numeric axes remain within host-enforced ranges.
- Every claim reference exists in visible evidence or observations.
- Unknown references trigger the configured repair/rejection path.
- Observed and inferred claims remain distinct.
- High score validation follows the evidence-support policy.
- Existing aggregate-score and route behavior remains.
- API/UI compatibility projects structured claims to readable evidence.

### Candidate-aware and direct-final tests

- Rich first-party context can classify as score_from_context.
- Missing technical evidence exposes the smallest relevant GitHub tool set.
- Homepage-only candidates do not receive GitHub tools.
- Unresolved identity does not receive repository tools.
- Web search appears only under documented conditions.
- cannot_score records a deterministic diagnostic reason.
- must_finalize prevents further tool requests.
- Direct-final remains disabled by default until the evaluation flag changes.
- Evaluation compares direct-final and investigation modes on the same candidates.

### Prompt v2 evaluation

- Run prompt v1 and v2 against the fixed set with equivalent model/provider settings.
- Check score agreement with human labels, route agreement, invalid tool plans, evidence-reference validity, turns, repairs, context size, and repeat stability.
- Momentum-only candidates do not gain unsupported technical-substance scores.
- Missing evidence lowers confidence rather than becoming an invented negative claim.
- Pure news, tutorials, resource lists, and standalone model releases remain appropriately penalized.
- Strong workflow candidates are not suppressed merely because momentum is low.

### Brief tests

- Brief Writer uses its own provider/model and request fingerprint.
- Brief input contains no executable tools, raw investigation trace, raw tool trace, retry history, or HTTP error pages.
- Brief project facts are attributable.
- Brief does not invent capabilities absent from project facts.
- Core highlights describe the project rather than evidence quality.
- Use cases describe actual end users rather than Hero Radar analyst activities.
- Brief persistence order and candidate-level failure isolation remain deterministic.
- Existing brief factuality/usefulness does not regress on the fixed set.

### Resolver/README tests

- A name-key entity with an approved GitHub alias becomes eligible for README enrichment.
- An unapproved proposal does not trigger README enrichment.
- A low-confidence resolver output does not trigger README enrichment.
- Canonical and alias forms of the same repository produce one cache/fetch.
- Initial Layer 2 context uses the resulting cached preview.

### Telemetry tests

- Provider usage is captured when returned and omitted cleanly when unavailable.
- Actual temperature and maximum output tokens are recorded.
- Estimates distinguish system prompt, tool schemas, candidate context, evidence, previous turn, observations, and total.
- Scorer, repair, brief, and web search are separately attributable.
- Token/latency/cache metrics contain no secrets.
- Context p50/p95/max, turns, tool outcomes, repairs, and direct-final rate can be computed from stored records.

### End-to-end verification

- Run focused tests after every phase.
- Run the complete Python suite.
- Run the complete web suite.
- Run the production web build.
- Run schema initialization and historical-run read compatibility checks.
- Run the fixed Layer 2 evaluation set before and after prompt/context rollout.
- Verify deterministic output ordering under concurrent scoring, tools, and briefs.
- Verify Edge Scout and legacy deepdive remain disabled in production configuration.

The rollout quality gate requires:

- materially fewer invalid/rejected GitHub file requests, with allowlist rejection approaching zero;
- fewer tool calls and turns without evidence-quality regression;
- bounded input-token p50/p95/max;
- 100 percent valid evidence references after repair policy;
- no regression in score agreement, strategic-candidate recall, route behavior, brief factuality, or brief usefulness;
- correct invalidation for prompt/schema/policy/sampling changes;
- explainable repeated-run score stability.

Direct-final rate and token count are not standalone success metrics. A cheaper agent that misses strategically important candidates fails the product gate.

## Out of Scope

- Converting the scorer to a provider-managed persistent thread or hosted agent session.
- Adding cross-candidate conversational memory.
- Adding vector-database retrieval for a maximum-three-turn scorer.
- Implementing general-purpose conversation compaction or session memory.
- Merging Link Resolver, Scoring Investigator, Brief Writer, Edge Scout, or legacy deepdive into one agent.
- Enabling Edge Scout.
- Enabling or redesigning legacy deepdive.
- Expanding the GitHub file allowlist without a separate security and product rationale.
- Replacing deterministic host validation, budgets, allowlists, concurrency, start-rate limits, or cache policy with prompt instructions.
- Changing deterministic Candidate Pool levels.
- Changing deterministic candidate admission or feed routing policy beyond relocating existing settings.
- Changing the deterministic aggregate Layer 2 score formula unless the fixed evaluation identifies a separate rubric defect.
- Adding unbounded browsing, recursive tool use, repository crawling, or write-capable tools.
- Giving Brief Writer tools.
- Copying code or prompt text from the unofficial Claude Code mirror.
- Depending on the unofficial mirror at runtime, in tests, or in distributed artifacts.
- Deleting full persisted raw request, response, or tool traces in the name of compaction.
- Treating lower token use, more direct-final responses, or fewer calls as success without quality evaluation.

## Further Notes

- The target is not more context or a more powerful general-purpose agent. It is a smaller and more truthful working context whose fields have owners, whose evidence is attributable, whose tools match the executable runtime, and whose growth is bounded.
- The desired request can be summarized as:

  stable system policy
  + bounded candidate facts
  + top attributable evidence
  + candidate-relevant tool schemas
  + real previous-turn state
  + remaining host budget
  + recent structured observations
  = one auditable scoring request

- The supporting review's proposed nested configuration, per-turn packet, scorer prompt v2, and output-schema v2 are decision-rich prototypes. Implementations may refine field names, but they must preserve the ownership, trust, attribution, budget, and stopping semantics specified here.
- Initial context-budget numbers are experimental defaults. They must be calibrated with provider token telemetry and the fixed evaluation set.
- The current provider may return an actual temperature different from the caller's requested temperature. Fingerprinting and telemetry use the actual request sent to the provider.
- The current local-agent ownership model is retained because it provides deterministic budgets, provider portability, SQLite transaction control, cache transparency, resume behavior, and auditable traces.
- Historical raw traces are evidence for debugging, not automatically active model context.
- The evaluation set and normalized request fixtures are long-lived regression assets and should be versioned with the prompt/schema/context-policy contracts.
- The source review is based on Hero Radar main at commit 804087a and the observed local Layer 2 run available on 2026-07-10. Future implementation should refresh baseline metrics before Phase 0 if production data has materially changed.
- The unofficial third-party Claude Code mirror has no trusted provenance or license for reuse. It may inform clean-room architecture principles only.
- Suggested commit boundaries are:

  1. freeze Layer 2 request and evaluation baselines;
  2. refactor Layer 2 configuration by component;
  3. separate Brief Writer provider configuration;
  4. add structured candidate-aware investigator tools;
  5. fingerprint complete LLM request contracts;
  6. add untrusted-evidence guardrails;
  7. build bounded scoring context packets;
  8. project tool traces into attributable observations;
  9. version the scorer prompt and evidence schema;
  10. reduce Brief Writer context to project facts;
  11. use Resolver aliases for README enrichment;
  12. add and calibrate context/token telemetry.

- Definition of done:

  - nested component configuration is the single source of truth;
  - scorer and Brief Writer have independent runtime identities;
  - model schemas and executors come from one ToolSpec registry;
  - candidate requests contain only relevant tools;
  - external content has an explicit trust boundary;
  - cache identity fingerprints the actual prompt/schema/policy/sampling contract;
  - duplicate and fake context fields are removed;
  - every turn has a bounded context packet, real working state, previous-turn intent, and remaining budget;
  - raw traces remain persisted without growing active context without bound;
  - final scoring claims cite valid evidence or observation IDs;
  - Brief Writer receives a compact component-specific packet;
  - approved Resolver GitHub aliases participate in README enrichment;
  - Edge Scout and legacy deepdive remain disabled;
  - focused tests, full Python tests, web tests, production build, schema compatibility checks, and fixed Layer 2 evaluation all pass;
  - before/after telemetry demonstrates lower invalid tool use and bounded context without scoring or brief-quality regression.
