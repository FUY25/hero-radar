import assert from 'node:assert/strict';
import test from 'node:test';
import {
  feedEmptyState,
  feedCardDescription,
  feedBriefPreview,
  feedFocusLayout,
  feedSignalDescription,
  feedScoredGroups,
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
      major_company: 'Anthropic',
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
  diagnostics: [
    {
      group_id: 'group:error',
      canonical_name: 'bad/repo',
      deepdive_status: 'candidate_error',
      context: { members: [] },
    },
  ],
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
  assert.equal(normalized.today_focus[0].major_company, 'Anthropic');
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
  const rows = feedRows(normalizeFeedPayload({
    ...payload,
    scored_list: [
      {
        group_id: 'group:low',
        canonical_name: 'low/repo',
        l2_score: 34,
        deepdive_status: 'suppress_or_low',
        context: { members: [] },
      },
    ],
  }));

  assert.deepEqual(rows.map((row) => [row.group_id, row.section]), [
    ['group:repo', 'today_focus'],
    ['group:low', 'scored'],
  ]);
  assert.equal(rows[1].deepdive_status, 'suppress_or_low');
});

test('feedScoredGroups separates normal candidates from low signal scored rows', () => {
  const feed = normalizeFeedPayload({
    ...payload,
    telemetry: {
      scored: 3,
      briefs: 1,
      error_total: 0,
      route_counts: {
        score_plus_deepdive: 1,
        score_only: 1,
        suppress_or_low: 1,
      },
    },
    scored_list: [
      {
        group_id: 'group:signal',
        canonical_name: 'signal/repo',
        l2_score: 61,
        deepdive_status: 'score_only',
        context: { members: [] },
      },
      {
        group_id: 'group:low',
        canonical_name: 'low/repo',
        l2_score: 34,
        deepdive_status: 'suppress_or_low',
        context: { members: [] },
      },
    ],
  });
  const groups = feedScoredGroups(feed);
  const summary = feedRunSummary(feed);

  assert.deepEqual(groups.signals.map((row) => row.group_id), ['group:signal']);
  assert.deepEqual(groups.lowSignals.map((row) => row.group_id), ['group:low']);
  assert.equal(groups.totalScoredVisible, 3);
  assert.equal(summary.coverage, '已评分 3 · 今日重点 1 · 候选 1 · 低信号 1');
});

test('normalizeFeedPayload preserves diagnostics rows', () => {
  const normalized = normalizeFeedPayload(payload);

  assert.equal(normalized.diagnostics[0].group_id, 'group:error');
  assert.equal(normalized.diagnostics[0].deepdive_status, 'candidate_error');
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

test('feedSignalDescription prefers Chinese deepdive brief over scorer rationale', () => {
  assert.equal(
    feedSignalDescription({
      rationale_short: 'English scorer rationale should not show in selected brief card.',
      context_preview: 'English source preview.',
      deepdive_brief: {
        headline: '这是中文标题',
        core_highlights: ['这是中文重点摘要。'],
      },
    }),
    '这是中文标题',
  );
  assert.equal(
    feedSignalDescription({
      rationale_short: 'Fallback scorer rationale.',
      context_preview: 'Original preview.',
      deepdive_brief: null,
    }),
    'Fallback scorer rationale.',
  );
});

test('feedFocusLayout promotes first two selected briefs and keeps the rest medium', () => {
  const items = Array.from({ length: 6 }, (_, index) => ({
    group_id: `group:${index + 1}`,
    rank: index + 1,
    title: `Project ${index + 1}`,
  }));

  const layout = feedFocusLayout(items);

  assert.deepEqual(layout.featured.map((item) => item.group_id), ['group:1', 'group:2']);
  assert.deepEqual(layout.medium.map((item) => item.group_id), ['group:3', 'group:4', 'group:5', 'group:6']);
});

test('feedBriefPreview keeps selected cards compact before expansion', () => {
  const preview = feedBriefPreview({
    deepdive_brief: {
      headline: '这是中文 headline',
      core_highlights: ['第一条重点。', '第二条重点。', '第三条重点。'],
      use_cases: ['使用场景 A', '使用场景 B'],
      caveat: '风险说明。',
    },
  });

  assert.equal(preview.headline, '这是中文 headline');
  assert.deepEqual(preview.highlights, ['第一条重点。', '第二条重点。']);
  assert.deepEqual(preview.use_cases, []);
  assert.equal(preview.caveat, '风险说明。');
  assert.equal(preview.hasDetails, true);
});

test('feedEmptyState distinguishes missing feed from empty scored run', () => {
  assert.equal(feedEmptyState({ feed_run_id: '', today_focus: [], scored_list: [] }), 'missing');
  assert.equal(feedEmptyState({ feed_run_id: 'l2-run', today_focus: [], scored_list: [], diagnostics: [] }), 'empty');
  assert.equal(feedEmptyState({ feed_run_id: 'l2-run', today_focus: [], scored_list: [], diagnostics: [{ group_id: 'group:error' }] }), 'empty');
});
