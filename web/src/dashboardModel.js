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

const CANDIDATE_SOURCE_LABELS = {
  github: 'GitHub',
  hn: 'Hacker News',
  x_social: 'X / social',
  product_hunt: 'Product Hunt',
  package_family: 'npm',
  npm: 'npm',
  huggingface: 'Hugging Face',
  hf: 'Hugging Face',
};

const CANDIDATE_SOURCE_ORDER = new Map([
  ['github', 0],
  ['hn', 1],
  ['x_social', 2],
  ['product_hunt', 3],
  ['package_family', 4],
  ['npm', 5],
  ['huggingface', 6],
  ['hf', 7],
]);

const NON_SOURCE_FAMILIES = new Set(['', 'rule', 'resolver', 'cross_source']);

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
    feedTab: 'daily',
    railCollapsed: false,
    theme: 'light',
  };
}

export function activeChannelList(payload, section) {
  return section === 'settings' ? (payload.settings_channels || []) : (payload.channels || []);
}

export function workspaceSections() {
  return [
    { id: 'explore', label: 'Explore', icon: 'search', enabled: false },
    { id: 'feed', label: 'Feed', icon: 'feed', enabled: true },
    { id: 'sources', label: 'Sources', icon: 'database', enabled: true },
    { id: 'settings', label: 'Settings', icon: 'settings', enabled: true },
  ];
}

export function candidateTableColumns() {
  return [
    { label: '候选', cls: 'candidate-name-col' },
    { label: '重要性', cls: 'candidate-level-col' },
    { label: '证据', cls: 'candidate-evidence-col' },
    { label: '来源', cls: 'candidate-source-col' },
    { label: '链接', cls: 'candidate-link-col' },
    { label: '简介', cls: 'candidate-context-col' },
  ];
}

export function candidateVisibleEvidence(row, expanded = false) {
  const bullets = Array.isArray(row?.evidence_bullets) ? row.evidence_bullets : [];
  const hidden = Math.max(0, Number(row?.evidence_count || bullets.length) - 3);
  return {
    bullets: expanded ? bullets : bullets.slice(0, 3),
    extraCount: expanded ? 0 : hidden,
    expandable: bullets.length > 3 || hidden > 0,
  };
}

export function candidateVisibleSources(row, expanded = false, limit = 4) {
  const links = Array.isArray(row?.source_links) ? row.source_links : [];
  const groupsByKey = new Map();
  for (const link of links) {
    const label = sourceGroupLabel(link);
    const key = label;
    if (!groupsByKey.has(key)) {
      groupsByKey.set(key, {
        key,
        label,
        count: 0,
        link,
        names: [],
      });
    }
    const group = groupsByKey.get(key);
    group.count += 1;
    if (link.name && !group.names.includes(link.name)) group.names.push(link.name);
  }
  const groups = [...groupsByKey.values()].map((group) => ({
    ...group,
    title: group.names.slice(0, 4).join('\n'),
  }));
  const visibleLimit = Math.max(1, Number(limit) || 4);
  return {
    sources: expanded ? groups : groups.slice(0, visibleLimit),
    extraCount: expanded ? 0 : Math.max(0, groups.length - visibleLimit),
    expandable: groups.length > visibleLimit,
    totalCount: links.length,
  };
}

export function candidateContextSummary(rawText, expanded = false, limit = 220) {
  const text = cleanPreviewText(rawText);
  if (!text) return { text: '', expandable: false };
  const maxLength = Math.max(40, Number(limit) || 220);
  if (text.length <= maxLength) return { text, expandable: false };
  if (expanded) return { text, expandable: true };
  return {
    text: `${text.slice(0, maxLength).trimEnd()}...`,
    expandable: true,
  };
}

