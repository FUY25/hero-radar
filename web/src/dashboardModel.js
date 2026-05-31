const WINDOW_ORDER = new Map([
  ['24h', 0],
  ['7d', 1],
  ['30d', 2],
  ['30d+', 3],
  ['7d+30d+60d', 4],
  ['current', 5],
]);

const REPOFOMO_RANGES = [
  { id: '7d', label: '7d', kind: 'metric', path: 'm.stars_7d', dir: 'desc', onlyPositive: true },
  { id: '30d', label: '30d', kind: 'metric', path: 'm.stars_30d', dir: 'desc', onlyPositive: true },
  { id: '60d', label: '60d', kind: 'metric', path: 'm.stars_60d', dir: 'desc', onlyPositive: true },
];

const SORT_OPTIONS_BY_CHANNEL = {
  github_trending: [
    ['native', '原生顺序', '$rank', 'asc'],
    ['period_stars', '窗口新增 star', 'm.period_stars', 'desc'],
    ['total_stars', '总 star', 'm.stars_total', 'desc'],
    ['name', '名称', '$name', 'asc'],
  ],
  github_movers_trending_repos: [
    ['native', '原生顺序', '$rank', 'asc'],
    ['source_score', 'TR 动量分', 'm.source_score', 'desc'],
    ['stars_velocity', 'star 动量', 'm.stars_velocity', 'desc'],
    ['forks_velocity', 'fork 动量', 'm.forks_velocity', 'desc'],
    ['freshness', '新项目加成', 'm.freshness_bonus', 'desc'],
    ['stars_count', '总 star', 'm.stars_count', 'desc'],
  ],
  github_movers_repofomo: [
    ['native', '范围排名', '$rank', 'asc'],
    ['stars_7d', '7d 新增', 'm.stars_7d', 'desc'],
    ['stars_30d', '30d 新增', 'm.stars_30d', 'desc'],
    ['stars_60d', '60d 新增', 'm.stars_60d', 'desc'],
    ['stars_total', '总 star', 'm.stars_total', 'desc'],
  ],
  github_search: [
    ['native', '搜索顺序', '$rank', 'asc'],
    ['stars', '总 star', 'm.stars', 'desc'],
    ['forks', '总 fork', 'm.forks', 'desc'],
    ['open_issues', 'open issues', 'r.open_issues_count', 'desc'],
    ['updated', '更新时间', 'r.updated_at', 'desc'],
  ],
  hn_search: [
    ['native', '搜索顺序', '$rank', 'asc'],
    ['points', 'HN 分数', 'm.points', 'desc'],
    ['comments', '评论数', 'm.comments', 'desc'],
    ['created', '发布时间', 'm.created_at', 'desc'],
  ],
  hn_top: [
    ['native', '榜单顺序', '$rank', 'asc'],
    ['score', 'HN 分数', 'm.score', 'desc'],
    ['comments', '评论数', 'm.comments', 'desc'],
    ['created', '发布时间', 'm.created_at_unix', 'desc'],
  ],
  product_hunt: [
    ['native', 'PH 顺序', '$rank', 'asc'],
    ['votes', '票数', 'm.votes', 'desc'],
    ['comments', '评论数', 'm.comments', 'desc'],
    ['daily_rank', '日榜排名', 'm.daily_rank', 'asc'],
    ['weekly_rank', '周榜排名', 'm.weekly_rank', 'asc'],
  ],
  huggingface_models: [
    ['native', 'HF 顺序', '$rank', 'asc'],
    ['trendingScore', 'HF 趋势分', 'r.trendingScore', 'desc'],
    ['downloads', '下载量', 'm.downloads', 'desc'],
    ['likes', '点赞', 'm.likes', 'desc'],
  ],
  huggingface_datasets: [
    ['native', 'HF 顺序', '$rank', 'asc'],
    ['trendingScore', 'HF 趋势分', 'r.trendingScore', 'desc'],
    ['downloads', '下载量', 'm.downloads', 'desc'],
    ['likes', '点赞', 'm.likes', 'desc'],
  ],
  huggingface_spaces: [
    ['native', 'HF 顺序', '$rank', 'asc'],
    ['trendingScore', 'HF 趋势分', 'r.trendingScore', 'desc'],
    ['likes', '点赞', 'm.likes', 'desc'],
  ],
  npm_search: [
    ['native', '搜索顺序', '$rank', 'asc'],
    ['weekly', '周下载', 'm.weekly_downloads', 'desc'],
    ['monthly', '月下载', 'm.monthly_downloads', 'desc'],
    ['score', 'npm 搜索分', 'm.score_final', 'desc'],
    ['dependents', '被依赖数', 'm.dependents', 'desc'],
  ],
  pypi_newest: [
    ['native', 'RSS 顺序', '$rank', 'asc'],
    ['pub', '发布时间', 'm.pub_date', 'desc'],
    ['name', '名称', '$name', 'asc'],
  ],
  pypi_updates: [
    ['native', 'RSS 顺序', '$rank', 'asc'],
    ['pub', '发布时间', 'm.pub_date', 'desc'],
    ['name', '名称', '$name', 'asc'],
  ],
  x_seed_accounts: [
    ['native', '粉丝顺序', '$rank', 'asc'],
    ['followers', '粉丝', 'm.followers_count', 'desc'],
    ['following', '关注', 'm.following_count', 'desc'],
    ['keyword', 'AI 关键词分', 'm.keyword_score', 'desc'],
  ],
  x_tweets: [
    ['native', 'tweet 顺序', '$rank', 'asc'],
    ['created', '发布时间', 'm.created_at', 'desc'],
  ],
  settings_source_health: [
    ['native', '配置顺序', '$rank', 'asc'],
    ['status', '状态', 'm.status', 'asc'],
  ],
  settings_search_terms: [
    ['native', '配置顺序', '$rank', 'asc'],
    ['group', '组', 'm.group', 'asc'],
    ['name', '名称', '$name', 'asc'],
  ],
};

