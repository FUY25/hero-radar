# Layer2 Scout V2 Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild Layer 2 Edge Watch Scout as a qualitative binary gate that admits only concrete products with at least one strong novelty axis.

**Architecture:** Add a Scout-specific context view between presentation grouping and Kimi Scout so the model sees qualitative source context instead of raw metrics. Update Scout to use prompt-level micro-batches and deterministic include/priority rules. Rename the scoring adoption axis to `product_market_fit` while keeping legacy fallback for older responses.

**Tech Stack:** Python 3.11, SQLite, `unittest`/`pytest`, existing Kimi provider JSON interface.

---

## File Structure

- Create `pipeline/decision/layer2_scout_context.py`
  - Converts `CandidateGroup.context` into compact qualitative Scout input.
  - Extracts README excerpts, source descriptions, classifier summaries, and structured source context.
  - Excludes raw metrics, raw `evidence_rows`, known gaps, and deterministic promotion evidence.
- Modify `pipeline/decision/layer2_scout.py`
  - Uses `ScoutContextView`.
  - Sends prompt-level micro-batches with top-level `{ "candidates": [...] }`.
  - Validates top-level `{ "decisions": [...] }`.
  - Computes deterministic `include_in_l2_scoring` and `scout_score`.
- Modify `pipeline/decision/layer2_scoring.py`
  - Replaces required `adoption_path` axis with `product_market_fit`.
  - Accepts legacy `adoption_path` as fallback.
- Modify `pipeline/decision/run_layer2_evals.py`
  - Adds deterministic Scout v2 fixture cases for pass/fail calibration.
- Modify tests:
  - `tests/test_layer2_scout_context.py`
  - `tests/test_layer2_scout.py`
  - `tests/test_layer2_scoring.py`
  - `tests/test_layer2_evals.py`

## Scout V2 Rules

Scout is not a ranking model. It is a binary gate.

The model returns categorical judgments:

```json
{
  "group_id": "group:clicky",
  "is_concrete_product": true,
  "object_type": "product",
  "workflow_shift": "strong",
  "technical_substance": "weak",
  "product_market_fit": "medium",
  "confidence": 0.82,
  "reason": "Screen-aware Mac assistant changes interaction around cursor and voice."
}
```

The application computes:

```text
include_in_l2_scoring =
  is_concrete_product
  AND one or more of:
    workflow_shift == strong
    technical_substance == strong
    product_market_fit == strong
```

Important: `medium` never admits a candidate, even if two or three axes are `medium`.

Filtered default object types:

```text
model, article, tutorial, discussion, news, unknown
```

If an article/source page is about a concrete linked repo/product, the model should classify the object as `repo`, `product`, `package`, or `workflow`, not `article`.

## Eval Anchors

Positive anchors that should pass:

- HeyClicky: Mac-native screen/cursor/voice assistant with background agents.
- OpenClaw: local/multi-channel personal AI assistant with system access, browser control, memory, skills, and plugins.
- Hermes Agent: self-improving agent with persistent memory, skill creation, cross-session recall, scheduled automations, and multi-platform delivery.

Negative anchors that should fail:

- Generic AI chatbot landing page with no distinct workflow, technical mechanism, or product-market wedge.
- News article about an AI company/model with no concrete product/repo binding.
- Tutorial/blog/resource list without a product artifact.
- Model release or benchmark page without a product/workflow wrapper.
- Project with only `medium` on all three novelty axes.

## Task 1: Scout Context View

**Files:**
- Create: `pipeline/decision/layer2_scout_context.py`
- Test: `tests/test_layer2_scout_context.py`

- [ ] **Step 1: Write failing tests**

Add tests proving the Scout context:

```python
def test_scout_context_uses_qualitative_fields_and_excludes_metrics(self):
    group = CandidateGroup(
        group_id="group:clicky",
        canonical_entity_id="entity:clicky",
        canonical_name="Clicky",
        canonical_key="domain:heyclicky.com",
        canonical_link="https://www.heyclicky.com/",
        member_entity_ids=["entity:clicky"],
        level="edge_watch",
        source_families=["hn", "x_social"],
        context={
            "evidence_rows": [
                {
                    "metric_name": "hn_max_points_7d",
                    "metric_value": "144",
                    "note": "Clicky: AI buddy that lives on your Mac.",
                    "source": "hn_firebase",
                }
            ],
            "members": [
                {
                    "entity_id": "entity:clicky",
                    "canonical_link": "https://www.heyclicky.com/",
                    "context_preview": "An AI buddy that lives on your Mac and sees what you see.",
                    "readme_excerpt_available": False,
                    "source_links": [
                        {
                            "source": "hn_firebase",
                            "channel": "hn_top",
                            "name": "Clicky",
                            "external_url": "https://www.heyclicky.com/",
                            "author": "farza",
                        }
                    ],
                }
            ],
        },
    )

    view = scout_context_for_group(group)

    assert view["group_id"] == "group:clicky"
    assert view["candidate"]["name"] == "Clicky"
    assert view["candidate"]["canonical_link"] == "https://www.heyclicky.com/"
    assert "AI buddy" in view["candidate"]["project_context"][0]
    assert view["source_context"][0]["title"] == "Clicky"
    assert "evidence_rows" not in view
    assert "hn_max_points_7d" not in json.dumps(view)
    assert "144" not in json.dumps(view)
```