function normalizeCandidateRow(row, poolType) {
  const evidence = Array.isArray(row.evidence_bullets) ? row.evidence_bullets : [];
  const evidenceBullets = evidence.map((bullet) => ({
    ...bullet,
    display_label: readableEvidenceLabel(bullet),
    display_badge: readableProvenanceBadge(bullet),
  }));
  const sourceLinks = normalizeCandidateSourceLinks(row.source_links);
  return {
    ...row,
    level: row.level || poolType,
    pool_type: poolType,
    source_families: candidateSourceFamilies({ ...row, evidence_bullets: evidenceBullets }),
    evidence_bullets: evidenceBullets,
    evidence_extra_count: Math.max(0, Number(row.evidence_count || evidenceBullets.length) - 3),
    canonical_link: row.canonical_link || '',
    context_preview: row.context_preview || '',
    binding_confidence: row.binding_confidence || 'none',
    source_links: sourceLinks,
    source_link_count: Number(row.source_link_count || sourceLinks.length),
  };
}

function normalizeCandidateSourceLinks(rawLinks) {
  if (!Array.isArray(rawLinks)) return [];
  return rawLinks
    .filter((link) => link && link.item_id != null && link.channel)
    .map((link) => ({
      ...link,
      item_id: Number(link.item_id),
      channel: String(link.channel || ''),
      channel_label: String(link.channel_label || link.label || link.channel || ''),
      label: String(link.label || link.channel_label || link.channel || ''),
      name: String(link.name || ''),
      external_url: String(link.external_url || ''),
      window: String(link.window || ''),
    }))
    .filter((link) => Number.isFinite(link.item_id) && link.channel);
}

export function normalizeFeedPayload(payload = {}) {
  const normalizeItem = (item, section) => {
    const members = Array.isArray(item?.context?.members) ? item.context.members : [];
    const evidence = members
      .flatMap((member) => (Array.isArray(member.evidence_bullets) ? member.evidence_bullets : []))
      .map((bullet) => ({
        ...bullet,
        display_label: bullet.display_label || readableEvidenceLabel(bullet),
        display_badge: bullet.display_badge || readableProvenanceBadge(bullet),
      }));
    const sourceLinks = members.flatMap((member) => normalizeCandidateSourceLinks(member.source_links));
    return {
      ...item,
      section,
      title: String(item?.canonical_name || item?.canonical_key || item?.group_id || ''),
      l2_score: Number(item?.l2_score || 0),
      topic_tags: Array.isArray(item?.topic_tags) ? item.topic_tags : [],
      evidence_bullets: evidence,
      source_links: sourceLinks,
      context_preview: members.find((member) => member.context_preview)?.context_preview || '',
      deepdive: item?.deepdive || null,
      deepdive_brief: normalizeDeepdiveBrief(item?.deepdive_brief),
    };
  };
  return {
    feed_run_id: String(payload?.feed_run_id || ''),
    decision_run_id: String(payload?.decision_run_id || ''),
    generated_at: String(payload?.generated_at || ''),
    model_profile: payload?.model_profile || {},
    run_status: String(payload?.run_status || ''),
    telemetry: payload?.telemetry || {},
    stage_events: Array.isArray(payload?.stage_events) ? payload.stage_events : [],
    today_focus: (payload?.today_focus || []).map((item) => normalizeItem(item, 'today_focus')),
    scored_list: (payload?.scored_list || []).map((item) => normalizeItem(item, 'scored')),
    pending: payload?.pending || { edge_watch_scout: 0, deepdive: 0 },
  };
}

export function feedRows(feed) {
  return [
    ...(feed?.today_focus || []),
    ...(feed?.scored_list || []),
  ];
}

export function feedEmptyState(feed) {
  if (!feed?.feed_run_id) return 'missing';
  if (!(feed.today_focus || []).length && !(feed.scored_list || []).length) return 'empty';
  return '';
}

