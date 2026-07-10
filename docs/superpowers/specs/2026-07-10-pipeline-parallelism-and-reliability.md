# Hero Radar Pipeline Parallelism and Reliability

## Problem Statement

Hero Radar's daily pipeline is dominated by external I/O, but most source collection, Decision enrichment, and post-scoring work runs serially. A recent complete run spent roughly 7.2 minutes collecting sources, 4.3 minutes in the successful Decision retry, and 40.8 minutes in Layer 2. The existing implementation already proves that bounded candidate-level parallelism works for HN classification and Layer 2 scoring, but the same pattern is not consistently applied elsewhere.

This creates four user-visible problems:

- A daily run takes longer than necessary even when independent sources, candidates, packages, repositories, and briefs could be processed concurrently.
- External APIs are called through ad hoc sleeps rather than shared, testable rate limits, so increasing concurrency risks bursts, provider throttling, or unstable retries.
- Several modules combine remote I/O and SQLite writes in the same loop, making naive concurrency unsafe and obscuring the interface between collection and persistence.
- One resolver candidate can currently fail the entire Decision run, even though other candidates may have completed successfully and Layer 2 already has candidate-level failure isolation.

The user wants the pre-Layer-2 pipeline parallelized according to its real dependency graph, with bounded concurrency, provider-aware rate limiting, deterministic persistence, and failure isolation. They also want independent Chinese brief generation and independent primitive tool requests within a single scorer turn to run concurrently. Edge Watch Scout must remain disabled. The Layer 2 agent's deeper context-management and prompt architecture will be discussed after this implementation, not redesigned as part of it.

## Solution

Refactor the daily pipeline around explicit collection, execution, and persistence seams.

Source adapters will perform independent remote collection concurrently under bounded worker pools and shared provider/host rate limiters. They will return structured source results. A deterministic persistence step will write those results to SQLite in adapter order, preserving current ranking, export, logging, and error semantics without sharing one SQLite connection across collector threads.

Within source adapters, independent query/resource/feed work will use bounded ordered parallel maps. Sequential pagination for one query will remain sequential where later pages depend on prior page state, while independent queries, windows, languages, lists, resources, and enrichments may overlap. Provider-specific rate policies will cap concurrency and enforce minimum request spacing.

The Decision pipeline will execute the post-pass-1 work as a dependency-aware graph. GitHub backfill, HN classification, the X stage-1/stage-2 chain, and npm backfill may run concurrently. The classifier resolver will wait for HN and X outputs but may overlap with unfinished GitHub or npm work. Final evaluation will wait for all branches. README enrichment will remain after final candidate persistence but will fetch candidates concurrently. Branches and candidate workers will use isolated database connections or return results to a single persistence owner; a SQLite connection will never be used from a thread that did not create it.

The link resolver will isolate search, research, normalization, and persistence failures per candidate. A failed candidate will be recorded and included in summary telemetry, while other candidates continue. A mixed-success Decision run will complete with its usable candidates instead of aborting because of one resolver failure.

Layer 2 candidate scoring will retain its existing concurrency and routing behavior. Selected Chinese briefs will be generated with bounded candidate-level concurrency, defaulting to four workers. Within one Scoring Investigator turn, admitted primitive tool requests will execute concurrently where possible. Budget admission will remain deterministic and sequential, execution results will be reassembled in the model-requested order, and unavailable or over-budget requests will retain their current trace statuses. Tool execution will use thread-safe, per-call database ownership for cache access.

All concurrency will be observable and configurable. Defaults will be conservative. Existing output order, evidence semantics, scoring formulas, prompt text, and feed routing will remain unchanged.

## User Stories