export function initialDashboardState(payload) {
  return {
    section: 'sources',
    activeChannel: payload.channels?.[0]?.id || '',
    activeSettings: payload.settings_channels?.[0]?.id || '',
    activeWindow: 'all',
    query: '',
    sort: 'native',
    sortDir: 'asc',
    selectedItemId: null,
    railCollapsed: false,
    theme: 'light',
  };
}

export function activeChannelList(payload, section) {
  return section === 'settings' ? (payload.settings_channels || []) : (payload.channels || []);
}

export function workspaceSections() {
  return [
    { id: 'explore', label: 'Explore', enabled: false },
    { id: 'feed', label: 'Feed', enabled: true },
    { id: 'sources', label: 'Sources', enabled: true },
    { id: 'settings', label: 'Settings', enabled: true },
  ];
}

export function candidateRowsForFeed(candidates) {
  return [
    ...(candidates?.candidates || []).map((row) => ({ ...row, pool_type: row.level })),
    ...(candidates?.edge_watch || []).map((row) => ({ ...row, level: 'edge_watch', pool_type: 'edge_watch' })),
  ];
}

export function dashboardApiUrl(path, base = '') {
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  const cleanBase = String(base || '').replace(/\/+$/, '');
  return cleanBase ? `${cleanBase}${cleanPath}` : cleanPath;
}

export function visibleWindowsForChannel(items, channel) {
  const windows = new Set();
  for (const item of items || []) {
    if (item.channel === channel) windows.add(item.window || 'current');
  }
  return [...windows].sort(
    (a, b) => (WINDOW_ORDER.get(a) ?? 99) - (WINDOW_ORDER.get(b) ?? 99) || String(a).localeCompare(String(b)),
  );
}

export function availableRanges(items, channel) {
  if (channel === 'github_movers_repofomo') return REPOFOMO_RANGES;
  return visibleWindowsForChannel(items, channel).map((value) => ({ id: value, label: value, kind: 'window', value }));
}

export function defaultRangeId(items, channel) {
  const ranges = availableRanges(items, channel);
  const preferred = ranges.find((range) => range.id === '24h');
  return preferred?.id || ranges[0]?.id || '';
}

export function sortOptionsForChannel(channel) {
  return SORT_OPTIONS_BY_CHANNEL[channel] || [
    ['native', '原生顺序', '$rank', 'asc'],
    ['name', '名称', '$name', 'asc'],
    ['metric', '原生指标', 'native_metric.value', 'desc'],
  ];
}

export function getNested(value, path) {
  return String(path)
    .split('.')
    .filter(Boolean)
    .reduce((current, part) => (current == null ? undefined : current[part]), value);
}

