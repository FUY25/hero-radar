import assert from 'node:assert/strict';
import test from 'node:test';
import {
  activeChannelList,
  candidateRowsForFeed,
  columnWidthKey,
  columnWidthStyle,
  dashboardApiUrl,
  detailRowsForItem,
  filterAndSortRows,
  formatProjectList,
  getConfigValue,
  initialDashboardState,
  rowsForChannel,
  setConfigValue,
  settingsPanelDefs,
  sortOptionsForChannel,
  visibleWindowsForChannel,
  workspaceSections,
  xAvatarForHandle,
} from './dashboardModel.js';

const payload = {
  channels: [
    { id: 'github_trending', label: 'GitHub Trending', count: 2 },
    { id: 'hn_search', label: 'HN Search', count: 1 },
  ],
  settings_channels: [
    { id: 'settings_source_health', label: 'Source Health', count: 1 },
  ],
  items: [
    { item_id: 1, channel: 'github_trending', name: 'b/repo', description: 'B', window: '24h', channel_rank: 2, window_rank: 2, native_metric: { value: 10 }, metadata: { period_stars: 10 }, raw: {} },
    { item_id: 2, channel: 'github_trending', name: 'a/repo', description: 'A', window: '7d', channel_rank: 1, window_rank: 1, native_metric: { value: 20 }, metadata: { period_stars: 20 }, raw: {} },
    { item_id: -1, channel: 'settings_source_health', name: 'github', description: '正常', window: 'current', channel_rank: 1, metadata: { status: '正常' }, raw: {} },
  ],
};

test('initialDashboardState starts on first source channel', () => {
  const state = initialDashboardState(payload);
  assert.equal(state.section, 'sources');
  assert.equal(state.activeChannel, 'github_trending');
  assert.equal(state.activeSettings, 'settings_source_health');
});

test('activeChannelList switches between sources and settings', () => {
  assert.deepEqual(activeChannelList(payload, 'sources').map((row) => row.id), ['github_trending', 'hn_search']);
  assert.deepEqual(activeChannelList(payload, 'settings').map((row) => row.id), ['settings_source_health']);
});

test('visibleWindowsForChannel returns stable window order', () => {
  assert.deepEqual(visibleWindowsForChannel(payload.items, 'github_trending'), ['24h', '7d']);
});

test('filterAndSortRows filters by channel and window and supports name sort', () => {
  const rows = filterAndSortRows(payload.items, {
    activeChannel: 'github_trending',
    activeWindow: 'all',
    query: '',
    sort: 'name',
    sortDir: 'asc',
  });
  assert.deepEqual(rows.map((row) => row.name), ['a/repo', 'b/repo']);
});

test('detailRowsForItem exposes metadata and raw fields for detail panel', () => {
  const rows = detailRowsForItem(payload.items[0]);
  assert.deepEqual(rows.map((row) => row.key), ['metadata.period_stars', 'raw']);
});

test('workspaceSections keeps old top-level surfaces and feed candidate tab', () => {
  assert.deepEqual(workspaceSections().map((row) => row.id), ['explore', 'feed', 'sources', 'settings']);
});

test('candidateRowsForFeed merges potential and edge watch rows', () => {
  const candidates = {
    candidates: [{ entity_id: 'entity:1', canonical_entity: 'Repo', level: 'potential', fired_families: ['github'] }],
    edge_watch: [{ entity_id: 'entity:2', canonical_entity: 'Topic', reasons: ['hn'], status: 'open' }],
  };
  assert.deepEqual(candidateRowsForFeed(candidates).map((row) => row.level), ['potential', 'edge_watch']);
});

test('dashboardApiUrl defaults to same-origin api and respects explicit base', () => {
  assert.equal(dashboardApiUrl('/api/dashboard-data', ''), '/api/dashboard-data');
  assert.equal(
    dashboardApiUrl('/api/dashboard-data', 'http://127.0.0.1:8787/'),
    'http://127.0.0.1:8787/api/dashboard-data',
  );
});

test('xAvatarForHandle resolves tweet author avatars for tweet and seed account rows', () => {
  const items = [
    {
      item_id: 10,
      channel: 'x_tweets',
      name: 'Tweet',
      metadata: { author: 'sama', author_avatar: 'https://cdn.example/sama.jpg' },
      raw: {},
    },
    {
      item_id: 11,
      channel: 'x_tweets',
      name: 'Tweet',
      metadata: { author: 'karpathy' },
      raw: { author: { userName: 'karpathy', profilePicture: 'https://cdn.example/ak.png' } },
    },
    {
      item_id: 12,
      channel: 'x_seed_accounts',
      name: 'sama',
      metadata: { username: 'sama' },
      raw: {},
    },
  ];

  assert.equal(xAvatarForHandle(items, '@sama'), 'https://cdn.example/sama.jpg');
  assert.equal(xAvatarForHandle(items, 'karpathy'), 'https://cdn.example/ak.png');
  assert.equal(xAvatarForHandle(items, 'missing'), '');
});

