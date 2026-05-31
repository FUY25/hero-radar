import React, { useEffect, useMemo, useState } from 'react';
import {
  ArrowSquareOut,
  ChartLineUp,
  Lightning,
  Sparkle,
  ThumbsDown,
  ThumbsUp,
} from '@phosphor-icons/react';
import {
  activeChannelList,
  availableRanges as modelAvailableRanges,
  candidateSourceOptions,
  candidateRowsForFeed,
  candidateTableColumns,
  candidateVisibleEvidence,
  columnWidthKey,
  columnWidthStyle,
  dashboardApiUrl,
  defaultRangeId as modelDefaultRangeId,
  detailRowsForItem,
  feedEmptyState,
  feedRunSummary,
  filterCandidateRows,
  formatProjectList,
  getConfigValue,
  initialDashboardState,
  nativeRank as modelNativeRank,
  normalizeFeedPayload,
  rowsForChannel as modelRowsForChannel,
  scoreTone,
  setConfigValue,
  settingsPanelDefs,
  sourceItemNavigationState,
  sortOptionsForChannel as modelSortOptionsForChannel,
  valueAt as modelValueAt,
  workspaceSections,
  xAvatarForHandle,
} from './dashboardModel.js';

const API_BASE = import.meta.env.VITE_API_BASE || '';
const PAGE_SIZES = [50, 100, 200, 500];

function readColumnWidths(channel) {
  try {
    const parsed = JSON.parse(localStorage.getItem(columnWidthKey(channel)) || '{}');
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch (_) {
    return {};
  }
}

function writeColumnWidths(channel, widths) {
  localStorage.setItem(columnWidthKey(channel), JSON.stringify(widths || {}));
}

function storedPageSize() {
  const value = Number(localStorage.getItem('heroRadarDefaultPageSize') || 100);
  return PAGE_SIZES.includes(value) ? value : 100;
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value ?? {}));
}

function readLocalJson(key, fallback) {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || 'null');
    return parsed ?? fallback;
  } catch (_) {
    return fallback;
  }
}

function readAppUrlState() {
  const params = new URLSearchParams(window.location.search || '');
  const section = params.get('section');
  const feed = params.get('feed');
  const source = params.get('source');
  const settings = params.get('settings');
  const sourceWindow = params.get('window');
  const item = Number(params.get('item'));
  return {
    section: workspaceSections().some((row) => row.id === section && row.enabled) ? section : '',
    feedTab: feed === 'pool' || feed === 'daily' ? feed : '',
    activeChannel: source || '',
    activeSettings: settings || '',
    activeWindow: sourceWindow || '',
    selectedItemId: Number.isFinite(item) ? item : null,
  };
}

function appUrlForState(state) {
  const params = new URLSearchParams();
  const section = state?.section || 'sources';
  params.set('section', section);
  if (section === 'feed') {
    params.set('feed', state.feedTab || 'daily');
  }
  if (section === 'sources') {
    if (state.activeChannel) params.set('source', state.activeChannel);
    if (state.activeWindow) params.set('window', state.activeWindow);
    if (state.selectedItemId != null) params.set('item', String(state.selectedItemId));
  }
  if (section === 'settings' && state.activeSettings) {
    params.set('settings', state.activeSettings);
  }
  const query = params.toString();
  return `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash || ''}`;
}

