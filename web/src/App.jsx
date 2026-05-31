import React, { useEffect, useMemo, useState } from 'react';
import {
  activeChannelList,
  candidateRowsForFeed,
  dashboardApiUrl,
  detailRowsForItem,
  initialDashboardState,
  visibleWindowsForChannel,
  workspaceSections,
} from './dashboardModel.js';

const API_BASE = import.meta.env.VITE_API_BASE || '';
const PAGE_SIZES = [50, 100, 200, 500];

function column(label, help, path, cls = '', kind = 'text') {
  return { label, help, path, cls, kind };
}

function rank(label = '排名', help = '当前 source + 当前时间范围内的原生顺序。切换窗口后会重新从 1 开始；它不是跨 source 总分。') {
  return column(label, help, '$rank', 'num tight', 'num');
}

function name(label = '条目') {
  return column(label, '条目的原始名称和链接。这里不做跨平台实体合并，同一项目在不同 source 可能出现多次。', '$name', 'wide', 'link');
}

function desc() {
  return column('描述', 'source 返回的原文描述字段：GitHub description、Product Hunt tagline、HN story text、tweet text 等。', '$description', 'desc');
}

function detail() {
  return column('详情', '展开后看该 source 的保留字段、facts、样例和 raw/metadata 调试字段。', '$detail', 'wide', 'detail');
}