test('rowsForChannel recomputes RepoFOMO native rank for the selected metric range', () => {
  const items = [
    {
      item_id: 1,
      channel: 'github_movers_repofomo',
      name: 'slow/repo',
      description: '',
      channel_rank: 1,
      metadata: { stars_7d: 2, stars_30d: 40, stars_60d: 100 },
      raw: {},
    },
    {
      item_id: 2,
      channel: 'github_movers_repofomo',
      name: 'fast/repo',
      description: '',
      channel_rank: 2,
      metadata: { stars_7d: 20, stars_30d: 10, stars_60d: 60 },
      raw: {},
    },
  ];

  const rows = rowsForChannel(items, 'github_movers_repofomo', {
    activeWindow: '7d',
    query: '',
    sort: 'native',
    sortDir: 'asc',
  });

  assert.deepEqual(rows.map((row) => row.name), ['fast/repo', 'slow/repo']);
  assert.deepEqual(rows.map((row) => row.__display_rank), [1, 2]);
});

test('sortOptionsForChannel preserves old dashboard source-specific sort choices', () => {
  assert.deepEqual(
    sortOptionsForChannel('github_movers_trending_repos').map((option) => option[0]),
    ['native', 'source_score', 'stars_velocity', 'forks_velocity', 'freshness', 'stars_count'],
  );
  assert.deepEqual(
    sortOptionsForChannel('product_hunt').map((option) => option[0]),
    ['native', 'votes', 'comments', 'daily_rank', 'weekly_rank'],
  );
  assert.deepEqual(
    sortOptionsForChannel('huggingface_models').map((option) => option[0]),
    ['native', 'trendingScore', 'downloads', 'likes'],
  );
  assert.deepEqual(
    sortOptionsForChannel('x_seed_accounts').map((option) => option[0]),
    ['native', 'followers', 'following', 'keyword'],
  );
});

test('column width helpers use the old per-channel localStorage contract', () => {
  assert.equal(columnWidthKey('x_tweets'), 'heroRadarColumnWidths:x_tweets');
  assert.deepEqual(columnWidthStyle({ 2: 144 }, 2), { width: '144px', minWidth: '144px' });
  assert.deepEqual(columnWidthStyle({ 2: 144 }, 1), undefined);
});

test('formatProjectList renders extracted X project objects as names instead of object strings', () => {
  assert.equal(
    formatProjectList([
      { name: 'OpenAI', key: 'openai' },
      { key: 'anthropic' },
      'Claude Code',
      {},
    ]),
    'OpenAI，anthropic，Claude Code',
  );
});

test('settingsPanelDefs restores old writable settings panels with dynamic counts', () => {
  const settingsPayload = {
    channels: [
      { id: 'github_trending', label: 'GitHub Trending', count: 2 },
      { id: 'x_tweets', label: 'X Tweets', count: 3 },
    ],
    source_errors: { github_trending: null, x_tweets: 'disabled' },
    config_meta: { api_status: { github: {}, deepseek: {}, apify: {} } },
    config: {
      github_search: { queries: [{ label: 'agent', query: 'agent stars:>20' }] },
      hn: { algolia_queries: [{ label: 'agent', query: 'agent' }] },
      npm: { queries: [{ label: 'mcp', query: 'mcp' }] },
      apify: { x_keyword_queries: ['agent workflow'], x_seed_accounts: ['sama', 'karpathy'] },
    },
  };

  assert.deepEqual(
    settingsPanelDefs(settingsPayload).map((panel) => [panel.id, panel.label, panel.count]),
    [
      ['settings_run_sources', 'Run & Sources', 2],
      ['settings_search_terms', 'Search Terms', 4],
      ['settings_x_monitoring', 'X Monitoring', 2],
      ['settings_display', 'Display', 2],
      ['settings_api_status', 'API Status', 3],
    ],
  );
});

test('config path helpers update nested settings without mutating the original config', () => {
  const config = {
    github_movers: { trending_repos: { enabled: true, limit_per_period: 500 } },
    apify: { x_seed_accounts: ['sama'] },
  };

  const next = setConfigValue(config, 'github_movers.trending_repos.limit_per_period', 250);
  const withAccount = setConfigValue(next, 'apify.x_seed_accounts.1', 'karpathy');

  assert.equal(getConfigValue(config, 'github_movers.trending_repos.limit_per_period'), 500);
  assert.equal(getConfigValue(withAccount, 'github_movers.trending_repos.limit_per_period'), 250);
  assert.deepEqual(getConfigValue(withAccount, 'apify.x_seed_accounts'), ['sama', 'karpathy']);
});
