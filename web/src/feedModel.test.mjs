import assert from 'node:assert/strict';
import test from 'node:test';
import {
  feedEmptyState,
  feedRows,
  feedRunSummary,
  normalizeFeedPayload,
  scoreTone,
} from './dashboardModel.js';

const payload = {
  feed_run_id: 'l2-run',
  decision_run_id: 'decision-run',
  generated_at: '2026-05-31T12:00:00Z',
  model_profile: { scout: 'kimi-k2.5', scoring: 'kimi-k2.5', deepdive: 'kimi-k2.6' },
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
      context: {
        members: [
          {
            evidence_bullets: [
              {
                label: 'GH +321 stars / 24h',
                display_label: 'GitHub: +321 stars in 24h',
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
  assert.equal(normalized.today_focus[0].evidence_bullets[0].display_label, 'GitHub: +321 stars in 24h');
  assert.equal(normalized.today_focus[0].source_links[0].channel_label, 'GitHub Trending');
});

test('feedRows merges today focus and scored list with section markers', () => {
  const rows = feedRows(normalizeFeedPayload(payload));

  assert.deepEqual(rows.map((row) => [row.group_id, row.section]), [['group:repo', 'today_focus']]);
});

test('feedRunSummary formats model profile without secrets', () => {
  const summary = feedRunSummary(normalizeFeedPayload(payload));

  assert.equal(summary.models, 'scout kimi-k2.5 · scoring kimi-k2.5 · deepdive kimi-k2.6');
});

test('scoreTone maps numeric score to stable UI tone', () => {
  assert.equal(scoreTone(90), 'hot');
  assert.equal(scoreTone(75), 'warm');
  assert.equal(scoreTone(55), 'steady');
  assert.equal(scoreTone(30), 'quiet');
});

test('feedEmptyState distinguishes missing feed from empty scored run', () => {
  assert.equal(feedEmptyState({ feed_run_id: '', today_focus: [], scored_list: [] }), 'missing');
  assert.equal(feedEmptyState({ feed_run_id: 'l2-run', today_focus: [], scored_list: [] }), 'empty');
});
