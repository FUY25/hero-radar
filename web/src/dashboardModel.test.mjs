import assert from 'node:assert/strict';
import test from 'node:test';
import {
  activeChannelList,
  candidateContextSummary,
  candidateSourceOptions,
  candidateRowsForFeed,
  candidateTableColumns,
  candidateVisibleEvidence,
  candidateVisibleSources,
  columnWidthKey,
  columnWidthStyle,
  dashboardApiUrl,
  dashboardDataUrl,
  detailRowsForItem,
  filterCandidateRows,
  filterAndSortRows,
  formatProjectList,
  getConfigValue,
  initialDashboardState,
  layer2RunOptionsFromConfig,
  rowsForChannel,
  setConfigValue,
  settingsPanelDefs,
  sortOptionsForChannel,
  staticDemoNotice,
  sourceItemNavigationState,
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
  const sections = workspaceSections();
  assert.deepEqual(sections.map((row) => row.id), ['feed', 'sources', 'settings']);
  assert.deepEqual(sections.map((row) => row.icon), ['feed', 'database', 'settings']);
});

test('candidateRowsForFeed merges potential and edge watch rows', () => {
  const candidates = {
    candidates: [{ entity_id: 'entity:1', canonical_entity: 'Repo', level: 'potential', fired_families: ['github'] }],
    edge_watch: [{ entity_id: 'entity:2', canonical_entity: 'Topic', reasons: ['hn'], status: 'open' }],
  };
  assert.deepEqual(candidateRowsForFeed(candidates).map((row) => row.level), ['potential', 'edge_watch']);
});

test('candidateRowsForFeed keeps evidence and canonical link fields', () => {
  const candidates = {
    candidates: [{
      entity_id: 'entity:1',
      canonical_entity: 'Repo',
      level: 'potential',
      evidence_bullets: [{ label: 'GH +321 stars / 24h', origin_type: 'deterministic_rule' }],
      evidence_count: 4,
      canonical_link: 'https://github.com/owner/repo',
      context_preview: 'Repo description',
      binding_confidence: 'verified',
      source_links: [
        {
          ref: 'item:1',
          item_id: 1,
          source: 'github_trending',
          channel: 'github_trending',
          channel_label: 'GitHub Trending',
          label: 'GitHub Trending',
          name: 'owner/repo',
          external_url: 'https://github.com/owner/repo',
          window: '24h',
        },
      ],
      source_link_count: 1,
    }],
    edge_watch: [],
  };
  const [row] = candidateRowsForFeed(candidates);
  assert.equal(row.evidence_bullets[0].label, 'GH +321 stars / 24h');
  assert.equal(row.evidence_extra_count, 1);
  assert.equal(row.canonical_link, 'https://github.com/owner/repo');
  assert.equal(row.binding_confidence, 'verified');
  assert.equal(row.source_links[0].channel_label, 'GitHub Trending');
  assert.equal(row.source_link_count, 1);
});

test('candidateRowsForFeed adds readable evidence bullet labels and only marks LLM evidence', () => {
  const candidates = {
    candidates: [{
      entity_id: 'entity:1',
      canonical_entity: 'Project',
      level: 'potential',
      evidence_bullets: [
        {
          label: 'HN classifier: company_product',
          family: 'hn',
          origin_type: 'source_classifier',
          provenance_badge: 'LLM classifier',
          strength: 'watch',
        },
        {
          label: 'hn: strict_story_count_7d 3',
          family: 'hn',
          origin_type: 'deterministic_rule',
          provenance_badge: 'rule',
          strength: 'early_trigger',
        },
        {
          label: 'X potential',
          family: 'x_social',
          origin_type: 'source_classifier',
          provenance_badge: 'LLM classifier',
          strength: 'potential',
        },
        {
          label: 'HN max 143 pts / 7d',
          family: 'hn',
          origin_type: 'deterministic_rule',
          provenance_badge: 'rule',
          strength: 'watch',
        },
        {
          label: 'package_family: npm_repository_link github:owner/repo',
          family: 'package_family',
          origin_type: 'backfill',
          provenance_badge: 'backfill',
          strength: 'backfill',
        },
        {
          label: 'github: new_forks 647',
          family: 'github',
          origin_type: 'deterministic_rule',
          provenance_badge: 'rule',
          strength: 'high_potential',
        },
        {
          label: 'github: stars_accel_7d_vs_30d 3.439883451881052',
          family: 'github',
          origin_type: 'deterministic_rule',
          provenance_badge: 'rule',
          strength: 'high_potential',
        },
        {
          label: 'github: trending_direction_slope 0.421332',
          family: 'github',
          origin_type: 'deterministic_rule',
          provenance_badge: 'rule',
          strength: 'watch',
        },
        {
          label: 'package_family: daily_downloads_rising_ratio 4.099',
          family: 'package_family',
          origin_type: 'deterministic_rule',
          provenance_badge: 'rule',
          strength: 'high_potential',
        },
      ],
    }],
    edge_watch: [],
  };

  const [row] = candidateRowsForFeed(candidates);

  assert.deepEqual(row.evidence_bullets.map((bullet) => bullet.display_label), [
    'HN 语义判定：产品/公司',
    'HN 7 天内合格讨论：3 条',
    'X 语义判定：潜力',
    'HN 7 天内最高热度：143 points',
    'npm 仓库链接：GitHub owner/repo',
    'GitHub 新增 fork：647',
    'GitHub star 加速度：3.4x',
    'GitHub 趋势方向：0.42',
    'npm 下载加速度：4.1x',
  ]);
  assert.deepEqual(row.evidence_bullets.map((bullet) => bullet.display_badge), [
    'LLM',
    '',
    'LLM',
    '',
    '',
    '',
    '',
    '',
    '',
  ]);
});