export function feedRunSummary(feed) {
  const profile = feed?.model_profile || {};
  const telemetry = feed?.telemetry || {};
  return {
    run: feed?.feed_run_id || '',
    decision: feed?.decision_run_id || '',
    generated: feed?.generated_at || '',
    models: [
      profile.scout ? `scout ${profile.scout}` : '',
      profile.scoring ? `scoring ${profile.scoring}` : '',
      profile.brief ? `brief ${profile.brief}` : '',
      profile.deepdive ? `deepdive ${profile.deepdive}` : '',
    ].filter(Boolean).join(' · '),
    health: [
      feed?.run_status || '',
      `scored ${Number(telemetry.scored || 0)}`,
      `briefs ${Number(telemetry.briefs || 0)}`,
      `errors ${Number(telemetry.error_total || 0)}`,
    ].filter(Boolean).join(' · '),
  };
}

function normalizeDeepdiveBrief(brief) {
  if (!brief || typeof brief !== 'object') return null;
  const category = brief.category && typeof brief.category === 'object' ? brief.category : {};
  return {
    category: {
      primary: String(category.primary || ''),
      tags: Array.isArray(category.tags) ? category.tags.map((tag) => String(tag)).filter(Boolean) : [],
    },
    headline: String(brief.headline || ''),
    core_highlights: Array.isArray(brief.core_highlights) ? brief.core_highlights.map((item) => String(item)).filter(Boolean) : [],
    use_cases: Array.isArray(brief.use_cases) ? brief.use_cases.map((item) => String(item)).filter(Boolean) : [],
    caveat: brief.caveat ? String(brief.caveat) : '',
  };
}

export function scoreTone(score) {
  const value = Number(score || 0);
  if (value >= 85) return 'hot';
  if (value >= 70) return 'warm';
  if (value >= 50) return 'steady';
  return 'quiet';
}

export function scoreBarStyle(score) {
  const value = Math.max(0, Math.min(100, Math.round(Number(score || 0))));
  return {
    '--score-pct': `${value}%`,
    label: String(value),
  };
}

export function candidateRowsForFeed(candidates) {
  return [
    ...(candidates?.candidates || []).map((row) => normalizeCandidateRow(row, row.level)),
    ...(candidates?.edge_watch || []).map((row) => normalizeCandidateRow({ ...row, level: 'edge_watch' }, 'edge_watch')),
  ];
}

export function candidateSourceLabel(source) {
  return CANDIDATE_SOURCE_LABELS[source] || titleizeSource(source);
}

export function candidateSourceOptions(rows) {
  const counts = new Map();
  for (const row of rows || []) {
    for (const source of candidateSourceFamilies(row)) {
      counts.set(source, (counts.get(source) || 0) + 1);
    }
  }
  return [...counts.entries()]
    .map(([value, count]) => ({ value, label: candidateSourceLabel(value), count }))
    .sort((a, b) => (
      (CANDIDATE_SOURCE_ORDER.get(a.value) ?? 99) - (CANDIDATE_SOURCE_ORDER.get(b.value) ?? 99)
      || a.label.localeCompare(b.label)
    ));
}

export function filterCandidateRows(rows, { levelFilter = 'all', sourceFilters = [] } = {}) {
  const selectedSources = new Set(sourceFilters || []);
  return (rows || []).filter((row) => {
    if (levelFilter !== 'all' && row.level !== levelFilter) return false;
    if (!selectedSources.size) return true;
    return candidateSourceFamilies(row).some((source) => selectedSources.has(source));
  });
}

export function sourceItemNavigationState(items, sourceLink, currentState = {}) {
  const channel = String(sourceLink?.channel || '');
  const itemId = Number(sourceLink?.item_id);
  const pageSize = Number(currentState.pageSize || 100);
  if (!channel || !Number.isFinite(itemId)) {
    return null;
  }
  const ranges = availableRanges(items, channel);
  const requestedWindow = String(sourceLink?.window || '');
  const activeWindow = ranges.some((range) => range.id === requestedWindow)
    ? requestedWindow
    : defaultRangeId(items, channel);
  const targetState = {
    activeChannel: channel,
    activeWindow,
    query: '',
    sort: 'native',
    sortDir: 'asc',
  };
  const rows = rowsForChannel(items, channel, targetState);
  const index = rows.findIndex((row) => Number(row.item_id) === itemId);
  return {
    section: 'sources',
    activeChannel: channel,
    activeWindow,
    selectedItemId: itemId,
    query: '',
    sort: 'native',
    sortDir: 'asc',
    page: index >= 0 ? Math.floor(index / Math.max(1, pageSize)) + 1 : 1,
  };
}