function writeAppHistory(state, mode = 'push') {
  if (!state || !window.history) return;
  const nextUrl = appUrlForState(state);
  const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash || ''}`;
  if (nextUrl === currentUrl) return;
  const fn = mode === 'replace' ? window.history.replaceState : window.history.pushState;
  fn.call(window.history, { heroRadar: true }, '', nextUrl);
}

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

function hfColumns(label) {
  return [
    rank('HF 排名', 'Hugging Face trending API 的原始返回顺序。'),
    name(label),
    desc(),
    column('HF 趋势分', 'Hugging Face API 原生 trendingScore。', 'r.trendingScore', 'num', 'num'),
    column('点赞', 'Hugging Face likes。', 'm.likes', 'num', 'num'),
    column('下载量', 'Hugging Face downloads。', 'm.downloads', 'num', 'num'),
    column('任务/类型', 'Hugging Face pipeline_tag。', 'm.pipeline_tag'),
    column('库/SDK', 'Hugging Face library_name 或 Spaces sdk。', 'r.library_name'),
    column('创建时间', 'Hugging Face createdAt。', 'm.created_at', '', 'date'),
    column('修改时间', 'Hugging Face lastModified。', 'm.last_modified', '', 'date'),
    column('标签', 'Hugging Face tags。', 'm.tags', '', 'list'),
    detail(),
  ];
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
    column('star 动量', 'Trending Repos 原生 scoreComponents.starsVelocity。', 'm.stars_velocity', 'num', 'num'),
    column('fork 动量', 'Trending Repos 原生 scoreComponents.forksVelocity。', 'm.forks_velocity', 'num', 'num'),
    column('新项目加成', 'Trending Repos 原生 scoreComponents.freshnessBonus。', 'm.freshness_bonus', 'num', 'num'),
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
    column('7d 增长率', 'RepoFOMO 原生 7d% 增长率。', 'm.growth_7d_percent', 'num', 'num'),
    column('30d 增长率', 'RepoFOMO 原生 30d% 增长率。', 'm.growth_30d_percent', 'num', 'num'),
    column('总 fork', 'RepoFOMO 原生 forks。', 'm.forks', 'num', 'num'),
    column('新增 fork', 'RepoFOMO 原生 new_forks。', 'm.new_forks', 'num', 'num'),
    column('repo 年龄(天)', 'RepoFOMO 原生 star_age。', 'm.star_age_days', 'num', 'num'),
    detail(),
  ],
  github_search: [
    rank('搜索排名', 'GitHub Search API 在当前 query 里的返回顺序。'),
    name('Repo'),
    desc(),
    column('搜索词', '命中这行的 GitHub Search 配置 query。', 'm.query_label'),
    column('总 star', 'GitHub REST API stargazers_count。', 'm.stars', 'num', 'num'),
    column('总 fork', 'GitHub REST API forks_count。', 'm.forks', 'num', 'num'),
    column('watchers(≈star)', 'GitHub API watchers_count。', 'r.watchers_count', 'num', 'num'),
    column('open issues', 'GitHub API open_issues_count。', 'r.open_issues_count', 'num', 'num'),
    column('license', 'GitHub API license 字段。', 'r.license'),
    column('创建时间', 'GitHub created_at。', 'm.created_at', '', 'date'),
    column('更新时间', 'GitHub updated_at。', 'r.updated_at', '', 'date'),
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
    column('Featured 时间', 'Product Hunt featuredAt。', 'm.featured_at', '', 'date'),
    column('官网', 'Product Hunt 返回的产品官网链接。', 'm.website', '', 'url'),
    detail(),
  ],
  huggingface_models: hfColumns('Model'),
  huggingface_datasets: hfColumns('Dataset'),
  huggingface_spaces: hfColumns('Space'),
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
    column('质量分', 'npm search API score.detail.quality。', 'm.score_quality', 'num', 'num'),
    column('流行度分', 'npm search API score.detail.popularity。', 'm.score_popularity', 'num', 'num'),
    column('维护分', 'npm search API score.detail.maintenance。', 'm.score_maintenance', 'num', 'num'),
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
    column('最新版本', 'PyPI JSON API info.version。', 'm.latest_version'),
    column('发布时间', 'PyPI RSS pubDate。', 'm.pub_date', '', 'date'),
    column('Python 版本', 'PyPI JSON info.requires_python。', 'm.requires_python'),
    column('license', 'PyPI JSON info.license。', 'm.license'),
    column('关键词', 'PyPI JSON info.keywords。', 'm.keywords'),
    column('分类', 'PyPI JSON classifiers。', 'm.classifiers', 'desc', 'list'),
    column('项目链接', 'PyPI JSON project_urls。', 'm.project_urls', 'desc', 'object'),
    detail(),
  ],
  pypi_updates: [
    rank('RSS 排名', 'PyPI RSS feed 的原始条目顺序。'),
    name('Package'),
    desc(),
    column('feed 类型', 'PyPI RSS feed 来源。', 'm.feed'),
    column('版本', 'RSS title 里解析出的 release version。', 'm.version'),
    column('最新版本', 'PyPI JSON API info.version。', 'm.latest_version'),
    column('发布时间', 'PyPI RSS pubDate。', 'm.pub_date', '', 'date'),
    column('Python 版本', 'PyPI JSON info.requires_python。', 'm.requires_python'),
    column('license', 'PyPI JSON info.license。', 'm.license'),
    column('关键词', 'PyPI JSON info.keywords。', 'm.keywords'),
    column('分类', 'PyPI JSON classifiers。', 'm.classifiers', 'desc', 'list'),
    column('项目链接', 'PyPI JSON project_urls。', 'm.project_urls', 'desc', 'object'),
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
  return modelSortOptionsForChannel(channel);
}

function valueAt(item, path, rowRank = null) {
  return modelValueAt(item, path, rowRank);
}

function nativeRank(item) {
  return modelNativeRank(item);
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

function availableRanges(items, channel) {
  return modelAvailableRanges(items, channel);
}

function defaultRangeId(items, channel) {
  return modelDefaultRangeId(items, channel);
}

function rowsForChannel(items, channel, state) {
  return modelRowsForChannel(items, channel, state);
}

function levelLabel(level) {
  if (level === 'high_potential') return 'High Potential';
  if (level === 'potential') return 'Potential';
  if (level === 'edge_watch') return 'Edge Watch';
  return level || 'Unknown';
}

function sourceChipLabel(sourceLink) {
  const base = sourceLink?.channel_label || sourceLink?.label || sourceLink?.channel || '来源';
  return sourceLink?.window ? `${base} ${sourceLink.window}` : base;
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

function Handle({ value, item, items }) {
  const handle = String(value || '').replace(/^@/, '');
  if (!handle) return null;
  const avatarUrl = item?.metadata?.author_avatar || xAvatarForHandle(items, handle);
  return (
    <span className="x-person">
      {avatarUrl ? (
        <img className="x-avatar" src={avatarUrl} alt={`@${handle}`} loading="lazy" referrerPolicy="no-referrer" />
      ) : (
        <span className="x-avatar x-avatar-fallback" aria-hidden="true">{(handle.slice(0, 2) || '?').toUpperCase()}</span>
      )}
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

function Cell({ item, column: col, rowRank, items, selected = false }) {
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
  if (col.kind === 'projects') return formatProjectList(value);
  if (col.kind === 'list' || col.kind === 'object') return formatPlain(value);
  if (col.kind === 'handle') return <Handle value={value} item={item} items={items} />;
  if (col.kind === 'detail') {
    return (
      <details open={selected}>
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
  const [columnWidths, setColumnWidths] = useState(() => readColumnWidths(channel));
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

  useEffect(() => {
    setColumnWidths(readColumnWidths(channel));
  }, [channel]);

  useEffect(() => {
    if (!state.selectedItemId) return;
    const row = document.getElementById(`source-item-${state.selectedItemId}`);
    if (row) row.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [state.selectedItemId, channel, page]);

  function sortBy(option) {
    if (!option) return;
    if (state.sort === option[0]) {
      onStateChange({ sortDir: (state.sortDir || option[3]) === 'asc' ? 'desc' : 'asc', page: 1 });
    } else {
      onStateChange({ sort: option[0], sortDir: option[3], page: 1 });
    }
  }

  function startColumnResize(event, index) {
    event.preventDefault();
    event.stopPropagation();
    const th = event.currentTarget.closest('th');
    if (!th) return;
    const startX = event.clientX;
    const startWidth = th.getBoundingClientRect().width;
    th.classList.add('is-resizing');
    document.body.classList.add('resizing-columns');
    const onMove = (moveEvent) => {
      const nextWidth = Math.max(56, Math.round(startWidth + moveEvent.clientX - startX));
      setColumnWidths((current) => {
        const next = { ...(current || {}), [index]: nextWidth };
        writeColumnWidths(channel, next);
        return next;
      });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      th.classList.remove('is-resizing');
      document.body.classList.remove('resizing-columns');
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp, { once: true });
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
                    data-col-index={index}
                    style={columnWidthStyle(columnWidths, index)}
                    onClick={(event) => {
                      if (event.target.closest('.col-resizer') || event.target.closest('.hint')) return;
                      sortBy(sortOption);
                    }}
                  >
                    <span className="th-inner">
                      <span className="th-label">{col.label}</span>
                      {sortOption ? <span className="sort-indicator">{isActiveSort ? sortArrow : '↕'}</span> : null}
                      <span className="hint" data-tip={col.help}>?</span>
                    </span>
                    <span
                      className="col-resizer"
                      data-col-index={index}
                      title="拖动调整列宽"
                      aria-hidden="true"
                      onPointerDown={(event) => startColumnResize(event, index)}
                      onClick={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                      }}
                    />
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {pagedRows.length ? pagedRows.map((item) => {
              const selected = Number(state.selectedItemId) === Number(item.item_id);
              return (
              <tr
                key={`${item.channel}:${item.item_id}:${item.external_id || item.name}`}
                id={`source-item-${item.item_id}`}
                className={selected ? 'selected-source-row' : ''}
              >
                {columns.map((col, index) => (
                  <td
                    className={col.cls || ''}
                    key={`${item.item_id}:${col.path}:${index}`}
                    style={columnWidthStyle(columnWidths, index)}
                  >
                    <Cell item={item} column={col} rowRank={item.__display_rank ?? nativeRank(item)} items={items} selected={selected} />
                  </td>
                ))}
              </tr>
              );
            }) : (
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
              <button
                type="button"
                key={size}
                className={`control-button ${state.pageSize === size ? 'active' : ''}`}
                onClick={() => {
                  localStorage.setItem('heroRadarDefaultPageSize', String(size));
                  onStateChange({ pageSize: size, page: 1 });
                }}
              >
                {size}/页
              </button>
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

function SourcesView({ payload, state, onStateChange, hiddenSources = new Set() }) {
  const channels = activeChannelList(payload, 'sources').filter((channel) => !hiddenSources.has(channel.id));
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
            onClick={() => {
              localStorage.setItem('heroRadarSourceTab', channel.id);
              onStateChange(
                { activeChannel: channel.id, activeWindow: defaultRangeId(payload.items || [], channel.id), sort: 'native', sortDir: 'asc', page: 1, selectedItemId: null },
                { history: 'push' },
              );
            }}
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
      <div className="subrail-title">控制台</div>
      <nav className="settings-subnav" aria-label="Settings">
        {channels.map((channel) => (
          <button type="button" key={channel.id} className={channel.id === activeSettings ? 'active' : ''} onClick={() => onSelect(channel.id)}>
            <span className="subnav-label">{channel.label}</span>
          </button>
        ))}
      </nav>
    </aside>
  );
}

function SourceHealthBadge({ payload, source }) {
  return <StatusDot error={payload.source_errors?.[source]} />;
}

function SettingField({ config, path, label, help, type = 'text', onConfigChange, min, max, step }) {
  const value = getConfigValue(config, path, '');
  return (
    <div className="setting-row two">
      <div>
        <div className="setting-label">{label}</div>
        <div className="setting-help">{help}</div>
      </div>
      <input
        className="field"
        type={type}
        min={min}
        max={max}
        step={step}
        value={value ?? ''}
        onChange={(event) => {
          const nextValue = type === 'number' ? Number(event.target.value || 0) : event.target.value;
          onConfigChange(path, nextValue);
        }}
      />
    </div>
  );
}

function SettingCheckbox({ config, path, label, help, onConfigChange }) {
  return (
    <label className="toggle-row">
      <input
        type="checkbox"
        checked={Boolean(getConfigValue(config, path, false))}
        onChange={(event) => onConfigChange(path, event.target.checked)}
      />
      <span>
        <strong>{label}</strong>
        <div className="setting-help">{help}</div>
      </span>
    </label>
  );
}

function SettingSelect({ config, path, label, help, options, onConfigChange }) {
  const value = String(getConfigValue(config, path, options[0] || ''));
  return (
    <div className="setting-row two">
      <div>
        <div className="setting-label">{label}</div>
        <div className="setting-help">{help}</div>
      </div>
      <select className="select" value={value} onChange={(event) => onConfigChange(path, event.target.value)}>
        {options.map((option) => <option value={option} key={option}>{option}</option>)}
      </select>
    </div>
  );
}

function SettingsCard({ payload, title, source, note, children }) {
  const error = payload.source_errors?.[source];
  return (
    <div className="settings-card">
      <div className="settings-card-head">
        <div>
          <div className="settings-card-title">{title}</div>
          <div className="settings-card-note">{note}</div>
        </div>
        <SourceHealthBadge payload={payload} source={source} />
      </div>
      <div className="setting-list">
        {children}
        {error ? <div className="message-line warn">{error}</div> : null}
      </div>
    </div>
  );
}

function SettingsToolbar({ panel, configDirty, configBusy, message, messageKind, onSave, onReload, onRun }) {
  return (
    <section className="settings-toolbar">
      <div>
        <div className="title">{panel?.label || 'Settings'}</div>
        <div className="copy">server mode · {configDirty ? '有未保存修改' : '配置已同步'} · 保存后下一次 pipeline run 生效</div>
        {message ? <div className={`message-line ${messageKind || ''}`}>{message}</div> : null}
      </div>
      <div className="settings-actions">
        <button type="button" className="primary-button" disabled={!configDirty || configBusy} onClick={onSave}>保存配置</button>
        <button type="button" disabled={configBusy} onClick={onReload}>从 API 重载</button>
        <button type="button" disabled={configDirty || configBusy} onClick={onRun}>立即运行</button>
      </div>
    </section>
  );
}

function RunSourcesSettings({ payload, config, onConfigChange }) {
  return (
    <>
      <section className="settings-section">
        <h2>运行与来源</h2>
        <p className="section-copy">控制每个 source 是否启用、抓取上限和最近一次运行状态。这里不做打分，只改变下一次 pipeline 如何采集。</p>
        <div className="settings-grid">
          <SettingsCard payload={payload} title="GitHub Trending" source="github_trending" note="抓 GitHub Trending daily / weekly / monthly；语言 scope 仍由 config 控制但不在 UI 主动调。">
            <div className="setting-help">Always on。当前不在 Settings 暴露 language/scope filter。</div>
          </SettingsCard>
          <SettingsCard payload={payload} title="Trending Repos" source="github_movers" note="第三方 GitHub momentum source；抓 daily / weekly / monthly。">
            <SettingCheckbox config={config} path="github_movers.trending_repos.enabled" label="启用 Trending Repos" help="关闭后下一次 run 不抓这个 mover source。" onConfigChange={onConfigChange} />
            <SettingField config={config} path="github_movers.trending_repos.limit_per_period" label="每窗口上限" help="配置请求/解析后每个 period 最多保留多少条；source 可能实际只给更少。" type="number" min="1" step="1" onConfigChange={onConfigChange} />
          </SettingsCard>
          <SettingsCard payload={payload} title="RepoFOMO" source="github_movers" note="周/月级 repo movers 补充源。">
            <SettingCheckbox config={config} path="github_movers.repofomo.enabled" label="启用 RepoFOMO" help="关闭后下一次 run 不抓 RepoFOMO leaderboard。" onConfigChange={onConfigChange} />
            <SettingField config={config} path="github_movers.repofomo.limit" label="保留上限" help="leaderboard 最多保留多少条。" type="number" min="1" step="1" onConfigChange={onConfigChange} />
          </SettingsCard>
          <SettingsCard payload={payload} title="GitHub Search" source="github_search" note="按 Search Terms 里的 GitHub query 主动搜索 repo。">
            <SettingField config={config} path="github_search.max_results_per_query" label="每个 query 最大结果" help="每个 GitHub Search query 最多抓多少条；受 GitHub API 分页和 rate limit 影响。" type="number" min="1" step="1" onConfigChange={onConfigChange} />
            <SettingField config={config} path="github_search.per_page" label="每页大小" help="GitHub Search API per_page，最大通常是 100。" type="number" min="1" max="100" step="1" onConfigChange={onConfigChange} />
          </SettingsCard>
          <SettingsCard payload={payload} title="HN Search" source="hn_algolia" note="HN Algolia search_by_date，按 Search Terms 和窗口抓讨论。">
            <SettingField config={config} path="hn.algolia_hits_per_page" label="每 query/window 上限" help="HN Algolia 每个 query 和时间窗最多返回多少条。" type="number" min="1" step="1" onConfigChange={onConfigChange} />
          </SettingsCard>
          <SettingsCard payload={payload} title="HN Top" source="hn_firebase" note="HN Firebase top/new/best 榜单。">
            <SettingField config={config} path="hn.firebase_limit" label="每个榜单上限" help="topstories/newstories/beststories 每个 list 取多少条。" type="number" min="1" step="1" onConfigChange={onConfigChange} />
            <SettingField config={config} path="hn.firebase_workers" label="并发 worker" help="拉 HN item detail 时的并发数；过高可能不稳。" type="number" min="1" step="1" onConfigChange={onConfigChange} />
          </SettingsCard>
          <SettingsCard payload={payload} title="Product Hunt" source="product_hunt" note="PH GraphQL launches/posts。">
            <SettingCheckbox config={config} path="product_hunt.enabled" label="启用 Product Hunt" help="需要 PRODUCTHUNT_TOKEN；关闭后下一次 run 跳过。" onConfigChange={onConfigChange} />
            <SettingField config={config} path="product_hunt.first" label="请求 first" help="GraphQL 请求的 first 参数；PH 可能仍只返回实际可用数量。" type="number" min="1" step="1" onConfigChange={onConfigChange} />
          </SettingsCard>
          <SettingsCard payload={payload} title="HF Spaces" source="huggingface_trending" note="Hugging Face trending。Models/Datasets 仍采集但 dashboard 主 source 隐藏。">
            <SettingField config={config} path="huggingface.limit" label="每类上限" help="models / datasets / spaces 每类最多请求多少条。" type="number" min="1" step="1" onConfigChange={onConfigChange} />
          </SettingsCard>
          <SettingsCard payload={payload} title="npm Search" source="npm_search" note="按 Search Terms 里的 npm query 搜包。">
            <SettingCheckbox config={config} path="npm.enabled" label="启用 npm Search" help="关闭后下一次 run 跳过 npm。" onConfigChange={onConfigChange} />
            <SettingField config={config} path="npm.size" label="每个 query size" help="npm registry search 每个 query 请求数量。" type="number" min="1" step="1" onConfigChange={onConfigChange} />
          </SettingsCard>
          <SettingsCard payload={payload} title="PyPI Feeds" source="pypi_feeds" note="PyPI newest / updates RSS，并对前 N 条做 JSON enrich。">
            <SettingCheckbox config={config} path="pypi.enabled" label="启用 PyPI" help="关闭后下一次 run 跳过 PyPI feeds。" onConfigChange={onConfigChange} />
            <SettingField config={config} path="pypi.limit_per_feed" label="RSS 每 feed 上限" help="newest 和 updates 各保留多少条。" type="number" min="1" step="1" onConfigChange={onConfigChange} />
            <SettingField config={config} path="pypi.json_enrich_limit_per_feed" label="JSON enrich 上限" help="每个 feed 对前多少条请求 PyPI JSON 补 project_urls/classifiers。" type="number" min="0" step="1" onConfigChange={onConfigChange} />
          </SettingsCard>
          <SettingsCard payload={payload} title="Apify configured" source="apify_configured" note="付费 actor 防误跑 gate；真正执行还需要 APIFY_ENABLE_RUNS=true。">
            <SettingCheckbox config={config} path="apify.enabled" label="启用 Apify configured adapter" help="只打开 config 还不够；没有 APIFY_ENABLE_RUNS=true 时仍会拒绝付费 actor。" onConfigChange={onConfigChange} />
            <SettingField config={config} path="apify.max_results_per_run" label="run 最大结果" help="通用 Apify adapter 的每轮最大结果。" type="number" min="1" step="1" onConfigChange={onConfigChange} />
          </SettingsCard>
        </div>
      </section>
      <section className="settings-section">
        <h2>Source 状态</h2>
        <p className="section-copy">最近一次 snapshot 的 adapter 状态。正常只表示请求/解析没有报错，不代表 source 数据一定完整。</p>
        <div className="settings-table">
          {Object.entries(payload.source_errors || {}).map(([source, error]) => (
            <div className="status-row" key={source}>
              <strong>{source}</strong>
              <SourceHealthBadge payload={payload} source={source} />
              <div className={`message-line ${error ? 'warn' : 'good'}`}>{error || '正常'}</div>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}

function QueryEditor({ config, title, path, kind, copy, onConfigChange, replaceConfig }) {
  const list = getConfigValue(config, path, []) || [];
  function updateEntry(index, key, value) {
    const next = [...list];
    next[index] = kind === 'object' ? { ...(next[index] || {}), [key]: value } : value;
    replaceConfig(setConfigValue(config, path, next), '配置已修改，尚未保存。', 'warn');
  }
  return (
    <section className="settings-section">
      <h2>{title}</h2>
      <p className="section-copy">{copy}</p>
      <div className="settings-table">
        {list.length ? list.map((entry, index) => {
          const label = kind === 'object' ? (entry?.label || '') : `X keyword ${index + 1}`;
          const query = kind === 'object' ? (entry?.query || '') : String(entry || '');
          return (
            <div className="query-row" key={`${path}:${index}`}>
              <div>
                {kind === 'object' ? (
                  <input className="field" value={label} placeholder="label" onChange={(event) => updateEntry(index, 'label', event.target.value)} />
                ) : (
                  <div className="setting-label">{label}</div>
                )}
              </div>
              <textarea className="textarea" value={query} placeholder="query" onChange={(event) => updateEntry(index, 'query', event.target.value)} />
              <button
                type="button"
                className="small-button danger-button"
                onClick={() => {
                  const next = [...list];
                  next.splice(index, 1);
                  replaceConfig(setConfigValue(config, path, next), '已删除 query，保存后下一次 run 生效。', 'warn');
                }}
              >
                删除
              </button>
            </div>
          );
        }) : <div className="empty">还没有 query。</div>}
      </div>
      <div className="settings-section-actions">
        <button
          type="button"
          className="small-button"
          onClick={() => {
            const next = [...list, kind === 'object' ? { label: 'new', query: '' } : ''];
            replaceConfig(setConfigValue(config, path, next), '已新增 query，保存后下一次 run 生效。', 'warn');
          }}
        >
          新增 query
        </button>
      </div>
    </section>
  );
}

function SearchTermsSettings({ config, onConfigChange, replaceConfig }) {
  return (
    <>
      <QueryEditor config={config} title="GitHub 搜索词" path="github_search.queries" kind="object" copy="真实传给 GitHub Search API 的 repo query。想关注什么就直接加 query。" onConfigChange={onConfigChange} replaceConfig={replaceConfig} />
      <QueryEditor config={config} title="HN 搜索词" path="hn.algolia_queries" kind="object" copy="真实传给 HN Algolia search_by_date 的 query。每个 query 会按 24h / 7d / 30d 窗口抓。" onConfigChange={onConfigChange} replaceConfig={replaceConfig} />
      <QueryEditor config={config} title="npm 搜索词" path="npm.queries" kind="object" copy="真实传给 npm registry search 的 query。适合补充 package 生态里的工具信号。" onConfigChange={onConfigChange} replaceConfig={replaceConfig} />
      <QueryEditor config={config} title="X 关键词" path="apify.x_keyword_queries" kind="string" copy="预留给 X keyword/topic 抓取。当前 X 主信号仍是 seed accounts tweets。" onConfigChange={onConfigChange} replaceConfig={replaceConfig} />
    </>
  );
}

function XMonitoringSettings({ payload, config, onConfigChange, replaceConfig }) {
  const accounts = getConfigValue(config, 'apify.x_seed_accounts', []) || [];
  const selectedWindows = new Set(getConfigValue(config, 'apify.x_tweets.windows', []) || []);
  const [newAccount, setNewAccount] = useState('');
  function setWindows(nextWindows) {
    replaceConfig(setConfigValue(config, 'apify.x_tweets.windows', nextWindows), 'X 时间窗已修改，保存后下一次 run 生效。', 'warn');
  }
  return (
    <>
      <section className="settings-section">
        <h2>X 监控</h2>
        <p className="section-copy">X 先按 seed accounts 抓 tweets。这里弱化 engagement，重点是这些人提到了什么项目、原文怎么说。</p>
        <div className="settings-grid">
          <SettingsCard payload={payload} title="Tweet 抓取" source="x_tweets" note="下一次 X tweets run 使用这些参数。">
            <SettingCheckbox config={config} path="apify.x_tweets.enabled" label="启用 X tweets" help="关闭后 dashboard 不再从 x_tweets_latest.json 导入 tweets。" onConfigChange={onConfigChange} />
            <SettingField config={config} path="apify.x_tweets.accounts_limit" label="账号上限" help="最多从 seed accounts 里取多少个账号抓 tweets。" type="number" min="1" step="1" onConfigChange={onConfigChange} />
            <SettingField config={config} path="apify.x_tweets.max_tweets_per_account" label="每账号 tweet 上限" help="Apify 抓取时每个账号最多多少条；这个会影响成本。" type="number" min="1" step="1" onConfigChange={onConfigChange} />
            <SettingField config={config} path="apify.x_tweets.dashboard_tweet_limit" label="dashboard tweet 上限" help="导入 dashboard 的 tweet 总数上限，不等于实际 actor 抓取上限。" type="number" min="1" step="1" onConfigChange={onConfigChange} />
            <SettingCheckbox config={config} path="apify.x_tweets.use_since_date_filter" label="使用 actor sinceDate 过滤" help="默认关闭。该 actor 会先按每账号条数截断再做 date filter，容易把不活跃账号过滤成 0；关闭时由本地 tweet store 按窗口过滤。" onConfigChange={onConfigChange} />
            <div className="setting-row stack">
              <div className="setting-label">时间窗</div>
              <div className="settings-actions">
                {['24h', '7d', '30d', '30d+'].map((windowId) => (
                  <label className="toggle-row" key={windowId}>
                    <input
                      type="checkbox"
                      checked={selectedWindows.has(windowId)}
                      onChange={(event) => {
                        const next = new Set(selectedWindows);
                        if (event.target.checked) next.add(windowId);
                        else next.delete(windowId);
                        setWindows([...next]);
                      }}
                    />
                    <span>{windowId}</span>
                  </label>
                ))}
              </div>
              <div className="setting-help">用于给 tweet 标 24h / 7d / 30d / 30d+ 窗口；下一次 run 生效。</div>
            </div>
            <SettingCheckbox config={config} path="apify.x_tweets.include_retweets" label="包含 retweets" help="打开后 retweet 也会进入抓取/导入。" onConfigChange={onConfigChange} />
            <SettingCheckbox config={config} path="apify.x_tweets.include_replies" label="包含 replies" help="打开后 replies 也会进入抓取/导入，噪声通常更高。" onConfigChange={onConfigChange} />
          </SettingsCard>
          <SettingsCard payload={payload} title="Seed 发现" source="x_seed_accounts" note="从 following 候选池筛 AI 相关个人账号。">
            <SettingCheckbox config={config} path="apify.x_seed_from_following.enabled" label="启用 following seed file" help="从 x_following_ai_seed_candidates_latest.json 读取候选并筛个人账号。" onConfigChange={onConfigChange} />
            <SettingField config={config} path="apify.x_seed_from_following.limit" label="候选展示上限" help="Settings 里 X Accounts 的候选数量上限。" type="number" min="1" step="1" onConfigChange={onConfigChange} />
            <SettingSelect config={config} path="apify.x_seed_from_following.sort" label="候选排序" help="当前按 followers_count 筛前 N。" options={['followers_count', 'keyword_score', 'following_count']} onConfigChange={onConfigChange} />
          </SettingsCard>
        </div>
      </section>
      <section className="settings-section">
        <h2>Seed 账号</h2>
        <p className="section-copy">手动维护的账号池。这里只放个人账号，不放 official accounts。保存后下一次 X tweets run 生效。</p>
        <div className="setting-row two">
          <div>
            <div className="setting-label">新增账号</div>
            <div className="setting-help">输入 handle，不需要 @。</div>
          </div>
          <div className="settings-actions">
            <input className="field" value={newAccount} placeholder="karpathy" onChange={(event) => setNewAccount(event.target.value)} />
            <button
              type="button"
              className="small-button"
              onClick={() => {
                const handle = newAccount.trim().replace(/^@/, '');
                if (!handle) return;
                const next = accounts.includes(handle) ? accounts : [...accounts, handle];
                replaceConfig(setConfigValue(config, 'apify.x_seed_accounts', next), `已加入 @${handle}，保存后下一次 X run 生效。`, 'warn');
                setNewAccount('');
              }}
            >
              添加
            </button>
          </div>
        </div>
        <div className="settings-table seed-account-table">
          {accounts.length ? accounts.map((account, index) => (
            <div className="account-row" key={`${account}:${index}`}>
              <div>
                <Handle value={account} items={payload.items || []} />
                <div className="setting-help">apify.x_seed_accounts[{index}]</div>
              </div>
              <button
                type="button"
                className="small-button danger-button"
                onClick={() => {
                  const next = [...accounts];
                  next.splice(index, 1);
                  replaceConfig(setConfigValue(config, 'apify.x_seed_accounts', next), '已移除账号，保存后下一次 X run 生效。', 'warn');
                }}
              >
                移除
              </button>
            </div>
          )) : <div className="empty">还没有 seed account。</div>}
        </div>
      </section>
    </>
  );
}

function DisplaySettings({ payload, pageSize, onPageSizeChange, theme, onThemeChange, hiddenSources, onHiddenSourcesChange }) {
  return (
    <>
      <section className="settings-section">
        <h2>显示设置</h2>
        <p className="section-copy">这些是浏览器本地显示偏好，存在 localStorage，不写入 pipeline/config.json。</p>
        <div className="settings-grid">
          <div className="settings-card">
            <div className="settings-card-title">默认分页</div>
            <div className="settings-card-note">新打开 tab 时使用的 page size。</div>
            <select className="select settings-card-control" value={pageSize} onChange={(event) => onPageSizeChange(Number(event.target.value))}>
              {PAGE_SIZES.map((size) => <option key={size} value={size}>{size}/页</option>)}
            </select>
          </div>
          <div className="settings-card">
            <div className="settings-card-title">Theme</div>
            <div className="settings-card-note">只影响本浏览器显示，设置存在 localStorage。</div>
            <div className="settings-actions settings-card-control">
              {['light', 'dark'].map((mode) => (
                <button type="button" key={mode} className={`control-button ${theme === mode ? 'active' : ''}`} onClick={() => onThemeChange(mode)}>
                  {mode === 'light' ? '浅色' : '深色'}
                </button>
              ))}
            </div>
          </div>
        </div>
      </section>
      <section className="settings-section">
        <h2>Source 标签显示</h2>
        <p className="section-copy">隐藏 source tab 只是 UI 偏好；不会删除 dashboard payload、数据库或采集配置。</p>
        <div className="settings-grid">
          {(payload.channels || []).map((channel) => (
            <label className="toggle-row settings-card compact" key={channel.id}>
              <input
                type="checkbox"
                checked={!hiddenSources.has(channel.id)}
                onChange={(event) => {
                  const next = new Set(hiddenSources);
                  if (event.target.checked) next.delete(channel.id);
                  else next.add(channel.id);
                  onHiddenSourcesChange(next);
                }}
              />
              <span>
                <strong>{channel.label}</strong>
                <div className="setting-help">{formatNumber(channel.count)} rows · 只影响本浏览器显示，不改数据库和 pipeline。</div>
              </span>
            </label>
          ))}
        </div>
      </section>
    </>
  );
}

function ApiStatusSettings({ payload }) {
  const rows = Object.values(payload.config_meta?.api_status || {});
  return (
    <section className="settings-section">
      <h2>API 状态</h2>
      <p className="section-copy">这里只显示环境变量是否配置，不显示 token 明文。Apify paid actor 还额外受 APIFY_ENABLE_RUNS gate 控制。</p>
      <div className="settings-table">
        {rows.map((row) => (
          <div className="status-row" key={`${row.label}:${row.env}`}>
            <strong>{row.label}</strong>
            <span className={`status-dot ${row.configured ? 'ok' : 'warn'}`}>{row.configured ? 'configured' : 'missing/off'}</span>
            <div>
              <code>{row.env}</code>
              <div className="setting-help">{row.note}</div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function SettingsView({ payload, state, settings }) {
  const panels = settingsPanelDefs({ ...payload, config: settings.config });
  const activeSettings = panels.some((panel) => panel.id === state.activeSettings) ? state.activeSettings : panels[0]?.id || '';
  const panel = panels.find((row) => row.id === activeSettings);
  const body = activeSettings === 'settings_search_terms'
    ? <SearchTermsSettings config={settings.config} onConfigChange={settings.updateConfig} replaceConfig={settings.replaceConfig} />
    : activeSettings === 'settings_x_monitoring'
      ? <XMonitoringSettings payload={payload} config={settings.config} onConfigChange={settings.updateConfig} replaceConfig={settings.replaceConfig} />
      : activeSettings === 'settings_display'
        ? (
          <DisplaySettings
            payload={payload}
            pageSize={settings.pageSize}
            onPageSizeChange={settings.onPageSizeChange}
            theme={settings.theme}
            onThemeChange={settings.onThemeChange}
            hiddenSources={settings.hiddenSources}
            onHiddenSourcesChange={settings.onHiddenSourcesChange}
          />
        )
        : activeSettings === 'settings_api_status'
          ? <ApiStatusSettings payload={payload} />
          : <RunSourcesSettings payload={payload} config={settings.config} onConfigChange={settings.updateConfig} />;
  return (
    <section className="settings-panel">
      <section className="status-list">
        <div className="settings-note">
          <strong>Settings 是控制面板，不是项目榜。</strong>
          {' '}配置改动的目标文件是 <code>pipeline/config.json</code>；确认后下一次 pipeline run 生效。默认节奏先按每 24 小时一轮设计，cron 暂不启用。
        </div>
      </section>
      <SettingsToolbar
        panel={panel}
        configDirty={settings.configDirty}
        configBusy={settings.configBusy}
        message={settings.configMessage}
        messageKind={settings.configMessageKind}
        onSave={settings.onSaveConfig}
        onReload={settings.onReloadConfig}
        onRun={settings.onRunPipeline}
      />
      {body}
    </section>
  );
}

function DailyFeedView({ payload, onOpenSource }) {
  const feed = normalizeFeedPayload(payload.feed || {});
  const emptyState = feedEmptyState(feed);
  if (emptyState) {
    return (
      <section className="daily-feed-shell">
        <div className="feed-run-strip empty-run">
          <div>
            <strong>每日 Feed</strong>
            <span>{emptyState === 'missing' ? '还没有 Layer 2 run' : '当前 run 没有 scored items'}</span>
          </div>
          <Lightning size={18} weight="duotone" aria-hidden="true" />
        </div>
      </section>
    );
  }
  const summary = feedRunSummary(feed);
  return (
    <section className="daily-feed-shell">
      <div className="feed-run-strip">
        <div>
          <strong>{summary.run}</strong>
          <span>{summary.decision}</span>
        </div>
        <div className="feed-run-meta">
          <span>{summary.generated}</span>
          <span>{summary.models}</span>
          <span>pending scout {feed.pending?.edge_watch_scout || 0} · deepdive {feed.pending?.deepdive || 0}</span>
        </div>
      </div>
      <section className="today-focus-grid" aria-label="Today Focus">
        {feed.today_focus.map((item) => (
          <FeedSignalCard key={item.group_id} item={item} onOpenSource={onOpenSource} />
        ))}
      </section>
      <section className="scored-feed-list" aria-label="Scored Feed">
        {feed.scored_list.map((item, index) => (
          <ScoredFeedRow key={item.group_id} item={{ ...item, rank: item.rank || index + 1 }} onOpenSource={onOpenSource} />
        ))}
      </section>
    </section>
  );
}

function FeedSignalCard({ item, onOpenSource }) {
  const tone = scoreTone(item.l2_score);
  return (
    <article className={`feed-signal-card ${tone}`}>
      <div className="signal-card-topline">
        <span className="score-rail">{Math.round(item.l2_score)}</span>
        <span className="signal-reason">{item.primary_reason}</span>
        <Sparkle size={16} weight="duotone" aria-hidden="true" />
      </div>
      <h2>{item.title}</h2>
      <p>{item.rationale_short || item.context_preview}</p>
      <div className="feed-tags">
        {(item.topic_tags || []).slice(0, 4).map((tag) => <span key={tag}>{tag}</span>)}
      </div>
      {item.deepdive ? <p className="deepdive-summary">{item.deepdive.summary}</p> : null}
      <FeedEvidence item={item} />
      <FeedLinks item={item} onOpenSource={onOpenSource} />
      <div className="feed-feedback" aria-label="Feed feedback">
        <button type="button" title="有用" aria-label="有用"><ThumbsUp size={16} /></button>
        <button type="button" title="没用" aria-label="没用"><ThumbsDown size={16} /></button>
      </div>
    </article>
  );
}

function ScoredFeedRow({ item, onOpenSource }) {
  return (
    <article className={`scored-feed-row ${scoreTone(item.l2_score)}`}>
      <span className="score-rail small">{Math.round(item.l2_score)}</span>
      <div className="scored-feed-main">
        <strong>{item.title}</strong>
        <p>{item.rationale_short || item.context_preview}</p>
      </div>
      <span className="signal-reason">{item.primary_reason}</span>
      <FeedLinks item={item} onOpenSource={onOpenSource} />
    </article>
  );
}

function FeedEvidence({ item }) {
  const bullets = item.evidence_bullets || [];
  return (
    <div className="feed-evidence">
      {bullets.slice(0, 3).map((bullet) => (
        <span key={`${item.group_id}:${bullet.display_label || bullet.label}`}>
          {bullet.display_label || bullet.label}
        </span>
      ))}
      {bullets.length > 3 ? <span>+{bullets.length - 3}</span> : null}
    </div>
  );
}

function FeedLinks({ item, onOpenSource }) {
  const links = item.source_links || [];
  return (
    <div className="feed-links">
      {item.canonical_link ? (
        <a href={item.canonical_link} target="_blank" rel="noreferrer">
          <ArrowSquareOut size={15} aria-hidden="true" /> 打开
        </a>
      ) : null}
      {links.slice(0, 3).map((link) => (
        <button
          type="button"
          key={`${item.group_id}:${link.item_id}:${link.channel}`}
          onClick={() => onOpenSource?.(link)}
        >
          <ChartLineUp size={15} aria-hidden="true" /> {link.channel_label}
        </button>
      ))}
    </div>
  );
}

function FeedView({ payload, tab = 'daily', onTabChange, onOpenSource }) {
  const [levelFilter, setLevelFilter] = useState('all');
  const [sourceFilters, setSourceFilters] = useState([]);
  const [expandedEvidenceIds, setExpandedEvidenceIds] = useState(() => new Set());
  const [columnWidths, setColumnWidths] = useState(() => readColumnWidths('candidate_pool'));
  const columns = candidateTableColumns();
  const rows = useMemo(() => candidateRowsForFeed(payload.candidates), [payload.candidates]);
  const sourceOptions = useMemo(() => candidateSourceOptions(rows), [rows]);
  const filteredRows = useMemo(
    () => filterCandidateRows(rows, { levelFilter, sourceFilters }),
    [rows, levelFilter, sourceFilters],
  );
  const toggleSourceFilter = (source) => {
    setSourceFilters((current) => (
      current.includes(source)
        ? current.filter((value) => value !== source)
        : [...current, source]
    ));
  };
  const toggleEvidence = (entityId) => {
    setExpandedEvidenceIds((current) => {
      const next = new Set(current);
      if (next.has(entityId)) next.delete(entityId);
      else next.add(entityId);
      return next;
    });
  };
  function startColumnResize(event, index) {
    event.preventDefault();
    event.stopPropagation();
    const th = event.currentTarget.closest('th');
    if (!th) return;
    const startX = event.clientX;
    const startWidth = th.getBoundingClientRect().width;
    th.classList.add('is-resizing');
    document.body.classList.add('resizing-columns');
    const onMove = (moveEvent) => {
      const nextWidth = Math.max(56, Math.round(startWidth + moveEvent.clientX - startX));
      setColumnWidths((current) => {
        const next = { ...(current || {}), [index]: nextWidth };
        writeColumnWidths('candidate_pool', next);
        return next;
      });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      th.classList.remove('is-resizing');
      document.body.classList.remove('resizing-columns');
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp, { once: true });
  }
  return (
    <>
      <section className="channel-tabs feed-tabs" aria-label="Feed views">
        <button type="button" className={tab === 'daily' ? 'active' : ''} onClick={() => onTabChange?.('daily')}>每日 Feed</button>
        <button type="button" className={tab === 'pool' ? 'active' : ''} onClick={() => onTabChange?.('pool')}>候选池</button>
      </section>
      {tab === 'daily' ? (
        <DailyFeedView payload={payload} onOpenSource={onOpenSource} />
      ) : (
        <section className="settings-panel candidate-panel">
          <section className="settings-toolbar">
            <div>
              <div className="title">候选池</div>
              <div className="copy">Dynamic candidates from /api/dashboard-data · run {payload.candidates?.run_id || payload.run_id || 'unknown'}</div>
            </div>
            <div className="settings-actions">
              <select className="select compact-select" value={levelFilter} onChange={(event) => setLevelFilter(event.target.value)}>
                <option value="all">全部重要性</option>
                <option value="high_potential">高潜力</option>
                <option value="potential">潜力</option>
                <option value="edge_watch">观察</option>
              </select>
              <div className="candidate-source-filters" aria-label="Candidate source filters">
                <button
                  type="button"
                  className={!sourceFilters.length ? 'active' : ''}
                  onClick={() => setSourceFilters([])}
                >
                  全部来源
                </button>
                {sourceOptions.map((source) => (
                  <button
                    type="button"
                    key={source.value}
                    className={sourceFilters.includes(source.value) ? 'active' : ''}
                    aria-pressed={sourceFilters.includes(source.value)}
                    onClick={() => toggleSourceFilter(source.value)}
                  >
                    {source.label}
                    <span>{source.count}</span>
                  </button>
                ))}
              </div>
            </div>
          </section>
          <div className="table-wrap">
            <table className="candidate-table">
              <thead>
                <tr>
                  {columns.map((column, index) => (
                    <th
                      key={column.label}
                      className={column.cls || ''}
                      data-col-index={index}
                      style={columnWidthStyle(columnWidths, index)}
                    >
                      <span className="th-inner">
                        <span className="th-label">{column.label}</span>
                      </span>
                      <span
                        className="col-resizer"
                        data-col-index={index}
                        title="拖动调整列宽"
                        aria-hidden="true"
                        onPointerDown={(event) => startColumnResize(event, index)}
                        onClick={(event) => {
                          event.preventDefault();
                          event.stopPropagation();
                        }}
                      />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredRows.length ? filteredRows.map((row) => {
                  const evidence = candidateVisibleEvidence(row, expandedEvidenceIds.has(row.entity_id));
                  return (
                    <tr key={`${row.pool_type}:${row.entity_id}`}>
                      <td className="candidate-name-cell" style={columnWidthStyle(columnWidths, 0)}>
                        <strong>{row.canonical_entity || row.entity_id}</strong>
                        <code>{row.canonical_key || row.entity_id}</code>
                      </td>
                      <td style={columnWidthStyle(columnWidths, 1)}><span className={`badge ${row.level}`}>{levelLabel(row.level)}</span></td>
                      <td style={columnWidthStyle(columnWidths, 2)}>
                        <div className="evidence-list">
                          {evidence.bullets.map((bullet) => (
                            <span className="evidence-pill" title={bullet.label} key={`${row.entity_id}:${bullet.label}:${bullet.origin_type}`}>
                              {bullet.display_label || bullet.label}
                              {(bullet.display_badge || bullet.provenance_badge) ? <small>{bullet.display_badge || bullet.provenance_badge}</small> : null}
                            </span>
                          ))}
                          {evidence.extraCount > 0 ? (
                            <button
                              type="button"
                              className="evidence-more"
                              aria-expanded={false}
                              onClick={() => toggleEvidence(row.entity_id)}
                            >
                              +{evidence.extraCount}
                            </button>
                          ) : null}
                          {evidence.expandable && evidence.extraCount === 0 ? (
                            <button
                              type="button"
                              className="evidence-more"
                              aria-expanded
                              onClick={() => toggleEvidence(row.entity_id)}
                            >
                              收起
                            </button>
                          ) : null}
                        </div>
                      </td>
                      <td style={columnWidthStyle(columnWidths, 3)}>
                        <div className="candidate-source-list">
                          {(row.source_links || []).slice(0, 4).map((sourceLink) => (
                            <button
                              type="button"
                              className="candidate-source-chip"
                              key={`${row.entity_id}:${sourceLink.ref || sourceLink.channel}:${sourceLink.item_id}`}
                              title={sourceLink.name || sourceLink.external_url || sourceLink.channel_label}
                              onClick={() => onOpenSource?.(sourceLink)}
                            >
                              {sourceChipLabel(sourceLink)}
                            </button>
                          ))}
                          {Math.max(0, Number(row.source_link_count || 0) - (row.source_links || []).slice(0, 4).length) > 0 ? (
                            <span className="candidate-source-more">
                              +{Math.max(0, Number(row.source_link_count || 0) - (row.source_links || []).slice(0, 4).length)}
                            </span>
                          ) : null}
                          {!(row.source_links || []).length ? <span className="muted">暂无内部来源</span> : null}
                        </div>
                      </td>
                      <td style={columnWidthStyle(columnWidths, 4)}>
                        {row.canonical_link ? (
                          <a className="candidate-link" href={row.canonical_link} target="_blank" rel="noreferrer">
                            打开
                          </a>
                        ) : (
                          <span className="muted">{row.binding_confidence === 'weak' ? '弱绑定' : '暂无链接'}</span>
                        )}
                      </td>
                      <td className="candidate-context" style={columnWidthStyle(columnWidths, 5)}>{row.context_preview || row.first_trigger_at || row.status || ''}</td>
                    </tr>
                  );
                }) : (
                  <tr><td colSpan={columns.length}><div className="empty">Candidate Pool 当前没有数据。</div></td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}

function WorkspaceIcon({ name: iconName }) {
  if (iconName === 'search') {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <circle cx="8.5" cy="8.5" r="5.5" />
        <path d="M13 13l4 4" />
      </svg>
    );
  }
  if (iconName === 'feed') {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <path d="M5 5h10" />
        <path d="M5 10h10" />
        <path d="M5 15h7" />
      </svg>
    );
  }
  if (iconName === 'database') {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <ellipse cx="10" cy="5" rx="6" ry="2.5" />
        <path d="M4 5v7c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5V5" />
        <path d="M4 8.5c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="3" />
      <path d="M10 2v3" />
      <path d="M10 15v3" />
      <path d="M2 10h3" />
      <path d="M15 10h3" />
      <path d="M4.3 4.3l2.1 2.1" />
      <path d="M13.6 13.6l2.1 2.1" />
      <path d="M15.7 4.3l-2.1 2.1" />
      <path d="M6.4 13.6l-2.1 2.1" />
    </svg>
  );
}

function App() {
  const [payload, setPayload] = useState(null);
  const [state, setState] = useState(null);
  const [error, setError] = useState('');
  const [railCollapsed, setRailCollapsed] = useState(() => localStorage.getItem('heroRadarRail') === 'collapsed');
  const [theme, setTheme] = useState(() => localStorage.getItem('heroRadarTheme') || 'light');
  const [hiddenSources, setHiddenSources] = useState(() => new Set(readLocalJson('heroRadarHiddenSources', [])));
  const [runtimeConfig, setRuntimeConfig] = useState({});
  const [savedConfigText, setSavedConfigText] = useState('{}');
  const [configMessage, setConfigMessage] = useState('');
  const [configMessageKind, setConfigMessageKind] = useState('');
  const [configBusy, setConfigBusy] = useState(false);

  useEffect(() => {
    fetch(dashboardApiUrl('/api/dashboard-data', API_BASE))
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((data) => {
        const initial = initialDashboardState(data);
        const urlState = readAppUrlState();
        const storedSection = localStorage.getItem('heroRadarSection');
        const storedNormalizedSection = storedSection === 'source' ? 'sources' : storedSection === 'setting' ? 'settings' : storedSection;
        const section = urlState.section || (urlState.activeChannel ? 'sources' : storedNormalizedSection);
        const storedSourceTab = localStorage.getItem('heroRadarSourceTab');
        const activeChannel = (data.channels || []).some((channel) => channel.id === urlState.activeChannel)
          ? urlState.activeChannel
          : (data.channels || []).some((channel) => channel.id === storedSourceTab)
          ? storedSourceTab
          : initial.activeChannel;
        const panels = settingsPanelDefs(data);
        const storedSettingsTab = localStorage.getItem('heroRadarSettingsTab');
        const activeSettings = panels.some((panel) => panel.id === urlState.activeSettings)
          ? urlState.activeSettings
          : panels.some((panel) => panel.id === storedSettingsTab)
          ? storedSettingsTab
          : panels[0]?.id || initial.activeSettings;
        const feedTab = urlState.feedTab || (localStorage.getItem('heroRadarFeedTab') === 'pool' ? 'pool' : 'daily');
        const activeRangeChannel = section === 'settings' ? activeSettings : activeChannel;
        const rangeIds = availableRanges(data.items || [], activeRangeChannel).map((range) => range.id);
        const activeWindow = rangeIds.includes(urlState.activeWindow)
          ? urlState.activeWindow
          : defaultRangeId(data.items || [], activeRangeChannel);
        const config = cloneJson(data.config || {});
        setPayload(data);
        setRuntimeConfig(config);
        setSavedConfigText(JSON.stringify(config));
        setState({
          ...initial,
          section: workspaceSections().some((row) => row.id === section && row.enabled) ? section : initial.section,
          activeChannel,
          activeSettings,
          feedTab,
          page: 1,
          pageSize: storedPageSize(),
          activeWindow,
          sortDir: 'asc',
          selectedItemId: urlState.selectedItemId,
        });
      })
      .catch((err) => setError(String(err.message || err)));
  }, []);

  useEffect(() => {
    localStorage.setItem('heroRadarRail', railCollapsed ? 'collapsed' : 'expanded');
  }, [railCollapsed]);

  useEffect(() => {
    localStorage.setItem('heroRadarTheme', theme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem('heroRadarHiddenSources', JSON.stringify([...hiddenSources]));
  }, [hiddenSources]);

  useEffect(() => {
    if (!payload) return undefined;
    const onPopState = () => {
      const urlState = readAppUrlState();
      setState((current) => {
        if (!current) return current;
        const next = { ...current };
        if (urlState.section) next.section = urlState.section;
        if (urlState.feedTab) next.feedTab = urlState.feedTab;
        if ((payload.channels || []).some((channel) => channel.id === urlState.activeChannel)) {
          next.activeChannel = urlState.activeChannel;
        }
        if (settingsPanelDefs(payload).some((panel) => panel.id === urlState.activeSettings)) {
          next.activeSettings = urlState.activeSettings;
        }
        next.selectedItemId = urlState.selectedItemId;
        const activeRangeChannel = next.section === 'settings' ? next.activeSettings : next.activeChannel;
        const ranges = availableRanges(payload.items || [], activeRangeChannel);
        next.activeWindow = ranges.some((range) => range.id === urlState.activeWindow)
          ? urlState.activeWindow
          : defaultRangeId(payload.items || [], activeRangeChannel);
        next.page = 1;
        if (next.section === 'sources' && next.selectedItemId != null) {
          const navState = sourceItemNavigationState(
            payload.items || [],
            { item_id: next.selectedItemId, channel: next.activeChannel, window: next.activeWindow },
            next,
          );
          if (navState) {
            next.activeWindow = navState.activeWindow;
            next.page = navState.page;
          }
        }
        return next;
      });
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, [payload]);

  function patchState(patch, options = {}) {
    setState((current) => {
      const next = { ...(current || {}), ...patch };
      if (options.history) writeAppHistory(next, options.history);
      return next;
    });
  }

  function markConfigMessage(message, kind = 'warn') {
    setConfigMessage(message);
    setConfigMessageKind(kind);
  }

  function updateConfig(path, value) {
    setRuntimeConfig((current) => setConfigValue(current, path, value));
    markConfigMessage('配置已修改，尚未保存。', 'warn');
  }

  function replaceConfig(nextConfig, message, kind = 'warn') {
    setRuntimeConfig(nextConfig);
    markConfigMessage(message, kind);
  }

  async function reloadConfigFromApi() {
    if (configBusy) return;
    setConfigBusy(true);
    try {
      const response = await fetch(dashboardApiUrl('/api/config', API_BASE), { cache: 'no-store' });
      const data = await response.json();
      if (!response.ok || !data.config) throw new Error(data.error || `HTTP ${response.status}`);
      const config = cloneJson(data.config);
      setRuntimeConfig(config);
      setSavedConfigText(JSON.stringify(config));
      markConfigMessage('已从 /api/config 重载。', 'good');
    } catch (err) {
      markConfigMessage(`重载失败：${String(err.message || err)}`, 'warn');
    } finally {
      setConfigBusy(false);
    }
  }

  async function saveConfig() {
    if (configBusy) return;
    setConfigBusy(true);
    markConfigMessage('正在保存 pipeline/config.json...', '');
    try {
      const response = await fetch(dashboardApiUrl('/api/config', API_BASE), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config: runtimeConfig }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
      setSavedConfigText(JSON.stringify(runtimeConfig));
      markConfigMessage(`已保存；backup: ${data.backup_path || 'created'}。下一次 run 生效。`, 'good');
    } catch (err) {
      markConfigMessage(`保存失败：${String(err.message || err)}`, 'warn');
    } finally {
      setConfigBusy(false);
    }
  }

  async function runPipelineNow() {
    if (configBusy || JSON.stringify(runtimeConfig || {}) !== savedConfigText) return;
    setConfigBusy(true);
    markConfigMessage('Pipeline 正在运行；完成后会刷新 dashboard。', '');
    try {
      const response = await fetch(dashboardApiUrl('/api/run', API_BASE), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.stderr || data.error || `HTTP ${response.status}`);
      markConfigMessage('Pipeline 完成，正在刷新 dashboard。', 'good');
      window.location.reload();
    } catch (err) {
      markConfigMessage(`Run failed：${String(err.message || err).slice(0, 800)}`, 'warn');
      setConfigBusy(false);
    }
  }

  function updateHiddenSources(nextHiddenSources) {
    setHiddenSources(nextHiddenSources);
    markConfigMessage('Source tab 显示偏好已更新；这不影响采集。', 'good');
    if (state?.activeChannel && nextHiddenSources.has(state.activeChannel)) {
      const nextChannel = (payload?.channels || []).find((channel) => !nextHiddenSources.has(channel.id));
      if (nextChannel) patchState({ activeChannel: nextChannel.id, activeWindow: defaultRangeId(payload.items || [], nextChannel.id), page: 1 });
    }
  }

  function updatePageSize(size) {
    localStorage.setItem('heroRadarDefaultPageSize', String(size));
    patchState({ pageSize: size, page: 1 });
    markConfigMessage(`默认分页已改为 ${size}/页。`, 'good');
  }

  function updateFeedTab(nextTab) {
    localStorage.setItem('heroRadarFeedTab', nextTab);
    patchState({ section: 'feed', feedTab: nextTab, page: 1 }, { history: 'push' });
  }

  function openCandidateSource(sourceLink) {
    if (!payload || !state) return;
    const navState = sourceItemNavigationState(payload.items || [], sourceLink, state);
    if (!navState) return;
    const previousFeedState = {
      ...state,
      section: 'feed',
      feedTab: 'pool',
      selectedItemId: null,
    };
    const nextState = {
      ...state,
      ...navState,
    };
    localStorage.setItem('heroRadarFeedTab', 'pool');
    localStorage.setItem('heroRadarSection', 'sources');
    localStorage.setItem('heroRadarSourceTab', navState.activeChannel);
    writeAppHistory(previousFeedState, 'replace');
    writeAppHistory(nextState, 'push');
    setState(nextState);
  }

  const sections = workspaceSections();
  const activeSection = state?.section || 'sources';
  const settingsChannels = payload ? settingsPanelDefs({ ...payload, config: runtimeConfig }) : [];
  const activeSettings = state && settingsChannels.some((channel) => channel.id === state.activeSettings)
    ? state.activeSettings
    : settingsChannels[0]?.id || '';
  const configDirty = JSON.stringify(runtimeConfig || {}) !== savedConfigText;

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
                onClick={() => {
                  if (!section.enabled) return;
                  localStorage.setItem('heroRadarSection', section.id);
                  patchState({ section: section.id, page: 1, selectedItemId: null }, { history: 'push' });
                }}
              >
                <span className="nav-icon" aria-hidden="true"><WorkspaceIcon name={section.icon} /></span>
                <span className="full">{section.label}</span>
              </button>
            ))}
          </nav>
        </aside>

        {payload && activeSection === 'settings' ? (
          <SettingsSubrail
            channels={settingsChannels}
            activeSettings={activeSettings}
            onSelect={(id) => {
              localStorage.setItem('heroRadarSettingsTab', id);
              patchState({ activeSettings: id, page: 1, selectedItemId: null }, { history: 'push' });
            }}
          />
        ) : null}

        <div className="workspace">
          <main>
            {error ? <div className="error visible">Failed to load dashboard data: {error}</div> : null}
            {!payload || !state ? <div className="empty">Loading dashboard data from {dashboardApiUrl('/api/dashboard-data', API_BASE)}...</div> : null}
            {payload && state && activeSection === 'sources' ? <SourcesView payload={payload} state={state} onStateChange={patchState} hiddenSources={hiddenSources} /> : null}
            {payload && state && activeSection === 'settings' ? (
              <SettingsView
                payload={payload}
                state={{ ...state, activeSettings }}
                settings={{
                  config: runtimeConfig,
                  updateConfig,
                  replaceConfig,
                  configDirty,
                  configBusy,
                  configMessage,
                  configMessageKind,
                  onSaveConfig: saveConfig,
                  onReloadConfig: reloadConfigFromApi,
                  onRunPipeline: runPipelineNow,
                  pageSize: state.pageSize,
                  onPageSizeChange: updatePageSize,
                  theme,
                  onThemeChange: (mode) => {
                    setTheme(mode);
                    markConfigMessage(`显示主题已切换为 ${mode === 'dark' ? '深色' : '浅色'}。`, 'good');
                  },
                  hiddenSources,
                  onHiddenSourcesChange: updateHiddenSources,
                }}
              />
            ) : null}
            {payload && state && activeSection === 'feed' ? (
              <FeedView
                payload={payload}
                tab={state.feedTab || 'daily'}
                onTabChange={updateFeedTab}
                onOpenSource={openCandidateSource}
              />
            ) : null}
          </main>
        </div>
      </div>
    </div>
  );
}

export default App;