test('candidate source options and filters use source families, not rule provenance', () => {
  const rows = candidateRowsForFeed({
    candidates: [
      {
        entity_id: 'entity:hn',
        canonical_entity: 'HN project',
        level: 'potential',
        source_families: ['hn'],
        evidence_bullets: [{ label: 'hn: strict_story_count_7d 3', family: 'hn', provenance_badge: 'rule' }],
      },
      {
        entity_id: 'entity:x',
        canonical_entity: 'X project',
        level: 'potential',
        source_families: ['x_social'],
        evidence_bullets: [{ label: 'X potential', family: 'x_social', provenance_badge: 'LLM classifier' }],
      },
      {
        entity_id: 'entity:gh',
        canonical_entity: 'GitHub project',
        level: 'edge_watch',
        source_families: ['github'],
        evidence_bullets: [{ label: 'GH +321 stars / 24h', family: 'github', provenance_badge: 'rule' }],
      },
    ],
    edge_watch: [],
  });

  assert.deepEqual(candidateSourceOptions(rows), [
    { value: 'github', label: 'GitHub', count: 1 },
    { value: 'hn', label: 'Hacker News', count: 1 },
    { value: 'x_social', label: 'X / social', count: 1 },
  ]);
  assert.deepEqual(
    filterCandidateRows(rows, { levelFilter: 'all', sourceFilters: ['hn', 'x_social'] }).map((row) => row.entity_id),
    ['entity:hn', 'entity:x'],
  );
  assert.deepEqual(
    filterCandidateRows(rows, { levelFilter: 'edge_watch', sourceFilters: ['hn', 'x_social'] }).map((row) => row.entity_id),
    [],
  );
});

test('dashboardApiUrl defaults to same-origin api and respects explicit base', () => {
  assert.equal(dashboardApiUrl('/api/dashboard-data', ''), '/api/dashboard-data');
  assert.equal(
    dashboardApiUrl('/api/dashboard-data', 'http://127.0.0.1:8787/'),
    'http://127.0.0.1:8787/api/dashboard-data',
  );
});

test('dashboardDataUrl prefers a static demo snapshot when configured', () => {
  assert.equal(
    dashboardDataUrl({ apiBase: '', staticUrl: './demo/dashboard-data.json' }),
    './demo/dashboard-data.json',
  );
  assert.equal(
    dashboardDataUrl({ apiBase: 'http://127.0.0.1:8787/' }),
    'http://127.0.0.1:8787/api/dashboard-data',
  );
});

test('staticDemoNotice only appears for static snapshots', () => {
  assert.equal(staticDemoNotice('').enabled, false);
  assert.deepEqual(staticDemoNotice('./dashboard-data.json'), {
    enabled: true,
    label: '演示快照',
    text: '这是只读演示，不连接后端或实时数据。',
    actionText: '查看 GitHub repo',
    actionUrl: 'https://github.com/FUY25/hero-radar',
  });
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
      ['settings_run_sources', '运行与来源', 2],
      ['settings_search_terms', '搜索词', 4],
      ['settings_x_monitoring', 'X 监控', 2],
      ['settings_display', '显示设置', 2],
      ['settings_api_status', 'API 状态', 3],
    ],
  );
});

test('settingsPanelDefs includes Layer 2 settings when config has layer2', () => {
  const panels = settingsPanelDefs({
    channels: [],
    source_errors: {},
    config_meta: { api_status: { kimi: {} } },
    config: {
      layer2: {
        enabled: true,
        routing: {},
        scoring_agent: { model: 'kimi-k2.5' },
        brief_writer: { model: 'kimi-k2.5' },
        tool_runtime: {},
        edge_scout: { model: 'kimi-k2.5' },
        legacy_deepdive: { model: 'kimi-k2.6' },
      },
    },
  });

  assert.ok(panels.some((panel) => panel.id === 'settings_layer2'));
});