Add a second test proving README excerpts can be longer than the old 1000-char preview when present in group context:

```python
def test_scout_context_keeps_readme_excerpt_as_project_context(self):
    readme = "Agent runtime " + ("memory skills browser control " * 80)
    group = CandidateGroup(... context={"members": [{"context_preview": readme, "readme_excerpt_available": True, "source_links": []}]})
    view = scout_context_for_group(group)
    assert view["candidate"]["has_readme"] is True
    assert "memory skills browser control" in view["candidate"]["project_context"][0]
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m pytest tests/test_layer2_scout_context.py -q
```

Expected: FAIL because `pipeline.decision.layer2_scout_context` does not exist.

- [ ] **Step 3: Implement minimal context builder**

Create `scout_context_for_group(group: CandidateGroup) -> dict[str, Any]`.

Implementation requirements:

- Include `group_id`.
- Include `candidate.name`, `candidate.canonical_key`, `candidate.canonical_link`, `candidate.level`, `candidate.has_readme`, and `candidate.project_context`.
- Include `source_context` entries from member `source_links`.
- Normalize source link keys to `source`, `channel`, `title`, `url`, `author`, and `text` when available.
- Include classifier/source summaries from `evidence_rows.note` only as text snippets, not metric fields.
- Do not include raw `evidence_rows`, `metric_name`, `metric_value`, known gaps, rule ids, or raw provenance.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
python3 -m pytest tests/test_layer2_scout_context.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/layer2_scout_context.py tests/test_layer2_scout_context.py
git commit -m "Add Layer 2 Scout context view"
```

## Task 2: Scout V2 Binary Gate And Micro-Batch

**Files:**
- Modify: `pipeline/decision/layer2_scout.py`
- Test: `tests/test_layer2_scout.py`

- [ ] **Step 1: Write failing tests**

Add tests for:

1. Micro-batch sends one provider call for three groups.
2. At least one `strong` axis includes a concrete product.
3. Three `medium` axes do not include.
4. News/article/model object types do not include.
5. Legacy single-object provider response is rejected for v2.

Representative test:

```python
provider = FakeLLMProvider([
    {
        "decisions": [
            {
                "group_id": "group:clicky",
                "is_concrete_product": True,
                "object_type": "product",
                "workflow_shift": "strong",
                "technical_substance": "weak",
                "product_market_fit": "medium",
                "confidence": 0.8,
                "reason": "Cursor-adjacent Mac assistant is a new interaction model.",
            },
            {
                "group_id": "group:medium",
                "is_concrete_product": True,
                "object_type": "repo",
                "workflow_shift": "medium",
                "technical_substance": "medium",
                "product_market_fit": "medium",
                "confidence": 0.8,
                "reason": "Interesting but no strong novelty axis.",
            },
            {
                "group_id": "group:news",
                "is_concrete_product": False,
                "object_type": "news",
                "workflow_shift": "strong",
                "technical_substance": "strong",
                "product_market_fit": "strong",
                "confidence": 0.8,
                "reason": "News article, not a product.",
            },
        ]
    }
])
included = scout_edge_watch_groups(conn, feed_run_id="l2-run", groups=groups, provider=provider, batch_size=3)
assert [group.group_id for group in included] == ["group:clicky"]
assert len(provider.calls) == 1
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m pytest tests/test_layer2_scout.py -q
```

Expected: FAIL because Scout still expects one response per group and legacy schema.

- [ ] **Step 3: Implement Scout V2**

Changes:

- Set prompt version to `layer2-edge-scout-v2`.
- Rewrite system prompt to describe the three-gate rule.
- Add `batch_size: int = 3` parameter.
- Build batches using `scout_context_for_group(group)`.
- Provider input payload:

```json
{
  "candidates": [ ... ],
  "decision_rule": "include only concrete products with at least one strong novelty axis"
}
```

- Validate response top-level `decisions`.
- Persist one `l2_scout_results` row per group using deterministic include and score.
- Compute deterministic score:

```text
0.0 for filtered non-concrete or blocked object type
0.35 for concrete with no strong axis
0.75 for one strong axis
0.85 for two strong axes
0.95 for three strong axes
```

- Store `reason`.
- Store `risk` as a compact debug string such as `object_type=repo;strong_axes=workflow_shift`.
- Store empty `needed_context_json` for compatibility with existing schema.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
python3 -m pytest tests/test_layer2_scout.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/layer2_scout.py tests/test_layer2_scout.py
git commit -m "Calibrate Layer 2 Scout v2 gate"
```