1. As a Hero Radar operator, I want independent source adapters to collect concurrently, so that the daily source stage finishes closer to the duration of its slowest sources instead of the sum of every source duration.
2. As a Hero Radar operator, I want source concurrency to be bounded, so that one daily run cannot create an uncontrolled number of network requests.
3. As a Hero Radar operator, I want GitHub-backed collectors to share a GitHub rate policy, so that GitHub Trending, Search, Movers, README, and file requests do not overwhelm the same upstream provider.
4. As a Hero Radar operator, I want rate limits to be configurable, so that authenticated and unauthenticated environments can use different safe request rates.
5. As a maintainer, I want rate limiting to use an injectable clock and sleeper, so that timing behavior can be verified without slow tests.
6. As a maintainer, I want source collectors to return structured results instead of writing through a shared connection, so that network concurrency cannot corrupt or lock SQLite persistence.
7. As a maintainer, I want source results persisted in the configured adapter order, so that logs, error maps, snapshot ordering, and exports remain deterministic.
8. As a Hero Radar operator, I want one failed source adapter to be recorded without cancelling independent adapters, so that a partial upstream outage does not erase the rest of the day's data.
9. As a Hero Radar operator, I want independent GitHub Trending period/language requests to overlap under a shared limit, so that broad trend coverage does not require serial waits.
10. As a Hero Radar operator, I want independent GitHub Search queries to overlap while each query's pagination stays ordered, so that search collection is faster without changing rank semantics.
11. As a Hero Radar operator, I want Trending Repos and RepoFOMO collection to overlap, so that independent mover providers do not block one another.
12. As a Hero Radar operator, I want independent HN Algolia windows and queries to overlap, so that multiple time windows do not add serial latency.
13. As a Hero Radar operator, I want all configured HN Firebase lists to share one bounded item-fetch pool, so that top, new, and best stories are fetched efficiently without multiplying worker limits.
14. As a Hero Radar operator, I want Hugging Face resource collection and bounded card enrichment to overlap, so that models, datasets, and spaces do not dominate source-stage latency.
15. As a Hero Radar operator, I want independent npm queries and PyPI feeds/enrichments to overlap under provider limits, so that package discovery is faster and polite to upstream APIs.
16. As a Hero Radar operator, I want the Decision pipeline to run independent post-pass-1 branches concurrently, so that GitHub, HN, X, and npm enrichment latency overlaps.
17. As a maintainer, I want the X stage-1 to stage-2 dependency preserved, so that entity tiering never runs before tweet triage has produced its mentions.
18. As a maintainer, I want the resolver to wait for HN and X classifier outputs, so that it always sees the complete classifier candidate set.
19. As a maintainer, I want final evaluation to wait for every Decision branch, so that no enrichment is silently excluded from final candidates.
20. As a Hero Radar operator, I want HN requests, X batches, X entity tiers, GitHub jobs, npm jobs, resolver candidates, and README fetches to use bounded concurrency, so that each expensive collection unit can overlap safely.
21. As a Hero Radar operator, I want one resolver candidate's 403, timeout, invalid result, or provider error to be recorded without failing the Decision run, so that unrelated candidates still reach Layer 2.
22. As a Hero Radar operator, I want resolver summaries to report attempted, enriched, researched, failed, alias, and proposal counts, so that partial failures are visible rather than silently swallowed.
23. As a maintainer, I want resolver rounds for one candidate to remain sequential, so that each search observation informs the next LLM action.
24. As a maintainer, I want candidate-level resolver results persisted deterministically after concurrent work, so that repeated runs produce stable aliases and proposals.
25. As a Hero Radar operator, I want README enrichment to fetch several repositories concurrently, so that context preparation no longer waits on every repository one at a time.
26. As a Hero Radar operator, I want selected Chinese briefs to be generated concurrently with a conservative default of four workers, so that Today Focus rendering is faster without changing which candidates are selected.
27. As a maintainer, I want brief persistence and feed-item status updates to remain deterministic, so that completion order cannot change brief order or routing.
28. As a maintainer, I want injected fake providers to be usable in deterministic single-worker tests, so that concurrency does not make existing test doubles flaky.
29. As a Layer 2 scorer, I want independent primitive tool requests from one turn to execute concurrently, so that README, file, homepage, evidence, and web observations do not incur fully serial network latency.
30. As a maintainer, I want tool budgets admitted before concurrent execution, so that race conditions cannot exceed total or per-family limits.
31. As a maintainer, I want tool results reassembled in the original request order, so that model context and persisted traces remain stable regardless of completion order.
32. As a maintainer, I want over-budget and unavailable tools to preserve their current trace rows without being submitted to workers, so that behavior remains backward compatible.
33. As a maintainer, I want each concurrent tool call to own a safe database connection for cache reads and writes, so that SQLite's thread-affinity rules are never violated.
34. As a Hero Radar operator, I want local and LLM caches to continue working under concurrency, so that duplicate requests are avoided and retries remain inexpensive.
35. As a Hero Radar operator, I want stage telemetry to include configured concurrency, completed counts, and isolated failures, so that performance and reliability can be audited from run logs.
36. As a maintainer, I want current public function behavior and output schemas preserved, so that the dashboard and existing scripts require no migration.
37. As a maintainer, I want deterministic single-worker fallbacks, so that debugging can reproduce the pre-concurrency execution order.
38. As a maintainer, I want the full existing test suite to pass after the refactor, so that concurrency changes do not alter ranking, routing, scoring, or evidence semantics.
39. As a product owner, I want Edge Watch Scout to remain disabled, so that this performance project does not change candidate admission policy.
40. As a product owner, I want the Layer 2 agent context architecture discussed separately after the pipeline work, so that execution optimization and agent redesign are not mixed into one risky change.