test('layer2RunOptionsFromConfig maps settings to run payload defaults', () => {
  const options = layer2RunOptionsFromConfig({
    layer2: {
      enabled: true,
      routing: {
        max_edge_watch_scout: 11,
        max_scored_candidates: 22,
        max_deepdives_per_run: 3,
        deepdive_min_l2_score: 71,
      },
      scoring_agent: {
        model: 'score-model',
        tool_budget: { max_web_search_calls_per_candidate: 4 },
      },
      brief_writer: { enabled: true, model: 'brief-model' },
      tool_runtime: { enable_kimi_web_search: true },
      edge_scout: { enabled: false, model: 'scout-model' },
      legacy_deepdive: { enabled: false, model: 'deepdive-model' },
    },
  });

  assert.deepEqual(
    {
      run_layer2: options.run_layer2,
      layer2_enable_edge_scout: options.layer2_enable_edge_scout,
      layer2_enable_briefs: options.layer2_enable_briefs,
      layer2_enable_legacy_deepdive: options.layer2_enable_legacy_deepdive,
      layer2_scout_limit: options.layer2_scout_limit,
      layer2_scoring_limit: options.layer2_scoring_limit,
      layer2_deepdive_limit: options.layer2_deepdive_limit,
      layer2_deepdive_min_l2_score: options.layer2_deepdive_min_l2_score,
      layer2_scoring_model: options.layer2_scoring_model,
      layer2_scoring_prompt_version: options.layer2_scoring_prompt_version,
      layer2_scoring_output_schema_version: options.layer2_scoring_output_schema_version,
      layer2_scoring_context_policy_version: options.layer2_scoring_context_policy_version,
      layer2_tool_registry_version: options.layer2_tool_registry_version,
      layer2_brief_model: options.layer2_brief_model,
      layer2_brief_max_output_tokens: options.layer2_brief_max_output_tokens,
      layer2_deepdive_model: options.layer2_deepdive_model,
      layer2_enable_kimi_web_search: options.layer2_enable_kimi_web_search,
      layer2_max_web_search_calls: options.layer2_max_web_search_calls,
    },
    {
      run_layer2: true,
      layer2_enable_edge_scout: false,
      layer2_enable_briefs: true,
      layer2_enable_legacy_deepdive: false,
      layer2_scout_limit: 11,
      layer2_scoring_limit: 22,
      layer2_deepdive_limit: 3,
      layer2_deepdive_min_l2_score: 71,
      layer2_scoring_model: 'score-model',
      layer2_scoring_prompt_version: 'layer2-scoring-investigator-v2',
      layer2_scoring_output_schema_version: 'layer2-scoring-output-v2',
      layer2_scoring_context_policy_version: 'layer2-scoring-context-v1',
      layer2_tool_registry_version: 'layer2-tools-v1',
      layer2_brief_model: 'brief-model',
      layer2_brief_max_output_tokens: 3000,
      layer2_deepdive_model: 'deepdive-model',
      layer2_enable_kimi_web_search: true,
      layer2_max_web_search_calls: 4,
    },
  );
});

test('layer2RunOptionsFromConfig preserves zero scoring limit as no cap', () => {
  const options = layer2RunOptionsFromConfig({
    layer2: {
      enabled: true,
      routing: { max_scored_candidates: 0 },
    },
  });

  assert.equal(options.run_layer2, true);
  assert.equal(options.layer2_scoring_limit, 0);
});

test('candidateTableColumns uses Chinese column names', () => {
  const columns = candidateTableColumns();
  assert.deepEqual(columns.map((column) => column.label), ['候选', '重要性', '证据', '来源', '链接', '简介']);
  assert.equal(columns.at(-1).cls, 'candidate-context-col');
});

test('candidateVisibleEvidence expands the full evidence list', () => {
  const row = {
    evidence_bullets: [
      { label: 'one' },
      { label: 'two' },
      { label: 'three' },
      { label: 'four' },
      { label: 'five' },
    ],
  };

  assert.deepEqual(candidateVisibleEvidence(row, false), {
    bullets: row.evidence_bullets.slice(0, 3),
    extraCount: 2,
    expandable: true,
  });
  assert.deepEqual(candidateVisibleEvidence(row, true), {
    bullets: row.evidence_bullets,
    extraCount: 0,
    expandable: true,
  });
});