## Task 3: Product Market Fit Scoring Axis

**Files:**
- Modify: `pipeline/decision/layer2_scoring.py`
- Test: `tests/test_layer2_scoring.py`

- [ ] **Step 1: Write failing tests**

Update scoring tests so canonical responses use `product_market_fit`, and add one legacy fallback test:

```python
score = aggregate_l2_score({
    "momentum": 80,
    "workflow_shift": 90,
    "technical_substance": 70,
    "product_market_fit": 60,
    "confidence": 75,
    "derivative_news_penalty": 10,
})
assert score == 66.75

legacy_score = aggregate_l2_score({
    "momentum": 80,
    "workflow_shift": 90,
    "technical_substance": 70,
    "adoption_path": 60,
    "confidence": 75,
    "derivative_news_penalty": 10,
})
assert legacy_score == 66.75
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m pytest tests/test_layer2_scoring.py -q
```

Expected: FAIL because `product_market_fit` is not accepted.

- [ ] **Step 3: Implement scoring axis rename**

Changes:

- Update `SCORING_SYSTEM_PROMPT` to list `product_market_fit`.
- Update `aggregate_l2_score()` to read `product_market_fit`, falling back to `adoption_path`.
- Update `_validate_response()` to persist `product_market_fit` in `axes_json`.
- Keep repair behavior unchanged.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
python3 -m pytest tests/test_layer2_scoring.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/layer2_scoring.py tests/test_layer2_scoring.py
git commit -m "Rename Layer 2 scoring adoption axis"
```

## Task 4: Scout Eval Fixtures

**Files:**
- Modify: `pipeline/decision/run_layer2_evals.py`
- Modify: `tests/test_layer2_evals.py`

- [ ] **Step 1: Write failing tests**

Add tests proving default evals include v2 Scout cases:

```python
def test_default_eval_cases_cover_scout_v2_strong_gate(self):
    from pipeline.decision.run_layer2_evals import default_scout_v2_eval_cases, evaluate_scout_v2_cases

    result = evaluate_scout_v2_cases(default_scout_v2_eval_cases())

    assert result["ok"]
    assert result["metrics"]["positive_cases"] >= 3
    assert result["metrics"]["medium_only_failures"] >= 1
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m pytest tests/test_layer2_evals.py -q
```

Expected: FAIL because eval helpers do not exist.

- [ ] **Step 3: Implement deterministic eval helpers**

Add:

- `default_scout_v2_eval_cases()`
- `evaluate_scout_v2_cases(cases)`

Use deterministic case objects with expected include decisions:

- HeyClicky: pass via `workflow_shift=strong`.
- OpenClaw: pass via `workflow_shift=strong` and `product_market_fit=strong`.
- Hermes: pass via `technical_substance=strong` and `product_market_fit=strong`.
- Generic chatbot: fail.
- AI company/model news: fail.
- Tutorial/resource list: fail.
- Medium-only project: fail.

Implementation should call the same deterministic include helper used by Scout v2, not duplicate the rule.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
python3 -m pytest tests/test_layer2_evals.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/run_layer2_evals.py tests/test_layer2_evals.py
git commit -m "Add Layer 2 Scout v2 eval fixtures"
```

## Task 5: Integrated Verification

**Files:**
- No production edits unless verification exposes a regression.

- [ ] **Step 1: Run targeted tests**

```bash
python3 -m pytest tests/test_layer2_scout_context.py tests/test_layer2_scout.py tests/test_layer2_scoring.py tests/test_layer2_evals.py -q
```

Expected: PASS.

- [ ] **Step 2: Run broader Layer2 tests**

```bash
python3 -m pytest tests/test_layer2_context.py tests/test_layer2_grouping.py tests/test_run_layer2_feed.py tests/test_feed_api.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full tests**

```bash
python3 -m pytest tests -q
```

Expected: PASS.

- [ ] **Step 4: Run deterministic eval command**

```bash
python3 -m pipeline.decision.run_layer2_evals
```

Expected: JSON result with `"ok": true`.

- [ ] **Step 5: Commit verification-only changes if any**

If verification required code/test fixes, commit them with:

```bash
git add <changed-files>
git commit -m "Verify Layer 2 Scout v2 calibration"
```

If no changes were needed, do not create an empty commit.

## Self-Review

- Spec coverage: The plan covers qualitative context, no metrics/known gaps in Scout input, binary Scout decision, one-strong-axis rule, micro-batch A+B, product-market-fit naming, and eval anchors.
- Placeholder scan: No placeholders remain; each task has files, tests, commands, expected failures, implementation rules, and commit instructions.
- Type consistency: `product_market_fit`, `workflow_shift`, `technical_substance`, `is_concrete_product`, `object_type`, and `decisions` are used consistently across tasks.