## Implementation Decisions

- Introduce one reusable bounded ordered parallel execution module. Its interface accepts values, a worker, a positive concurrency limit, and optionally a rate limiter. It returns one result per input in input order even when completion order differs.
- Introduce one thread-safe monotonic rate limiter abstraction with configurable maximum in-flight work and minimum start interval. Tests will inject a fake clock/sleeper. Rate limiters will be shared by provider/host family rather than created per request.
- Source collection will have an explicit result type containing adapter name, items, error, timing, and stable adapter index. Collector workers will not use the main source-stage SQLite connection.
- Source-stage persistence will remain owned by the main thread and will process source results in configured adapter order. Ranking and export will run only after all selected source results have been persisted.
- The X tweet import is a database-mutating exception to ordinary source collection. It will be split into an explicit import phase or marked database-serial so that its `x_tweets_store` and cursor writes never overlap the source snapshot writer. The read-only conversion from the local X store to source items may still use the normal collection result interface.
- HN Firebase results will be normalized back to configured list order and rank order before persistence; completion order from the item-fetch pool will not leak into item ordering.
- The default source adapter concurrency will be conservative and configurable. Provider-specific inner concurrency will also be configurable, with single-worker behavior available for debugging and tests.
- Sequential pagination within one query will be preserved. Independent queries, windows, languages, lists, resource types, feeds, and card/package enrichments are parallelization units.
- Existing fixed sleeps will be replaced or mediated by the shared rate limiter where concurrency is introduced. No worker will use a long blocking global sleep while holding database state.
- The Decision pipeline will expose its dependency graph through a small orchestration interface rather than by scattering thread creation through stage implementations.
- Post-pass-1 branches will use isolated SQLite connections configured with a busy timeout. No connection object will cross a thread boundary. Where practical, remote fetching will return values for deterministic main-thread persistence.
- Cache helpers used by concurrent workers will support caller-owned transaction control or a collect-then-persist path; helpers must not force an internal commit that breaks job/candidate atomicity.
- HN and X will use distinct provider instances in normal CLI construction so that concurrent branches do not mutate or serialize through one provider object. Dependency injection will retain a deterministic path for test providers.
- The X stage-1 batch runner and X stage-2 entity runner will gain bounded ordered concurrency while preserving stage ordering and cache behavior.
- GitHub and npm backfill runners will separate remote job execution from database result application. Job results will be applied in job order, and individual failures will retain their current failed-job records.
- GitHub backfill completeness/lower-bound metadata will be returned per job. It will no longer be communicated through mutable state on a GitHub client shared by concurrent workers.
- Resolver candidates will be independent work items. Each candidate's internal search/research rounds remain sequential. Exceptions will be converted into sanitized candidate failure results, persisted as telemetry, counted in the resolver summary, and will not be re-raised as run-fatal errors.
- Resolver result application will remain deterministic and preserve confidence thresholds, alias approval behavior, and proposal behavior.
- A Decision run with both successful work and isolated resolver failures will use `ok_with_errors`. Every consumer that currently selects or resumes only `ok` Decision runs will treat `ok_with_errors` as usable, including daily resume checks, Layer 2 latest-run selection, and dashboard/API latest-run selection. A run with no usable output remains failed.
- README enrichment will fetch uncached repositories concurrently and store successful results without changing the existing cache key or excerpt limits.
- Brief selection and route insertion remain deterministic and serial. Only generation for the already-selected rows becomes concurrent. Generated results are applied in selection order.
- Production brief workers will use independent provider instances and independent SQLite connections. Injected providers default to one worker unless an explicit factory is supplied.
- Primitive tool request handling will have two phases: deterministic admission, followed by concurrent execution. Admission computes total and family budgets in request order before any work is submitted.
- Primitive tool traces will be returned in request order. Execution failures remain trace rows and do not cancel sibling tools or the candidate.
- The production investigator tool registry will provide per-call database ownership for cache-backed tools. Existing tool names, argument contracts, path allowlists, sanitization, and result-size caps remain unchanged.
- Edge Scout remains disabled and receives no enablement, batching, prompt, or routing changes.
- Existing Layer 2 scorer prompts, scoring axes, formula, hard caps, brief prompt, candidate selection, and route thresholds remain unchanged.
- New concurrency settings will have safe defaults and will be surfaced through existing config/CLI plumbing without requiring a database migration.
- Telemetry will record stage concurrency and isolated error counts but will never expose provider secrets.
- The implementation will be delivered as small commits grouped by module seam, followed by a dedicated review pass and one final full-suite verification.