function candidateSourceFamilies(row) {
  const rawFamilies = Array.isArray(row.source_families) && row.source_families.length
    ? row.source_families
    : [
      ...(Array.isArray(row.evidence_bullets) ? row.evidence_bullets.map((bullet) => bullet.family) : []),
      ...(Array.isArray(row.fired_families) ? row.fired_families : []),
    ];
  const families = [];
  for (const family of rawFamilies) {
    const value = String(family || '').trim();
    if (!value || NON_SOURCE_FAMILIES.has(value) || families.includes(value)) continue;
    families.push(value);
  }
  return families;
}

function readableEvidenceLabel(bullet) {
  const label = String(bullet?.label || '').trim();
  const family = String(bullet?.family || '').trim();
  const strength = String(bullet?.strength || '').trim();

  const githubStars = label.match(/^GH \+(.+) stars \/ 24h$/i);
  if (githubStars) return `GitHub 24 小时新增：${githubStars[1]} stars`;

  const githubMetric = label.match(/^github:\s*(stars_today|stars_7d|stars_velocity|forks_velocity|new_forks|forks_7d|daily_downloads|stars_accel_7d_vs_30d|trending_direction_slope)\s+(.+)$/i);
  if (githubMetric) return readableMetricEvidence('github', githubMetric[1], githubMetric[2]);

  const hnStoryCount = label.match(/^hn:\s*strict_story_count_(\d+d)\s+(\d+)$/i);
  if (hnStoryCount) return `HN ${readableWindow(hnStoryCount[1])}内合格讨论：${hnStoryCount[2]} 条`;

  const hnMetric = label.match(/^hn:\s*(hn_max_points_7d)\s+(.+)$/i);
  if (hnMetric) return readableMetricEvidence('hn', hnMetric[1], hnMetric[2]);

  const hnMaxPoints = label.match(/^HN max (.+) pts \/ (.+)$/i);
  if (hnMaxPoints) return `HN ${readableWindow(hnMaxPoints[2])}内最高热度：${hnMaxPoints[1]} points`;

  const hnClassifier = label.match(/^HN classifier:\s*(.+)$/i);
  if (hnClassifier) return `HN 语义判定：${readableClassifierValue(hnClassifier[1])}`;

  const xTier = label.match(/^X\s+(.+)$/i);
  if (xTier) return `X 语义判定：${readableTier(xTier[1])}`;

  const productHuntRank = label.match(/^product_hunt:\s*(daily_rank|weekly_rank)\s+(.+)$/i);
  if (productHuntRank) return readableMetricEvidence('product_hunt', productHuntRank[1], productHuntRank[2]);

  const npmMetric = label.match(/^(package_family|npm):\s*(daily_downloads|downloads_7d|daily_downloads_rising_ratio)\s+(.+)$/i);
  if (npmMetric) return readableMetricEvidence('npm', npmMetric[2], npmMetric[3]);

  const npmRepositoryLink = label.match(/^(package_family|npm):\s*npm_repository_link\s+(.+)$/i);
  if (npmRepositoryLink) return `npm 仓库链接：${readableLinkKey(npmRepositoryLink[2])}`;

  const resolved = label.match(/^Resolved\s+(.+)$/i);
  if (resolved) return `已解析链接：${readableLinkKey(resolved[1])}`;

  if (family) return `${candidateSourceLabel(family)}: ${label.replace(`${family}:`, '').trim() || readableTier(strength)}`;
  return label;
}