test('candidateVisibleSources groups internal source links and exposes hidden groups', () => {
  const row = candidateRowsForFeed({
    candidates: [{
      entity_id: 'entity:1',
      canonical_entity: 'Repo',
      level: 'potential',
      source_links: [
        { item_id: 1, channel: 'hn_top', channel_label: 'HN Top', window: 'current', name: 'story one' },
        { item_id: 2, channel: 'hn_top', channel_label: 'HN Top', window: 'current', name: 'story two' },
        { item_id: 3, channel: 'hn_search', channel_label: 'HN Search', window: '7d', name: 'search story' },
        { item_id: 4, channel: 'product_hunt', channel_label: 'Product Hunt', window: 'current', name: 'launch' },
        { item_id: 5, channel: 'github_trending', channel_label: 'GitHub Trending', window: '24h', name: 'repo' },
        { item_id: 6, channel: 'x_tweets', channel_label: 'X Tweets', window: '7d', name: 'tweet' },
      ],
      source_link_count: 6,
    }],
    edge_watch: [],
  }).at(0);

  assert.deepEqual(
    candidateVisibleSources(row, false).sources.map((source) => [source.label, source.count, source.link.item_id]),
    [
      ['HN Top', 2, 1],
      ['HN Search 7d', 1, 3],
      ['Product Hunt', 1, 4],
      ['GitHub Trending 24h', 1, 5],
    ],
  );
  assert.equal(candidateVisibleSources(row, false).extraCount, 1);
  assert.equal(candidateVisibleSources(row, false).expandable, true);
  assert.equal(candidateVisibleSources(row, true).sources.length, 5);
  assert.equal(candidateVisibleSources(row, true).extraCount, 0);
});

test('candidateContextSummary collapses long descriptions', () => {
  const longText = 'A'.repeat(240);

  assert.deepEqual(candidateContextSummary('Short description', false, 80), {
    text: 'Short description',
    expandable: false,
  });
  assert.deepEqual(candidateContextSummary(longText, false, 80), {
    text: `${'A'.repeat(80)}...`,
    expandable: true,
  });
  assert.deepEqual(candidateContextSummary(longText, true, 80), {
    text: longText,
    expandable: true,
  });
});

test('candidateContextSummary cleans markdown and html before display', () => {
  const raw = '<p align="center"><a href="https://opencode.ai"><img alt="OpenCode logo"></a></p> ![npm](https://img.shields.io/npm/v/opencode-ai.svg) # OpenCode [docs](https://opencode.ai/docs) ```bash npm install opencode-ai ```';

  assert.deepEqual(candidateContextSummary(raw, false, 120), {
    text: 'OpenCode docs',
    expandable: false,
  });
  assert.deepEqual(
    candidateContextSummary('<img src=" Banner title <p align="center">Readable project summary', false, 120),
    {
      text: 'Banner title Readable project summary',
      expandable: false,
    },
  );
  assert.deepEqual(
    candidateContextSummary('# Claude Agent SDK [![npm]][npm]\n[npm]: https://img.shields.io/npm/v/@anthropic-ai/claude-agent-sdk.svg\nThe Claude Agent SDK enables programmatic agents.', false, 160),
    {
      text: 'Claude Agent SDK The Claude Agent SDK enables programmatic agents.',
      expandable: false,
    },
  );
  assert.deepEqual(
    candidateContextSummary('Useful coding agent. [![Code style: Ruff](https:/ <img src="" ![Release', false, 120),
    {
      text: 'Useful coding agent.',
      expandable: false,
    },
  );
  assert.deepEqual(
    candidateContextSummary('Personal AI Infrastructure ![Releas', false, 120),
    {
      text: 'Personal AI Infrastructure',
      expandable: false,
    },
  );
  assert.deepEqual(
    candidateContextSummary('Compress outputs ▉▉▉ before agents ┌──┘ 60-95% fewer tokens', false, 120),
    {
      text: 'Compress outputs before agents 60-95% fewer tokens',
      expandable: false,
    },
  );
});

test('sourceItemNavigationState opens the internal source row and page from a candidate source link', () => {
  const items = [
    { item_id: 10, channel: 'hn_search', name: 'old story', window: '7d', channel_rank: 1, window_rank: 1, metadata: {}, raw: {} },
    { item_id: 11, channel: 'hn_search', name: 'target story', window: '7d', channel_rank: 2, window_rank: 2, metadata: {}, raw: {} },
    { item_id: 12, channel: 'hn_search', name: 'new story', window: '24h', channel_rank: 3, window_rank: 1, metadata: {}, raw: {} },
  ];

  assert.deepEqual(
    sourceItemNavigationState(items, { item_id: 11, channel: 'hn_search', window: '7d' }, { pageSize: 1 }),
    {
      section: 'sources',
      activeChannel: 'hn_search',
      activeWindow: '7d',
      selectedItemId: 11,
      query: '',
      sort: 'native',
      sortDir: 'asc',
      page: 2,
    },
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
