import assert from 'node:assert/strict';
import test from 'node:test';
import {
  activeChannelList,
  detailRowsForItem,
  filterAndSortRows,
  initialDashboardState,
  visibleWindowsForChannel,
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