## Testing Decisions

- Good tests will assert observable behavior at the highest available interface: returned summaries, persisted rows, output ordering, maximum observed concurrency, rate-limiter start times, failure counts, and continuation after errors. They will not assert private executor construction or exact thread scheduling.
- Source orchestration tests will inject fake adapters controlled by barriers/events. They will prove that more than one adapter overlaps, active work never exceeds the configured bound, persistence remains in adapter order, and one adapter error does not cancel the rest.
- Ordered parallel execution tests will prove input-order results, bounded activity, exception/result behavior, and the single-worker fallback.
- Rate-limiter tests will use fake monotonic time and sleeper behavior to prove minimum spacing and in-flight caps without real delays.
- Adapter-level tests will cover representative independent units: GitHub query ordering, shared HN Firebase worker caps, Hugging Face resource/card limits, npm query order, and PyPI enrichment order. Existing source parsing fixtures remain the prior art.
- Decision runner tests will use temporary SQLite databases and injected clients/providers. They will prove branch overlap while preserving the HN/X-to-resolver and all-branches-to-final-evaluation dependencies.
- Backfill tests will prove external calls overlap, database results are applied deterministically, caches remain valid, and one failed job does not cancel successful jobs.
- X classifier tests will prove stage-1 batches and stage-2 entities use bounded concurrency while stage 2 never begins before stage 1 completes.
- Resolver tests will add mixed-success cases where one candidate search or research call raises 403/timeout/invalid-output errors. The summary must count the failure, successful candidates must still persist aliases/evidence, and the Decision run must finish.
- README enrichment tests will use delayed fake clients to prove bounded overlap, cache hits avoid worker calls, limits remain exact, and output counts are stable.
- Layer 2 feed tests will prove selected briefs overlap under a provider factory, persist in selected order, and downgrade only the failed brief's feed-item status.
- Scoring Investigator tests will use delayed primitive tools to prove same-turn overlap, deterministic trace order, exact total/family budget enforcement, unavailable-tool behavior, error isolation, and cumulative next-turn context.
- SQLite thread-affinity tests will exercise production-style cache-backed tools with a temporary on-disk database, not an in-memory connection shared across workers.
- Existing tests for source scoring, entity resolution, candidate grouping, Decision summaries, Layer 2 scoring, feed routing, cache reuse, and API payloads are regression prior art and must continue to pass.
- Focused test modules will run after each seam is implemented. Web tests and Python unit tests will run throughout. The complete Python and web test suites will run once after all implementation work and again after review fixes if review finds issues.

## Out of Scope

- Enabling or redesigning Edge Watch Scout.
- Changing which candidates enter Layer 2.
- Redesigning the Layer 2 Scoring Investigator system prompt, rubric, scoring formula, or route thresholds.
- Converting the scorer to native provider tool calls or native multi-message agent sessions.
- Adding persistent agent memory, context summarization, token accounting, or a new tool schema registry.
- Changing the role of the candidate-link resolver or merging it with Layer 2; that architecture will be discussed after this implementation.
- Combining Layer 2 scoring and Chinese brief writing into one LLM call.
- Parallelizing dependent turns within one resolver candidate or one Layer 2 scorer candidate.
- Overlapping source collection, Decision, and Layer 2 for the same run.
- Overlapping different daily runs or removing the daily-run lock.
- Deleting local data, logs, backups, generated PDFs, or temporary artifacts.
- Changing dashboard layout or user-facing feed behavior beyond preserving current outputs.
- Introducing a distributed queue, external worker service, or non-SQLite database.

## Further Notes

- The latest observed complete daily run collected 5,353 source items, produced 194 Layer 2 groups, scored 104 candidates, and generated 8 Chinese briefs. These figures are diagnostic baselines, not hard acceptance thresholds.
- SQLite is the critical concurrency seam. The implementation must prefer isolated connections and deterministic result application over sharing a connection or relying on implicit thread safety.
- External APIs have different rate semantics. A single global concurrency number is insufficient; provider-family policies are required even if they share one generic limiter implementation.
- The current LLM cache makes retries fast and should remain stable. Concurrency must not alter cache keys or cause duplicate writes to corrupt responses.
- After this work is complete, the product owner and implementer will separately discuss: where the candidate-link resolver sits in the pipeline and which later modules consume it; whether Layer 2 scoring and Chinese brief generation should be understood as one agent, two agents, or two LLM modules in one workflow; and the trade-offs between isolated API calls with locally assembled context and a provider-managed complete agent session.