export function valueAt(item, path, rowRank = null) {
  if (!item || !path) return undefined;
  if (path === '$rank') return rowRank ?? item.__display_rank ?? item.window_rank ?? item.source_rank ?? item.channel_rank;
  if (path === '$window') return item.window || 'current';
  if (path === '$source') return item.source;
  if (path === '$name') return item.name;
  if (path === '$description') return item.description;
  if (path === '$detail') return item;
  if (path.startsWith('m.')) return getNested(item.metadata, path.slice(2));
  if (path.startsWith('r.')) return getNested(item.raw, path.slice(2));
  return getNested(item, path);
}

export function nativeRank(item) {
  return item?.window_rank ?? item?.source_rank ?? item?.channel_rank ?? 999999;
}

function searchableText(row) {
  const metadata = row.metadata && typeof row.metadata === 'object' ? Object.values(row.metadata) : [];
  return [row.name, row.description, row.external_id, row.source, ...(row.facts || []), ...metadata]
    .join(' ')
    .toLowerCase();
}

export function filterAndSortRows(items, state) {
  const query = (state.query || '').trim().toLowerCase();
  const rows = (items || []).filter((item) => {
    if (item.channel !== state.activeChannel) return false;
    if (state.activeWindow && state.activeWindow !== 'all' && (item.window || 'current') !== state.activeWindow) return false;
    return !query || searchableText(item).includes(query);
  });
  const dir = state.sortDir === 'desc' ? -1 : 1;
  return rows.sort((a, b) => {
    if (state.sort === 'name') return String(a.name || '').localeCompare(String(b.name || '')) * dir;
    if (state.sort === 'metric') return ((Number(a.native_metric?.value) || 0) - (Number(b.native_metric?.value) || 0)) * dir;
    return ((a.window_rank || a.channel_rank || 0) - (b.window_rank || b.channel_rank || 0)) * dir;
  });
}

function numericAt(item, path) {
  const num = Number(valueAt(item, path));
  return Number.isFinite(num) ? num : 0;
}

function rowMatchesRange(item, range) {
  if (!range) return true;
  if (range.kind === 'metric') {
    const num = numericAt(item, range.path);
    return range.onlyPositive ? num > 0 : Number.isFinite(num);
  }
  return (item.window || 'current') === range.id;
}

function metricRangeRankMap(items, channel, range) {
  if (!range || range.kind !== 'metric') return new Map();
  const dir = range.dir || 'desc';
  const rows = (items || [])
    .filter((item) => item.channel === channel && rowMatchesRange(item, range))
    .sort((a, b) => {
      const av = numericAt(a, range.path);
      const bv = numericAt(b, range.path);
      const diff = dir === 'asc' ? av - bv : bv - av;
      return diff || Number(a.source_rank ?? a.channel_rank ?? 999999) - Number(b.source_rank ?? b.channel_rank ?? 999999);
    });
  return new Map(rows.map((row, index) => [String(row.item_id), index + 1]));
}

function sortableValue(item, path, rankValue) {
  const value = valueAt(item, path, rankValue);
  if (path === '$name') return String(value || '').toLowerCase();
  if (path.includes('_unix')) return Number(value || 0);
  if (path.includes('_at') || path.includes('date') || path === 'm.pub_date' || path.includes('updated')) {
    const parsed = Date.parse(value || '');
    return Number.isNaN(parsed) ? 0 : parsed;
  }
  const num = Number(value);
  return Number.isFinite(num) ? num : String(value || '').toLowerCase();
}

export function rowsForChannel(items, channel, state) {
  const ranges = availableRanges(items, channel);
  const selectedRange = ranges.find((range) => range.id === state.activeWindow) || ranges[0] || null;
  const query = (state.query || '').trim().toLowerCase();
  const sortOptions = sortOptionsForChannel(channel);
  const sortOption = sortOptions.find((row) => row[0] === state.sort) || sortOptions[0];
  const sortDir = state.sortDir || sortOption?.[3] || 'asc';
  const metricRanks = metricRangeRankMap(items, channel, selectedRange);

  const rows = (items || [])
    .filter((item) => {
      if (item.channel !== channel) return false;
      if (!rowMatchesRange(item, selectedRange)) return false;
      return !query || searchableText(item).includes(query);
    })
    .map((item) => {
      const displayRank = metricRanks.get(String(item.item_id)) || nativeRank(item);
      return { ...item, __display_rank: displayRank };
    });

  rows.sort((a, b) => {
    const av = sortableValue(a, sortOption[2], a.__display_rank);
    const bv = sortableValue(b, sortOption[2], b.__display_rank);
    let diff = 0;
    if (typeof av === 'string' || typeof bv === 'string') diff = String(av).localeCompare(String(bv));
    else diff = av - bv;
    if (sortDir === 'desc') diff *= -1;
    return diff || nativeRank(a) - nativeRank(b) || String(a.name || '').localeCompare(String(b.name || ''));
  });
  return rows;
}

