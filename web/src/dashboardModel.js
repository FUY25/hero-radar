const WINDOW_ORDER = new Map([
  ['24h', 0],
  ['7d', 1],
  ['30d', 2],
  ['30d+', 3],
  ['7d+30d+60d', 4],
  ['current', 5],
]);

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

export function detailRowsForItem(item) {
  if (!item) return [];
  const rows = [];
  for (const [key, value] of Object.entries(item.metadata || {})) {
    rows.push({ key: `metadata.${key}`, value });
  }
  rows.push({ key: 'raw', value: item.raw || {} });
  return rows;
}
