import assert from 'node:assert/strict';
import test from 'node:test';
import {
  feedEmptyState,
  feedCardDescription,
  feedRows,
  feedRunSummary,
  normalizeFeedPayload,
  scoreBarStyle,
  scoreTone,
} from './dashboardModel.js';

const payload = {
  feed_run_id: 'l2-run',
  decision_run_id: 'decision-run',
  generated_at: '2026-05-31T12:00:00Z',
  model_profile: { scout: 'kimi-k2.5', scoring: 'kimi-k2.5', brief: 'kimi-k2.5' },
  run_status: 'ok_with_errors',
  telemetry: { scored: 3, briefs: 1, error_total: 2 },
  today_focus: [
    {
      group_id: 'group:repo',
      canonical_name: 'owner/repo',
      canonical_key: 'github:owner/repo',
      canonical_link: 'https://github.com/owner/repo',
      level: 'potential',
      l2_score: 88,
      primary_reason: 'Workflow Shift',
      topic_tags: ['agent workflow'],
      rationale_short: 'Worth reading.',
      source_families: ['github'],
      deepdive_status: 'ok',
      deepdive: { summary: 'Deep summary' },
      deepdive_brief: {
        category: { primary: '开发工具', tags: ['agent', 'repo'] },
        headline: 'owner/repo 值得今天重点看',
        core_highlights: ['把分散开发流程压到一个工具里。'],
        use_cases: ['开发者评估新的 agent workflow。'],
        caveat: '还需要验证真实使用留存。',
      },
      context: {
        members: [
          {
            evidence_bullets: [
              {
                label: 'GH +321 stars / 24h',
                display_label: 'GitHub 24 小时新增：321 stars',
                origin_type: 'deterministic_rule',
              },
            ],
            source_links: [
              { item_id: 1, channel: 'github_trending', channel_label: 'GitHub Trending', window: '24h' },
            ],
            context_preview: 'Repo description',
          },
        ],
      },
    },
  ],
  scored_list: [],
  pending: { edge_watch_scout: 2, deepdive: 1 },
};

test('normalizeFeedPayload keeps run summary and item evidence', () => {
  const normalized = normalizeFeedPayload(payload);

  assert.equal(normalized.feed_run_id, 'l2-run');
  assert.equal(normalized.today_focus[0].title, 'owner/repo');
  assert.equal(normalized.today_focus[0].evidence_bullets[0].display_label, 'GitHub 24 小时新增：321 stars');
  assert.equal(normalized.today_focus[0].source_links[0].channel_label, 'GitHub Trending');
  assert.equal(normalized.today_focus[0].deepdive_brief.category.primary, '开发工具');
  assert.equal(normalized.today_focus[0].deepdive_brief.core_highlights[0], '把分散开发流程压到一个工具里。');
});

test('normalizeFeedPayload preserves run status and telemetry', () => {
  const normalized = normalizeFeedPayload({
    feed_run_id: 'l2-run',
    run_status: 'ok_with_errors',
    telemetry: { error_counts: { scoring: 1 } },
    stage_events: [{ stage: 'scoring', status: 'scoring_error' }],
    today_focus: [],
    scored_list: [],
  });

  assert.equal(normalized.run_status, 'ok_with_errors');
  assert.equal(normalized.telemetry.error_counts.scoring, 1);
  assert.equal(normalized.stage_events[0].status, 'scoring_error');
});

test('feedRows merges today focus and scored list with section markers', () => {
  const rows = feedRows(normalizeFeedPayload(payload));

  assert.deepEqual(rows.map((row) => [row.group_id, row.section]), [['group:repo', 'today_focus']]);
});

test('feedRunSummary formats model profile without secrets', () => {
  const summary = feedRunSummary(normalizeFeedPayload(payload));

  assert.equal(summary.models, 'scout kimi-k2.5 · scoring kimi-k2.5 · brief kimi-k2.5');
  assert.equal(summary.health, 'ok_with_errors · scored 3 · briefs 1 · errors 2');
});

test('scoreTone maps numeric score to stable UI tone', () => {
  assert.equal(scoreTone(90), 'hot');
  assert.equal(scoreTone(75), 'warm');
  assert.equal(scoreTone(55), 'steady');
  assert.equal(scoreTone(30), 'quiet');
});

test('scoreBarStyle clamps scores for compact feed cards', () => {
  assert.equal(scoreBarStyle(88)['--score-pct'], '88%');
  assert.equal(scoreBarStyle(88).label, '88');
  assert.match(scoreBarStyle(88)['--score-gradient'], /linear-gradient/);
  assert.equal(scoreBarStyle(140)['--score-pct'], '100%');
  assert.equal(scoreBarStyle(140).label, '100');
  assert.equal(scoreBarStyle(-4)['--score-pct'], '0%');
  assert.equal(scoreBarStyle(-4).label, '0');
});

test('feedCardDescription shortens LLM rationale but preserves original context fallback', () => {
  const longRationale = '这是一个很长的 LLM rationale，用来解释为什么这个项目值得看，因为它改变了用户完成任务的交互方式，同时也有明确的技术实现和产品使用场景。';
  const longContext = 'Original source description should remain intact because it may be the only source-authored description shown in the feed list.';

  const shortened = feedCardDescription(
    { rationale_short: longRationale, context_preview: longContext },
    { maxChars: 34 },
  );
  assert.ok(shortened.endsWith('…'));
  assert.ok(shortened.length <= 35);
  assert.equal(
    feedCardDescription({ rationale_short: '', context_preview: longContext }, { maxChars: 34 }),
    longContext,
  );
});

test('feedEmptyState distinguishes missing feed from empty scored run', () => {
  assert.equal(feedEmptyState({ feed_run_id: '', today_focus: [], scored_list: [] }), 'missing');
  assert.equal(feedEmptyState({ feed_run_id: 'l2-run', today_focus: [], scored_list: [] }), 'empty');
});