function readableProvenanceBadge(bullet) {
  const origin = String(bullet?.origin_type || '').trim();
  const badge = String(bullet?.provenance_badge || '').trim();
  if (origin === 'source_classifier' || badge === 'LLM classifier') return 'LLM';
  return '';
}

function readableClassifierValue(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return {
    company_product: '产品/公司',
    project: '项目',
    repo: 'repo/项目',
    package: '包/库',
    non_product: '非产品',
    topic: '仅话题',
  }[normalized] || normalized.replaceAll('_', ' ');
}

function readableTier(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return {
    high_potential: '高潜力',
    potential: '潜力',
    watch: '观察',
    edge_watch: '观察',
    none: '不相关',
  }[normalized] || normalized.replaceAll('_', ' ');
}

function readableLinkKey(value) {
  const normalized = String(value || '').trim();
  if (normalized.startsWith('github:')) return `GitHub ${normalized.slice('github:'.length)}`;
  if (normalized.startsWith('domain:')) return normalized.slice('domain:'.length);
  if (normalized.startsWith('npm:')) return `npm ${normalized.slice('npm:'.length)}`;
  return normalized;
}

function readableMetricEvidence(family, metric, value) {
  const compact = String(value || '').trim();
  const normalized = String(metric || '').trim().toLowerCase();
  const ratio = compactMetricNumber(compact, 1);
  const decimal = compactMetricNumber(compact, 2);
  if (family === 'github') {
    return {
      stars_today: `GitHub 24 小时新增：${compact} stars`,
      stars_7d: `GitHub 7 天新增：${compact} stars`,
      stars_velocity: `GitHub star 动量：${compact}`,
      forks_velocity: `GitHub fork 动量：${compact}`,
      new_forks: `GitHub 新增 fork：${compact}`,
      forks_7d: `GitHub 7 天新增 fork：${compact}`,
      daily_downloads: `GitHub 相关下载：${compact}`,
      stars_accel_7d_vs_30d: `GitHub star 加速度：${ratio}x`,
      trending_direction_slope: `GitHub 趋势方向：${decimal}`,
    }[normalized] || `GitHub ${normalized}：${compact}`;
  }
  if (family === 'hn') {
    return {
      hn_max_points_7d: `HN 7 天内最高热度：${compact} points`,
    }[normalized] || `HN ${normalized}：${compact}`;
  }
  if (family === 'product_hunt') {
    return {
      daily_rank: `Product Hunt 当日排名：第 ${compact}`,
      weekly_rank: `Product Hunt 本周排名：第 ${compact}`,
    }[normalized] || `Product Hunt ${normalized}：${compact}`;
  }
  if (family === 'npm') {
    return {
      daily_downloads: `npm 日下载：${compact}`,
      downloads_7d: `npm 7 天下载：${compact}`,
      daily_downloads_rising_ratio: `npm 下载加速度：${ratio}x`,
    }[normalized] || `npm ${normalized}：${compact}`;
  }
  return `${normalized}：${compact}`;
}

function compactMetricNumber(value, maxFractionDigits) {
  const raw = String(value || '').trim();
  const numeric = Number(raw);
  if (!Number.isFinite(numeric)) return raw;
  return numeric
    .toFixed(Math.max(0, Number(maxFractionDigits) || 0))
    .replace(/\.?0+$/, '');
}