export function xAvatarForHandle(items, handle) {
  const wanted = String(handle || '').replace(/^@/, '').toLowerCase();
  if (!wanted) return '';
  const avatars = new Map();
  for (const item of items || []) {
    const metadata = item?.metadata || {};
    const rawAuthor = item?.raw?.author || {};
    const metadataHandle = metadata.author || metadata.username;
    const metadataAvatar = metadata.author_avatar;
    if (metadataHandle && metadataAvatar) {
      const key = String(metadataHandle).replace(/^@/, '').toLowerCase();
      if (key && !avatars.has(key)) avatars.set(key, metadataAvatar);
    }
    const rawHandle = rawAuthor.userName || rawAuthor.username;
    const rawAvatar = rawAuthor.profilePicture;
    if (rawHandle && rawAvatar) {
      const key = String(rawHandle).replace(/^@/, '').toLowerCase();
      if (key && !avatars.has(key)) avatars.set(key, rawAvatar);
    }
  }
  return avatars.get(wanted) || '';
}

export function columnWidthKey(channel) {
  return `heroRadarColumnWidths:${channel}`;
}

export function columnWidthStyle(widths, index) {
  const width = Number(widths?.[index]);
  if (!Number.isFinite(width) || width <= 0) return undefined;
  const px = `${Math.max(56, Math.round(width))}px`;
  return { width: px, minWidth: px };
}

export function formatProjectList(projects, limit = 8) {
  if (!Array.isArray(projects)) return '';
  return projects
    .map((project) => {
      if (!project) return '';
      if (typeof project === 'object') return project.name || project.key || '';
      return String(project);
    })
    .filter(Boolean)
    .slice(0, limit)
    .join('，');
}

export function getConfigValue(config, path, fallback = undefined) {
  const parts = String(path || '').split('.').filter(Boolean);
  let current = config;
  for (const part of parts) {
    if (current == null || typeof current !== 'object') return fallback;
    current = current[part];
  }
  return current === undefined ? fallback : current;
}

export function setConfigValue(config, path, value) {
  const parts = String(path || '').split('.').filter(Boolean);
  if (!parts.length) return config;
  const clone = Array.isArray(config) ? [...config] : { ...(config || {}) };
  let current = clone;
  for (let index = 0; index < parts.length - 1; index += 1) {
    const part = parts[index];
    const existing = current[part];
    const nextPart = parts[index + 1];
    const child = existing && typeof existing === 'object'
      ? (Array.isArray(existing) ? [...existing] : { ...existing })
      : (/^\d+$/.test(nextPart || '') ? [] : {});
    current[part] = child;
    current = child;
  }
  current[parts[parts.length - 1]] = value;
  return clone;
}

export function settingsPanelDefs(payload) {
  const config = payload?.config || {};
  const searchCount =
    (getConfigValue(config, 'github_search.queries', []) || []).length
    + (getConfigValue(config, 'hn.algolia_queries', []) || []).length
    + (getConfigValue(config, 'npm.queries', []) || []).length
    + (getConfigValue(config, 'apify.x_keyword_queries', []) || []).length;
  const xAccountCount = (getConfigValue(config, 'apify.x_seed_accounts', []) || []).length;
  const sourceCount = Object.keys(payload?.source_errors || {}).length;
  const displayCount = (payload?.channels || []).length;
  const apiCount = Object.keys(payload?.config_meta?.api_status || {}).length;
  return [
    { id: 'settings_run_sources', label: 'Run & Sources', count: sourceCount },
    { id: 'settings_search_terms', label: 'Search Terms', count: searchCount },
    { id: 'settings_x_monitoring', label: 'X Monitoring', count: xAccountCount },
    { id: 'settings_display', label: 'Display', count: displayCount },
    { id: 'settings_api_status', label: 'API Status', count: apiCount },
  ];
}

export function detailRowsForItem(item) {
  if (!item) return [];
  const rows = [];
  for (const [key, value] of Object.entries(item.metadata || {})) {
    rows.push({ key: `metadata.${key}`, value });
  }
  rows.push({ key: 'raw', value: item.raw || {} });
  return rows;
}