const COLUMNS_BY_CHANNEL = {
  github_trending: [
    rank('GitHub 排名', 'GitHub Trending 页面在当前时间范围里的原始顺序。'),
    name('Repo'),
    desc(),
    column('GitHub 原文窗口', 'GitHub Trending 的 since 参数。', 'm.period', '', 'githubPeriod'),
    column('原生新增 star', 'GitHub Trending 页面解析出的窗口新增 star。', 'm.period_stars', 'num', 'num'),
    column('总 star', 'GitHub Trending 页面显示的当前仓库总 star。', 'm.stars_total', 'num', 'num'),
    detail(),
  ],
  github_movers_trending_repos: [
    rank('TR 排名', 'Trending Repos 自己的 momentum 榜原始顺序。'),
    name('Repo'),
    desc(),
    column('star 增量曲线', 'Trending Repos 原生 sparkline 数组。', 'm.sparkline', 'spark', 'sparkline'),
    column('总 star', 'Trending Repos 原生 starsCount。', 'm.stars_count', 'num', 'num'),
    column('总 fork', 'Trending Repos 原生 forksCount。', 'm.forks_count', 'num', 'num'),
    column('TR 动量分', 'Trending Repos 原生 score。', 'm.source_score', 'num', 'num'),
    column('主题', 'Trending Repos 返回的 GitHub topics。', 'm.topics', '', 'list'),
    detail(),
  ],
  github_movers_repofomo: [
    rank('范围排名', 'RepoFOMO 当前原生范围内的排名。'),
    name('Repo'),
    desc(),
    column('一句话', 'RepoFOMO 原生 info/pitch 文案。', 'm.info', 'desc'),
    column('总 star', 'RepoFOMO 原生 tot_stars。', 'm.stars_total', 'num', 'num'),
    column('7d 新增 star', 'RepoFOMO 原生 7d_new。', 'm.stars_7d', 'num', 'num'),
    column('30d 新增 star', 'RepoFOMO 原生 30d_new。', 'm.stars_30d', 'num', 'num'),
    column('60d 新增 star', 'RepoFOMO 原生 60d_new。', 'm.stars_60d', 'num', 'num'),
    column('总 fork', 'RepoFOMO 原生 forks。', 'm.forks', 'num', 'num'),
    detail(),
  ],
  github_search: [
    rank('搜索排名', 'GitHub Search API 在当前 query 里的返回顺序。'),
    name('Repo'),
    desc(),
    column('搜索词', '命中这行的 GitHub Search 配置 query。', 'm.query_label'),
    column('总 star', 'GitHub REST API stargazers_count。', 'm.stars', 'num', 'num'),
    column('总 fork', 'GitHub REST API forks_count。', 'm.forks', 'num', 'num'),
    column('open issues', 'GitHub API open_issues_count。', 'r.open_issues_count', 'num', 'num'),
    column('license', 'GitHub API license 字段。', 'r.license'),
    column('创建时间', 'GitHub created_at。', 'm.created_at', '', 'date'),
    column('最近 push', 'GitHub pushed_at。', 'm.pushed_at', '', 'date'),
    column('主题', 'GitHub topics。', 'm.topics', '', 'list'),
    detail(),
  ],
  hn_search: [
    rank('搜索排名', 'HN Algolia search_by_date 在当前 query 和时间范围里的返回顺序。'),
    name('HN/URL'),
    desc(),
    column('搜索词', '命中这行的 HN Algolia query。', 'm.query_label'),
    column('HN 分数', 'HN Algolia 原生 points。', 'm.points', 'num', 'num'),
    column('评论数', 'HN Algolia 原生 num_comments。', 'm.comments', 'num', 'num'),
    column('作者', 'HN author。', 'm.author'),
    column('发布时间', 'HN Algolia created_at。', 'm.created_at', '', 'date'),
    column('HN 链接', 'Hacker News item 页面链接。', 'm.hn_url', '', 'url'),
    detail(),
  ],
  hn_top: [
    rank('榜单排名', 'HN Firebase list 内的原始位置。'),
    name('HN/URL'),
    desc(),
    column('榜单', 'HN Firebase 原生列表来源。', 'm.list'),
    column('HN 分数', 'HN Firebase item.score。', 'm.score', 'num', 'num'),
    column('评论数', 'HN Firebase item.descendants。', 'm.comments', 'num', 'num'),
    column('作者', 'HN Firebase item.by。', 'm.author'),
    column('发布时间', 'HN Firebase item.time，Unix 秒时间戳。', 'm.created_at_unix', '', 'unix'),
    column('HN 链接', 'Hacker News item 页面链接。', 'm.hn_url', '', 'url'),
    detail(),
  ],
  product_hunt: [
    rank('PH 排名', 'Product Hunt tab 的固定顺序。'),
    name('产品'),
    desc(),
    column('票数', 'Product Hunt votesCount。', 'm.votes', 'num', 'num'),
    column('评论数', 'Product Hunt commentsCount。', 'm.comments', 'num', 'num'),
    column('日榜排名', 'Product Hunt dailyRank。', 'm.daily_rank', 'num', 'num'),
    column('周榜排名', 'Product Hunt weeklyRank。', 'm.weekly_rank', 'num', 'num'),
    column('创建时间', 'Product Hunt createdAt。', 'm.created_at', '', 'date'),
    column('官网', 'Product Hunt 返回的产品官网链接。', 'm.website', '', 'url'),
    detail(),
  ],
  npm_search: [
    rank('搜索排名', 'npm registry search API 在当前 query 里的返回顺序。'),
    name('Package'),
    desc(),
    column('搜索词', '命中这行的 npm registry search query。', 'm.query_label'),
    column('版本', 'npm package.version。', 'm.version'),
    column('周下载', 'npm search API downloads.weekly。', 'm.weekly_downloads', 'num', 'num'),
    column('月下载', 'npm search API downloads.monthly。', 'm.monthly_downloads', 'num', 'num'),
    column('被依赖数', 'npm search API dependents。', 'm.dependents', 'num', 'num'),
    column('npm 搜索分', 'npm search API score.final。', 'm.score_final', 'num', 'num'),
    column('license', 'npm package.license。', 'm.license'),
    column('关键词', 'npm package.keywords。', 'm.keywords', '', 'list'),
    column('更新时间', 'npm package.date。', 'm.package_date', '', 'date'),
    detail(),
  ],
  pypi_newest: [
    rank('RSS 排名', 'PyPI RSS feed 的原始条目顺序。'),
    name('Package'),
    desc(),
    column('feed 类型', 'PyPI RSS feed 来源。', 'm.feed'),
    column('版本', 'RSS title 里解析出的 release version。', 'm.version'),
    column('发布时间', 'PyPI RSS pubDate。', 'm.pub_date', '', 'date'),
    column('Python 版本', 'PyPI JSON info.requires_python。', 'm.requires_python'),
    column('license', 'PyPI JSON info.license。', 'm.license'),
    detail(),
  ],
  pypi_updates: [
    rank('RSS 排名', 'PyPI RSS feed 的原始条目顺序。'),
    name('Package'),
    desc(),
    column('feed 类型', 'PyPI RSS feed 来源。', 'm.feed'),
    column('版本', 'RSS title 里解析出的 release version。', 'm.version'),
    column('发布时间', 'PyPI RSS pubDate。', 'm.pub_date', '', 'date'),
    column('Python 版本', 'PyPI JSON info.requires_python。', 'm.requires_python'),
    column('license', 'PyPI JSON info.license。', 'm.license'),
    detail(),
  ],
  x_seed_accounts: [
    rank('粉丝排名', 'Settings 里的 X seed account 顺序。'),
    name('账号'),
    desc(),
    column('username', 'X username，不带 @ 的账号名。', 'm.username', '', 'handle'),
    column('followers', 'X 账号 followers_count。', 'm.followers_count', 'num', 'num'),
    column('following', 'X 账号 following_count。', 'm.following_count', 'num', 'num'),
    column('AI 关键词分', '本地轻量 AI 相关度分。', 'm.keyword_score', 'num', 'num'),
    detail(),
  ],
  x_tweets: [
    rank('tweet 排名', 'X Tweets 当前时间范围里的展示顺序。'),
    name('Tweet'),
    desc(),
    column('作者', 'tweet author username。', 'm.author', '', 'handle'),
    column('created', 'tweet created_at。', 'm.created_at', '', 'date'),
    column('提及对象', '本地规则从 tweet 文本抽出的对象。', 'm.mentioned_projects', '', 'projects'),
    detail(),
  ],
  settings_source_health: [
    rank('序号', 'Settings Source Health 的行号，只用于浏览运行状态。'),
    column('Source', 'pipeline adapter 名称。', '$name'),
    desc(),
    column('状态', '最近一次该 adapter 的运行状态。', 'm.status'),
    column('说明', '错误、禁用或状态备注。', 'm.note', 'desc'),
    column('默认节奏', '当前默认节奏。', 'm.default_schedule'),
    column('生效规则', 'Settings 改动何时生效。', 'm.takes_effect'),
    detail(),
  ],
  settings_search_terms: [
    rank('序号', 'Settings Search Terms 的行号。'),
    column('设置项', '配置项名称。', '$name'),
    desc(),
    column('组', '这个 search term 属于哪个入口。', 'm.group'),
    column('启用', '这个配置项当前是否启用。', 'm.enabled'),
    column('默认节奏', '当前默认节奏。', 'm.default_schedule'),
    column('生效规则', '修改后何时生效。', 'm.takes_effect'),
    detail(),
  ],
};

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
    ['stars_count', '总 star', 'm.stars_count', 'desc'],
  ],
  github_movers_repofomo: [
    ['native', '范围排名', '$rank', 'asc'],
    ['stars_7d', '7d 新增', 'm.stars_7d', 'desc'],
    ['stars_30d', '30d 新增', 'm.stars_30d', 'desc'],
    ['stars_60d', '60d 新增', 'm.stars_60d', 'desc'],
  ],
  github_search: [
    ['native', '搜索顺序', '$rank', 'asc'],
    ['stars', '总 star', 'm.stars', 'desc'],
    ['forks', '总 fork', 'm.forks', 'desc'],
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
  ],
  npm_search: [
    ['native', '搜索顺序', '$rank', 'asc'],
    ['weekly', '周下载', 'm.weekly_downloads', 'desc'],
    ['monthly', '月下载', 'm.monthly_downloads', 'desc'],
    ['score', 'npm 搜索分', 'm.score_final', 'desc'],
  ],
  x_seed_accounts: [
    ['native', '粉丝顺序', '$rank', 'asc'],
    ['followers', '粉丝', 'm.followers_count', 'desc'],
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

const REPOMOFO_RANGES = [
  { id: '7d', label: '7d', kind: 'metric', path: 'm.stars_7d', dir: 'desc', onlyPositive: true },
  { id: '30d', label: '30d', kind: 'metric', path: 'm.stars_30d', dir: 'desc', onlyPositive: true },
  { id: '60d', label: '60d', kind: 'metric', path: 'm.stars_60d', dir: 'desc', onlyPositive: true },
];

function columnsForChannel(channel) {
  return COLUMNS_BY_CHANNEL[channel] || [
    rank(),
    column('时间窗', '这行数据对应的取数窗口。', '$window', 'tight', 'pill'),
    column('来源', '底层 adapter/source 名称。', '$source', 'tight', 'pill'),
    name(),
    desc(),
    column('原生指标', '每个 source 自己最有意义的数字。', 'native_metric.value', 'num', 'num'),
    detail(),
  ];
}

function sortOptionsForChannel(channel) {
  return SORT_OPTIONS_BY_CHANNEL[channel] || [
    ['native', '原生顺序', '$rank', 'asc'],
    ['name', '名称', '$name', 'asc'],
    ['metric', '原生指标', 'native_metric.value', 'desc'],
  ];
}

function valueAt(item, path, rowRank = null) {
  if (!item || !path) return undefined;
  if (path === '$rank') return rowRank ?? item.window_rank ?? item.channel_rank ?? item.source_rank;
  if (path === '$window') return item.window || 'current';
  if (path === '$source') return item.source;
  if (path === '$name') return item.name;
  if (path === '$description') return item.description;
  if (path === '$detail') return item;
  if (path.startsWith('m.')) return getNested(item.metadata, path.slice(2));
  if (path.startsWith('r.')) return getNested(item.raw, path.slice(2));
  return getNested(item, path);
}

function getNested(value, path) {
  return String(path)
    .split('.')
    .filter(Boolean)
    .reduce((current, part) => (current == null ? undefined : current[part]), value);
}

function nativeRank(item) {
  return item.window_rank ?? item.channel_rank ?? item.source_rank ?? 999999;
}

function formatNumber(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '';
  if (Math.abs(num) >= 1000) return Math.round(num).toLocaleString();
  if (Number.isInteger(num)) return String(num);
  return num.toFixed(2);
}

function formatDate(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toISOString().replace('T', ' ').replace(/\.\d{3}Z$/, 'Z');
}

function formatPlain(value) {
  if (value == null) return '';
  if (Array.isArray(value)) return value.filter(Boolean).slice(0, 8).join('，');
  if (typeof value === 'object') return Object.entries(value).slice(0, 5).map(([key, val]) => `${key}: ${String(val)}`).join('；');
  return String(value);
}

function searchText(row) {
  const metadata = row.metadata && typeof row.metadata === 'object' ? Object.values(row.metadata) : [];
  return [row.name, row.description, row.external_id, row.source, ...(row.facts || []), ...metadata].join(' ').toLowerCase();
}

function sortableValue(item, path, rankValue) {
  const value = valueAt(item, path, rankValue);
  if (path === '$name') return String(value || '').toLowerCase();
  if (path.includes('_at') || path.includes('date') || path.includes('created') || path.includes('updated')) {
    const parsed = Date.parse(value || '');
    return Number.isNaN(parsed) ? 0 : parsed;
  }
  if (path.includes('_unix')) return Number(value || 0);
  const num = Number(value);
  return Number.isFinite(num) ? num : String(value || '').toLowerCase();
}

function availableRanges(items, channel) {
  if (channel === 'github_movers_repofomo') return REPOMOFO_RANGES;
  return visibleWindowsForChannel(items, channel).map((value) => ({ id: value, label: value, kind: 'window', value }));
}

function defaultRangeId(items, channel) {
  return availableRanges(items, channel)[0]?.id || '';
}

function rowsForChannel(items, channel, state) {
  const ranges = availableRanges(items, channel);
  const selectedRange = ranges.find((range) => range.id === state.activeWindow) || ranges[0] || null;
  const range = selectedRange || null;
  const query = (state.query || '').trim().toLowerCase();
  const sortOptions = sortOptionsForChannel(channel);
  const sortOption = sortOptions.find((row) => row[0] === state.sort) || sortOptions[0];
  const sortDir = state.sortDir || sortOption?.[3] || 'asc';

  const rows = (items || []).filter((item) => {
    if (item.channel !== channel) return false;
    if (range?.kind === 'window' && (item.window || 'current') !== range.id) return false;
    if (range?.kind === 'metric') {
      const value = Number(valueAt(item, range.path));
      if (range.onlyPositive && !(value > 0)) return false;
    }
    return !query || searchText(item).includes(query);
  });

  rows.sort((a, b) => {
    const av = sortableValue(a, sortOption[2], nativeRank(a));
    const bv = sortableValue(b, sortOption[2], nativeRank(b));
    let diff = 0;
    if (typeof av === 'string' || typeof bv === 'string') diff = String(av).localeCompare(String(bv));
    else diff = av - bv;
    if (sortDir === 'desc') diff *= -1;
    return diff || nativeRank(a) - nativeRank(b) || String(a.name || '').localeCompare(String(b.name || ''));
  });
  return rows;
}

function levelLabel(level) {
  if (level === 'high_potential') return 'High Potential';
  if (level === 'potential') return 'Potential';
  if (level === 'edge_watch') return 'Edge Watch';
  return level || 'Unknown';
}

function StatusDot({ error }) {
  if (error === undefined) return <span className="status-dot">n/a</span>;
  return <span className={`status-dot ${error ? 'warn' : 'ok'}`}>{error ? '注意' : '正常'}</span>;
}

function Sparkline({ values }) {
  if (!Array.isArray(values) || !values.length) return null;
  const nums = values.map(Number).filter(Number.isFinite);
  if (!nums.length) return null;
  const width = 92;
  const height = 28;
  const pad = 3;
  const max = Math.max(1, ...nums);
  const xStep = nums.length > 1 ? (width - pad * 2) / (nums.length - 1) : 0;
  const points = nums.map((num, index) => {
    const x = pad + index * xStep;
    const y = height - pad - (num / max) * (height - pad * 2);
    return `${x.toFixed(1)},${Math.max(pad, Math.min(height - pad, y)).toFixed(1)}`;
  });
  const area = [`${pad},${height - pad}`, ...points, `${width - pad},${height - pad}`].join(' ');
  const last = nums[nums.length - 1];
  return (
    <span className="sparkline" title={`star 增量曲线: ${nums.map(formatNumber).join(' / ')}`}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="star 增量曲线">
        <line className="spark-axis" x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} />
        <polygon className="spark-area" points={area} />
        <polyline className="spark-line" points={points.join(' ')} />
      </svg>
      <span className="sparkline-last">{formatNumber(last)}</span>
    </span>
  );
}

