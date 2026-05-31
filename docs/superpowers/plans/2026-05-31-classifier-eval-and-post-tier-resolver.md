# Classifier Eval And Post-Tier Resolver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tighten X/HN source classifiers with realistic evals, keep news/topic noise out of the candidate pool, and add bounded post-tier link enrichment for accepted X/HN candidates before any larger run.

**Architecture:** X stays a two-stage classifier, but Stage 1 is tweet-level product-signal triage and Stage 2 is candidate-cluster tier judgment. HN first classifies whether an item is product/repo/tool/package eligible; only eligible items may contribute candidate evidence. A shared post-tier resolver enriches accepted X/HN outputs with official/repo/package links using existing internal rows first and bounded external lookup later.

**Tech Stack:** Python 3 stdlib, SQLite, `unittest`, existing DeepSeek OpenAI-compatible provider, existing `llm_cache`, fake providers for deterministic evals. No UI changes in this plan.

---

## Source Documents Read

- `/Users/fuyuming/Documents/Hero radar/docs/decision-layer-v1.md`, especially §4.4 X/social signal flow and §4.5 source rules.
- `/Users/fuyuming/Documents/Hero radar/docs/superpowers/plans/2026-05-31-pre-layer2-decision-pipeline.md`, for pre-Layer2 boundaries and table/API contracts.
- `/Users/fuyuming/Documents/Hero radar/docs/superpowers/plans/2026-05-31-layer1-dynamic-ui-x-hn-npm.md`, for the already implemented LLM/cache/HN/X/npm slice.

## Behavioral Decisions From Discussion

- All configured X seed accounts are credible. Engagement is display/context, not a tier driver.
- X Stage 0 is only deterministic hint extraction.
- X Stage 1 asks whether a tweet mentions a concrete product/project/tool/package and expresses recommendation/use/interest/strong sentiment. Fuzzy product names are allowed.
- X Stage 2 judges a grouped product signal. Resolver/web research runs after Stage 2, only for accepted `watch`, `potential`, or `high` outputs.
- X `high` is rare: it requires clear product binding plus a larger independent credible-author burst or unusually strong adoption/recommendation evidence.
- HN `news_article`, `topic_discussion`, `research_paper`, and `unknown` do not enter candidate pool unless the classifier extracts a concrete product/repo/tool/package object.
- HN heat can raise a product/repo/tool/package item, but heat alone cannot promote non-product news/topic content.
- Resolver enrichment is not a promotion signal by itself. It exists to make accepted candidates clickable and to merge duplicates by repo/domain/package.

## File Map

- Modify `pipeline/decision/x_classifier.py`: deterministic hints, Stage 1 schema/prompt validation, Stage 2 gate and acceptance rules.
- Modify `pipeline/decision/hn_classifier.py`: eligibility helpers and prompt wording for product/repo/tool/package-only candidate evidence.
- Modify `pipeline/decision/rules.py`: require HN classifier eligibility when classifier evidence is present; keep news/topic out of candidates.
- Modify `pipeline/decision/llm_evals.py`: add realistic X/HN cases from current DB patterns.
- Modify `pipeline/decision/run_llm_evals.py`: evaluate X Stage 1 and Stage 2 separately.
- Create `pipeline/decision/resolver.py`: shared post-tier resolver/enrichment with internal lookup and bounded injectable search client.
- Modify `pipeline/decision/run_decision.py`: run resolver after HN/X classifier evidence, before final rules consume classifier evidence.
- Add/modify tests:
  - `tests/test_x_classifier.py`
  - `tests/test_hn_classifier.py`
  - `tests/test_rules_engine.py`
  - `tests/test_llm_evals.py`
  - `tests/test_run_llm_evals.py`
  - `tests/test_resolver.py`
  - `tests/test_decision_runner.py`

## Task 1: X Classifier Eval And Gate Tightening

**Files:**
- Modify: `pipeline/decision/x_classifier.py`
- Modify: `pipeline/decision/llm_evals.py`
- Modify: `pipeline/decision/run_llm_evals.py`
- Test: `tests/test_x_classifier.py`
- Test: `tests/test_llm_evals.py`
- Test: `tests/test_run_llm_evals.py`

- [ ] **Step 1: Write failing tests**

Add tests that require:

```python
def test_stage0_extracts_mentions_hashtags_projects_and_expanded_links(self):
    tweets = candidate_tweets(conn, now="2026-05-31T04:00:00Z", limit=5)
    assert tweets[0]["stage0_hints"]["hashtags"] == ["mcp"]
    assert tweets[0]["stage0_hints"]["mentions"] == ["owner"]
    assert tweets[0]["stage0_hints"]["mentioned_projects"] == ["Clawdbot"]
    assert {"entity_key": "github:owner/repo", "entity_confidence": "linked"} in tweets[0]["deterministic_hints"]

def test_stage1_allows_product_signal_without_entity_key(self):
    output = validate_x_stage1_output({
        "triage": [{
            "tweet_id": "t1",
            "about_concrete_project": True,
            "closer_look": True,
            "product_names": ["Clawdbot"],
            "product_links": [],
            "project_refs": [],
            "expression_strength": "recommendation",
            "evidence_quote": "Clawdbot looks useful",
            "reason": "Concrete product name with recommendation."
        }]
    })
    assert output["triage"][0]["product_names"] == ["Clawdbot"]

def test_stage2_gate_includes_single_credible_stage1_watch_candidate(self):
    # one credible, one clear Stage1 product mention can reach Stage2 for watch,
    # but cannot become potential without stronger evidence.
    assert candidate_entity_mentions(conn, run_id="decision_run", limit=5)

def test_stage2_high_requires_three_credible_or_verified_context(self):
    output = validate_x_stage2_output({... "x_tier": "high", "cited_tweet_ids": ["t1", "t2"]})
    assert accepted_x_tier(output, aggregate={"credible_authors": 2, "distinct_authors": 2}) == "potential"
```

Run:

```bash
python3 -m unittest tests.test_x_classifier tests.test_llm_evals tests.test_run_llm_evals -v
```

Expected: new tests fail before implementation.

- [ ] **Step 2: Implement minimal X changes**

Implementation constraints:

```text
candidate_tweets returns both deterministic_hints and stage0_hints.
validate_x_stage1_output accepts product_names/product_links with empty project_refs.
run_x_stage1 creates name:<normalized> mentions when there is a closer_look product_name and no project_ref.
candidate_entity_mentions admits credible_authors >= 1 for Stage2 watch consideration.
accepted_x_tier clamps high to potential unless aggregate has >=3 credible authors or verified cross-source context.
accepted_x_tier clamps fuzzy potential/high to watch.
```

- [ ] **Step 3: Verify and commit**

Run:

```bash
python3 -m unittest tests.test_x_classifier tests.test_llm_evals tests.test_run_llm_evals -v
git add pipeline/decision/x_classifier.py pipeline/decision/llm_evals.py pipeline/decision/run_llm_evals.py tests/test_x_classifier.py tests/test_llm_evals.py tests/test_run_llm_evals.py docs/superpowers/plans/2026-05-31-classifier-eval-and-post-tier-resolver.md
git commit -m "fix: tighten x classifier eval gates"
```

## Task 2: HN Eligibility And Noise Consumption

**Files:**
- Modify: `pipeline/decision/hn_classifier.py`
- Modify: `pipeline/decision/rules.py`
- Modify: `pipeline/decision/llm_evals.py`
- Test: `tests/test_hn_classifier.py`
- Test: `tests/test_rules_engine.py`
- Test: `tests/test_llm_evals.py`

- [ ] **Step 1: Write failing tests**

Add tests that require:

```python
def test_hot_hn_news_with_github_url_is_noise_without_productness(self):
    # score >= potential threshold but classifier says news_article
    # result.potential_candidates == []

def test_hot_hn_project_with_domain_can_be_potential(self):
    # Show HN with score >= potential threshold and classifier project/company_product
    # result.potential_candidates[0].level == "potential"

def test_hn_eval_cases_cover_hot_news_vs_show_hn_product(self):
    names = {case["name"] for case in hn_eval_cases()}
    assert "hn_hot_news_with_company_url_noise" in names
    assert "hn_show_hn_product_domain_potential" in names
```

Run:

```bash
python3 -m unittest tests.test_hn_classifier tests.test_rules_engine tests.test_llm_evals -v
```

Expected: new tests fail before implementation.

- [ ] **Step 2: Implement minimal HN changes**

Implementation constraints:

```text
HN classifier keeps writing hn_projectness evidence for all classifications.
Rules suppress HN-only candidate evidence for item ids with classifier noise.
When classifier evidence exists for an HN item, HN score/story rules require project/package/company_product for that item.
Project/package/company_product can use HN heat normally.
```

- [ ] **Step 3: Verify and commit**

Run:

```bash
python3 -m unittest tests.test_hn_classifier tests.test_rules_engine tests.test_llm_evals -v
git add pipeline/decision/hn_classifier.py pipeline/decision/rules.py pipeline/decision/llm_evals.py tests/test_hn_classifier.py tests/test_rules_engine.py tests/test_llm_evals.py
git commit -m "fix: gate hn candidates by projectness"
```

## Task 3: Post-Tier Resolver Enrichment

**Files:**
- Create: `pipeline/decision/resolver.py`
- Modify: `pipeline/decision/run_decision.py`
- Test: `tests/test_resolver.py`
- Test: `tests/test_decision_runner.py`

- [ ] **Step 1: Write failing tests**

Add tests that require:

```python
def test_resolver_uses_internal_rows_before_search_client(self):
    result = resolve_candidate_links(conn, "name:clawdbot", max_searches=1, search_client=FakeSearch())
    assert result["resolved_links"][0]["key"] == "github:owner/clawdbot"
    assert search_client.calls == []

def test_resolver_is_bounded_and_cached_for_unresolved_name(self):
    first = resolve_candidate_links(conn, "name:clawdbot", max_searches=1, search_client=FakeSearch(...))
    second = resolve_candidate_links(conn, "name:clawdbot", max_searches=1, search_client=FakeSearch(...))
    assert first == second
    assert len(search_client.calls) == 1

def test_runner_enriches_accepted_x_candidate_after_stage2(self):
    # Stage2 emits watch/potential for name:clawdbot.
    # Resolver writes alias_link or merge proposal after acceptance only.
```

Run:

```bash
python3 -m unittest tests.test_resolver tests.test_decision_runner -v
```

Expected: new tests fail before implementation.

- [ ] **Step 2: Implement minimal resolver**

Implementation constraints:

```text
Resolver input is accepted classifier output, not raw tweets/items.
Internal lookup checks alias_links, entities, latest items URL/name/metadata repository first.
External lookup is injectable and bounded by max_searches.
Resolver stores cache rows in api_cache with source='resolver' and window='classifier_enrichment'.
Resolver may write deterministic alias_links for github/domain/npm keys only when confidence >= 0.8.
Resolver may write entity_merge_proposals for lower-confidence links.
```

- [ ] **Step 3: Verify and commit**

Run:

```bash
python3 -m unittest tests.test_resolver tests.test_decision_runner -v
git add pipeline/decision/resolver.py pipeline/decision/run_decision.py tests/test_resolver.py tests/test_decision_runner.py
git commit -m "feat: add post-tier candidate resolver"
```

## Task 4: Bounded Smoke And Full Local Verification

**Files:**
- Modify only if tests reveal a bug.

- [ ] **Step 1: Run deterministic suite**

```bash
python3 -m unittest discover -s tests -v
```

- [ ] **Step 2: Run bounded fake/full local decision smoke**

```bash
python3 -m pipeline.decision.run_decision \
  --db data/hero_radar.sqlite \
  --run-id decision_classifier_smoke_20260531 \
  --export-json data/exports/candidates_classifier_smoke_20260531.json \
  --classify-hn-limit 0 \
  --classify-x-limit 0
```

- [ ] **Step 3: Run bounded DeepSeek eval smoke only if configured**

```bash
python3 -m pipeline.decision.run_llm_evals --kind all --limit 2
```

Output handling:

```text
Do not print secrets or prompt payloads.
Report only total/pass/fail and failing case names.
If DeepSeek failures look like prompt/criteria failures, stop and discuss before a larger run.
```

- [ ] **Step 4: Push after successful commits**

```bash
git status --short --branch
git push origin main
```

## Self-Review

- Spec coverage: covers X Stage0/Stage1/Stage2, HN productness gating, realistic evals, post-tier resolver, bounded smoke. Does not implement Layer2 feed selection, chatbot, cron, rule editor, or full web research agent.
- Placeholder scan: no TBD or future-only behavior is required for acceptance; external web search is injectable and bounded.
- Type consistency: resolver uses typed keys already present in entity resolution: `github:owner/repo`, `domain:example.com`, `npm:package`, and `name:product`.