function cleanPreviewText(rawText) {
  let text = String(rawText || '');
  if (!text.trim()) return '';
  text = text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/~~~[\s\S]*?~~~/g, ' ')
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/\[\s*!\[[^\]]*]\s*]\([^)]*\)/g, ' ')
    .replace(/\[\s*!\[[^\]]*]\s*]\[[^\]]*]/g, ' ')
    .replace(/\[\s*!\[[^\]]*](?:\[[^\]]*]|\([^)]*)?.*$/g, ' ')
    .replace(/^\s*\[[^\]]+]:\s*\S+.*$/gm, ' ')
    .replace(/!\[[^\]]*$/g, ' ')
    .replace(/!\[[^\]]*]\([^)]*\)/g, ' ')
    .replace(/<(?:img|source)\b\s+[A-Za-z_:.-]+=["']?\s*(?=[^<>]*<)/gi, ' ')
    .replace(/<(?:img|source)\b[^<>]*$/gi, ' ')
    .replace(/<[^>]+>/g, ' ');
  text = decodeHtmlEntities(text)
    .replace(/\[([^\]]+)]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/\S+/g, ' ')
    .replace(/<([^<>]{1,80})>/g, '$1')
    .replace(/(^|\s)#{1,6}\s*/g, ' ')
    .replace(/(^|\s)>+\s*/g, ' ')
    .replace(/(^|\s)[*-]\s+/g, ' ')
    .replaceAll('`', '')
    .replaceAll('*', '')
    .replace(/\s+/g, ' ')
    .trim();
  return text;
}

function decodeHtmlEntities(value) {
  return String(value || '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

function readableWindow(value) {
  return String(value || '')
    .replace(/^24h$/i, '24 小时')
    .replace(/^(\d+)d$/i, '$1 天');
}

function sourceGroupLabel(sourceLink) {
  const base = sourceLink?.channel_label || sourceLink?.label || sourceLink?.channel || '来源';
  const window = String(sourceLink?.window || '').trim();
  if (!window || window === 'current') return base;
  return `${base} ${window}`;
}

function titleizeSource(value) {
  return String(value || '')
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
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
  const panels = [
    { id: 'settings_run_sources', label: '运行与来源', count: sourceCount },
    { id: 'settings_search_terms', label: '搜索词', count: searchCount },
    { id: 'settings_x_monitoring', label: 'X 监控', count: xAccountCount },
  ];
  if (config.layer2) {
    panels.push({
      id: 'settings_layer2',
      label: 'Layer 2 Feed',
      count: 0,
      description: 'Kimi scout/scoring/deepdive model and budget settings.',
    });
  }
  panels.push(
    { id: 'settings_display', label: '显示设置', count: displayCount },
    { id: 'settings_api_status', label: 'API 状态', count: apiCount },
  );
  return panels;
}

function numericConfigValue(config, path, fallback) {
  const value = Number(getConfigValue(config, path, fallback));
  return Number.isFinite(value) ? value : fallback;
}

export function layer2RunOptionsFromConfig(config = {}) {
  const layer2 = config.layer2 || {};
  return {
    run_layer2: Boolean(layer2.enabled),
    layer2_scout_limit: numericConfigValue(config, 'layer2.max_edge_watch_scout', 50),
    layer2_scoring_limit: numericConfigValue(config, 'layer2.max_scored_candidates', 150),
    layer2_deepdive_limit: numericConfigValue(config, 'layer2.max_deepdives_per_run', 10),
    layer2_deepdive_min_l2_score: numericConfigValue(config, 'layer2.deepdive_min_l2_score', 70),
    layer2_scout_model: String(layer2.edge_scout_model || 'kimi-k2.5'),
    layer2_scoring_model: String(layer2.scoring_model || 'kimi-k2.5'),
    layer2_deepdive_model: String(layer2.deepdive_model || 'kimi-k2.6'),
    layer2_enable_kimi_web_search: Boolean(layer2.enable_kimi_web_search),
    layer2_max_tool_calls: numericConfigValue(config, 'layer2.max_tool_calls_per_candidate', 20),
    layer2_max_web_search_calls: numericConfigValue(config, 'layer2.max_web_search_calls_per_candidate', 3),
    layer2_max_repo_files: numericConfigValue(config, 'layer2.max_repo_files_per_candidate', 8),
    layer2_max_pages: numericConfigValue(config, 'layer2.max_pages_per_candidate', 6),
    layer2_max_hn_thread_fetches: numericConfigValue(config, 'layer2.max_hn_thread_fetches_per_candidate', 3),
    layer2_max_x_context_fetches: numericConfigValue(config, 'layer2.max_x_context_fetches_per_candidate', 5),
  };
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