function Handle({ value }) {
  const handle = String(value || '').replace(/^@/, '');
  if (!handle) return null;
  return (
    <span className="x-person">
      <span className="x-avatar x-avatar-fallback" aria-hidden="true">{(handle.slice(0, 2) || '?').toUpperCase()}</span>
      <span className="x-person-name">@{handle}</span>
    </span>
  );
}

function DetailBlock({ item }) {
  const facts = item.facts || [];
  const rows = detailRowsForItem(item);
  const samples = Array.isArray(item.metadata?.sample_tweets) ? item.metadata.sample_tweets.slice(0, 3) : [];
  return (
    <div className="detail-block">
      {item.description ? <p>{item.description}</p> : null}
      {facts.length ? <p className="why">{facts.join(' · ')}</p> : null}
      {samples.map((sample, index) => (
        <div className="sample" key={`${sample.url || sample.text || index}`}>
          {sample.author ? <strong>@{sample.author} · </strong> : null}
          {sample.text || ''}
          {sample.url ? <a href={sample.url} target="_blank" rel="noreferrer"> 原文</a> : null}
        </div>
      ))}
      <div className="raw-grid">
        {rows.map((row) => (
          <div className="raw-cell" key={row.key}>
            <strong>{row.key}</strong>
            <span>{formatPlain(row.value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Cell({ item, column: col, rowRank }) {
  const value = valueAt(item, col.path, rowRank);
  if (col.kind === 'num') return formatNumber(value);
  if (col.kind === 'date') return formatDate(value);
  if (col.kind === 'unix') return value ? formatDate(Number(value) * 1000) : '';
  if (col.kind === 'sparkline') return <Sparkline values={value} />;
  if (col.kind === 'githubPeriod') return ({ daily: 'stars today', weekly: 'stars this week', monthly: 'stars this month' }[value] || value || '');
  if (col.kind === 'pill') return <span className="pill">{formatPlain(value)}</span>;
  if (col.kind === 'url') return value ? <a href={value} target="_blank" rel="noreferrer">打开</a> : '';
  if (col.kind === 'link') {
    return item.url ? <a href={item.url} target="_blank" rel="noreferrer">{formatPlain(value)}</a> : formatPlain(value);
  }
  if (col.kind === 'list' || col.kind === 'object' || col.kind === 'projects') return formatPlain(value);
  if (col.kind === 'handle') return <Handle value={value} />;
  if (col.kind === 'detail') {
    return (
      <details>
        <summary>查看</summary>
        <DetailBlock item={item} />
      </details>
    );
  }
  return formatPlain(value);
}

function SourceTable({ payload, channel, state, onStateChange, titlePrefix = '' }) {
  const items = payload.items || [];
  const ranges = availableRanges(items, channel);
  const columns = columnsForChannel(channel);
  const sortOptions = sortOptionsForChannel(channel);
  const effectiveState = {
    ...state,
    activeWindow: ranges.some((range) => range.id === state.activeWindow) ? state.activeWindow : ranges[0]?.id || '',
  };
  const rows = useMemo(() => rowsForChannel(items, channel, effectiveState), [items, channel, effectiveState.activeWindow, effectiveState.query, effectiveState.sort, effectiveState.sortDir]);
  const totalPages = Math.max(1, Math.ceil(rows.length / state.pageSize));
  const page = Math.min(state.page, totalPages);
  const start = (page - 1) * state.pageSize;
  const pagedRows = rows.slice(start, start + state.pageSize);
  const selectedSort = sortOptions.find((row) => row[0] === state.sort) || sortOptions[0];

  function patch(next) {
    onStateChange({ ...next, page: next.page ?? 1 });
  }

  function sortBy(option) {
    if (!option) return;
    if (state.sort === option[0]) {
      onStateChange({ sortDir: (state.sortDir || option[3]) === 'asc' ? 'desc' : 'asc', page: 1 });
    } else {
      onStateChange({ sort: option[0], sortDir: option[3], page: 1 });
    }
  }

  const from = rows.length ? start + 1 : 0;
  const to = Math.min(start + state.pageSize, rows.length);

  return (
    <>
      <section className="controls">
        <div className="control-group">
          <span className="control-label">搜索</span>
          <input
            className="field search-field"
            value={state.query}
            onChange={(event) => patch({ query: event.target.value })}
            placeholder="name / description / facts"
          />
        </div>
        {ranges.length ? (
          <div className="control-group">
            <span className="control-label">{channel === 'github_movers_repofomo' ? '原生范围' : '时间范围'}</span>
            {ranges.map((range) => (
              <button
                type="button"
                key={range.id}
                className={`control-button ${effectiveState.activeWindow === range.id ? 'active' : ''}`}
                onClick={() => patch({ activeWindow: range.id })}
              >
                {range.label}
              </button>
            ))}
          </div>
        ) : null}
        <div className="control-group">
          <span className="control-label">排序</span>
          <select
            className="select compact-select"
            value={selectedSort[0]}
            onChange={(event) => {
              const option = sortOptions.find((row) => row[0] === event.target.value);
              onStateChange({ sort: option?.[0] || 'native', sortDir: option?.[3] || 'asc', page: 1 });
            }}
          >
            {sortOptions.map((option) => <option key={option[0]} value={option[0]}>{option[1]}</option>)}
          </select>
        </div>
        {titlePrefix ? <div className="control-copy">{titlePrefix}</div> : null}
      </section>

      <div className="table-wrap" id="tableWrap">
        <table>
          <thead>
            <tr>
              {columns.map((col, index) => {
                const sortOption = sortOptions.find((option) => option[2] === col.path);
                const isActiveSort = sortOption && state.sort === sortOption[0];
                const sortArrow = (state.sortDir || sortOption?.[3]) === 'asc' ? '↑' : '↓';
                return (
                  <th
                    key={`${col.path}:${index}`}
                    className={`${col.cls || ''}${sortOption ? ' sortable' : ''}${isActiveSort ? ' sort-active' : ''}`}
                    onClick={() => sortBy(sortOption)}
                  >
                    <span className="th-inner">
                      <span className="th-label">{col.label}</span>
                      {sortOption ? <span className="sort-indicator">{isActiveSort ? sortArrow : '↕'}</span> : null}
                      <span className="hint" data-tip={col.help}>?</span>
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {pagedRows.length ? pagedRows.map((item) => (
              <tr key={`${item.channel}:${item.item_id}:${item.external_id || item.name}`}>
                {columns.map((col, index) => (
                  <td className={col.cls || ''} key={`${item.item_id}:${col.path}:${index}`}>
                    <Cell item={item} column={col} rowRank={nativeRank(item)} />
                  </td>
                ))}
              </tr>
            )) : (
              <tr><td colSpan={columns.length}><div className="empty">这个筛选条件下没有数据。</div></td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="pager">
        <div className="pager-left">
          <span>显示 {from}-{to} / {rows.length} 行</span>
          <span className="pager-size">
            <span>每页</span>
            {PAGE_SIZES.map((size) => (
              <button type="button" key={size} className={`control-button ${state.pageSize === size ? 'active' : ''}`} onClick={() => onStateChange({ pageSize: size, page: 1 })}>{size}/页</button>
            ))}
          </span>
        </div>
        <div className="pager-actions">
          <button type="button" className="control-button" disabled={page <= 1} onClick={() => onStateChange({ page: 1 })}>第一页</button>
          <button type="button" className="control-button" disabled={page <= 1} onClick={() => onStateChange({ page: Math.max(1, page - 1) })}>上一页</button>
          <span>第 {page} / {totalPages} 页</span>
          <button type="button" className="control-button" disabled={page >= totalPages} onClick={() => onStateChange({ page: Math.min(totalPages, page + 1) })}>下一页</button>
          <button type="button" className="control-button" disabled={page >= totalPages} onClick={() => onStateChange({ page: totalPages })}>最后页</button>
        </div>
      </div>
    </>
  );
}

function SourcesView({ payload, state, onStateChange }) {
  const channels = activeChannelList(payload, 'sources');
  const activeChannel = channels.some((channel) => channel.id === state.activeChannel) ? state.activeChannel : channels[0]?.id || '';
  return (
    <>
      <section className="channel-tabs" aria-label="Source channels">
        {channels.map((channel) => (
          <button
            type="button"
            key={channel.id}
            className={channel.id === activeChannel ? 'active' : ''}
            data-tip={channel.description || ''}
            onClick={() => onStateChange({ activeChannel: channel.id, activeWindow: defaultRangeId(payload.items || [], channel.id), sort: 'native', sortDir: 'asc', page: 1 })}
          >
            {channel.label}
            <span className="tab-count">{formatNumber(channel.count)}</span>
          </button>
        ))}
      </section>
      {activeChannel ? (
        <SourceTable payload={payload} channel={activeChannel} state={{ ...state, activeChannel }} onStateChange={onStateChange} />
      ) : <div className="empty">还没有可展示的 source channel。</div>}
    </>
  );
}

function SettingsSubrail({ channels, activeSettings, onSelect }) {
  return (
    <aside className="settings-subrail">
      <div className="subrail-eyebrow">Settings</div>
      <div className="subrail-title">Controls</div>
      <nav className="settings-subnav" aria-label="Settings">
        {channels.map((channel) => (
          <button type="button" key={channel.id} className={channel.id === activeSettings ? 'active' : ''} onClick={() => onSelect(channel.id)}>
            <span className="subnav-label">{channel.label}</span>
            <span className="tab-count">{formatNumber(channel.count)}</span>
          </button>
        ))}
      </nav>
    </aside>
  );
}

function SettingsView({ payload, state, onStateChange }) {
  const channels = activeChannelList(payload, 'settings');
  const activeSettings = channels.some((channel) => channel.id === state.activeSettings) ? state.activeSettings : channels[0]?.id || '';
  return (
    <>
      <section className="status-list">
        <div className="settings-note">
          <strong>Settings 是控制面板，不是项目榜。</strong>
          {' '}这里展示 source health/search terms 等本地配置和运行状态；当前 React 迁移只读动态 payload，不写 pipeline/config.json。
        </div>
      </section>
      <section className="settings-panel">
        <section className="settings-toolbar">
          <div>
            <div className="title">{channels.find((row) => row.id === activeSettings)?.label || 'Settings'}</div>
            <div className="copy">default schedule: {payload.config_meta?.default_schedule || '24h'} · takes effect: {payload.config_meta?.takes_effect || 'next pipeline run'}</div>
          </div>
          <div className="settings-actions">
            {Object.entries(payload.source_errors || {}).slice(0, 4).map(([source, error]) => (
              <span className="status-pill" key={source}>
                <strong>{source}</strong> · <StatusDot error={error} />
              </span>
            ))}
          </div>
        </section>
      </section>
      {activeSettings ? (
        <SourceTable
          payload={payload}
          channel={activeSettings}
          state={{ ...state, activeChannel: activeSettings }}
          onStateChange={onStateChange}
          titlePrefix="Settings rows come from the dynamic dashboard payload."
        />
      ) : <div className="empty">还没有 settings channel。</div>}
    </>
  );
}

function FeedView({ payload }) {
  const [tab, setTab] = useState('daily');
  const [levelFilter, setLevelFilter] = useState('all');
  const rows = useMemo(() => candidateRowsForFeed(payload.candidates), [payload.candidates]);
  const filteredRows = levelFilter === 'all' ? rows : rows.filter((row) => row.level === levelFilter);
  return (
    <>
      <section className="channel-tabs feed-tabs" aria-label="Feed views">
        <button type="button" className={tab === 'daily' ? 'active' : ''} onClick={() => setTab('daily')}>Daily Feed</button>
        <button type="button" className={tab === 'pool' ? 'active' : ''} onClick={() => setTab('pool')}>Candidate Pool</button>
      </section>
      {tab === 'daily' ? (
        <section className="empty feed-locked">
          <h2>Daily Feed locked</h2>
          <p>Layer 2 selection is out of this slice. Candidate Pool below remains available for all Potential / High Potential / Edge Watch rows.</p>
        </section>
      ) : (
        <section className="settings-panel">
          <section className="settings-toolbar">
            <div>
              <div className="title">Candidate Pool</div>
              <div className="copy">Dynamic candidates from /api/dashboard-data · run {payload.candidates?.run_id || payload.run_id || 'unknown'}</div>
            </div>
            <div className="settings-actions">
              <select className="select compact-select" value={levelFilter} onChange={(event) => setLevelFilter(event.target.value)}>
                <option value="all">All levels</option>
                <option value="high_potential">High Potential</option>
                <option value="potential">Potential</option>
                <option value="edge_watch">Edge Watch</option>
              </select>
            </div>
          </section>
          <div className="table-wrap">
            <table className="candidate-table">
              <thead>
                <tr>
                  <th>Entity</th>
                  <th>Level</th>
                  <th>Signals</th>
                  <th>First trigger</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.length ? filteredRows.map((row) => (
                  <tr key={`${row.pool_type}:${row.entity_id}`}>
                    <td>
                      <strong>{row.canonical_entity || row.entity_id}</strong>
                      <code>{row.entity_id}</code>
                    </td>
                    <td><span className={`badge ${row.level}`}>{levelLabel(row.level)}</span></td>
                    <td>{(row.fired_families || row.reasons || []).join(', ')}</td>
                    <td>{row.first_trigger_at || ''}</td>
                    <td>{row.status || row.human_status || ''}</td>
                  </tr>
                )) : (
                  <tr><td colSpan="5"><div className="empty">Candidate Pool 当前没有数据。</div></td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}

function App() {
  const [payload, setPayload] = useState(null);
  const [state, setState] = useState(null);
  const [error, setError] = useState('');
  const [railCollapsed, setRailCollapsed] = useState(() => localStorage.getItem('heroRadarRail') === 'collapsed');
  const [theme] = useState(() => localStorage.getItem('heroRadarTheme') || 'light');

  useEffect(() => {
    fetch(dashboardApiUrl('/api/dashboard-data', API_BASE))
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((data) => {
        const initial = initialDashboardState(data);
        setPayload(data);
        setState({ ...initial, page: 1, pageSize: 100, activeWindow: defaultRangeId(data.items || [], initial.activeChannel), sortDir: 'asc' });
      })
      .catch((err) => setError(String(err.message || err)));
  }, []);

  useEffect(() => {
    localStorage.setItem('heroRadarRail', railCollapsed ? 'collapsed' : 'expanded');
  }, [railCollapsed]);

  function patchState(patch) {
    setState((current) => ({ ...(current || {}), ...patch }));
  }

  const sections = workspaceSections();
  const activeSection = state?.section || 'sources';
  const settingsChannels = payload ? activeChannelList(payload, 'settings') : [];
  const activeSettings = state && settingsChannels.some((channel) => channel.id === state.activeSettings)
    ? state.activeSettings
    : settingsChannels[0]?.id || '';

  return (
    <div className={`app-root ${railCollapsed ? 'rail-collapsed' : ''} ${activeSection === 'settings' ? 'settings-mode' : ''}`} data-theme={theme}>
      <div className="app-shell">
        <aside className="rail">
          <div className="rail-head">
            <div className="brand">
              <div className="brand-mark" aria-hidden="true">HR</div>
              <div className="rail-title">Hero Radar</div>
            </div>
            <button
              className="rail-toggle"
              type="button"
              aria-label={railCollapsed ? '展开侧边栏' : '收起侧边栏'}
              title={railCollapsed ? '展开侧边栏' : '收起侧边栏'}
              onClick={() => setRailCollapsed((value) => !value)}
            />
          </div>
          <div className="nav-label">Workspace</div>
          <nav className="workspace-tabs" aria-label="Workspace">
            {sections.map((section) => (
              <button
                type="button"
                key={section.id}
                className={activeSection === section.id ? 'active' : ''}
                disabled={!section.enabled}
                title={section.enabled ? section.label : 'Layer 3, not in this slice'}
                onClick={() => section.enabled && patchState({ section: section.id, page: 1 })}
              >
                <span className="nav-icon" aria-hidden="true">{section.label.slice(0, 1)}</span>
                <span className="full">{section.label}</span>
              </button>
            ))}
          </nav>
        </aside>

        {payload && activeSection === 'settings' ? (
          <SettingsSubrail
            channels={settingsChannels}
            activeSettings={activeSettings}
            onSelect={(id) => patchState({ activeSettings: id, activeWindow: defaultRangeId(payload.items || [], id), sort: 'native', sortDir: 'asc', page: 1 })}
          />
        ) : null}

        <div className="workspace">
          <main>
            {error ? <div className="error visible">Failed to load dashboard data: {error}</div> : null}
            {!payload || !state ? <div className="empty">Loading dashboard data from {dashboardApiUrl('/api/dashboard-data', API_BASE)}...</div> : null}
            {payload && state && activeSection === 'sources' ? <SourcesView payload={payload} state={state} onStateChange={patchState} /> : null}
            {payload && state && activeSection === 'settings' ? <SettingsView payload={payload} state={{ ...state, activeSettings }} onStateChange={patchState} /> : null}
            {payload && state && activeSection === 'feed' ? <FeedView payload={payload} /> : null}
          </main>
        </div>
      </div>
    </div>
  );
}

export default App;
