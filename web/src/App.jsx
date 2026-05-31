import React, { useEffect, useMemo, useState } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8787';

function levelLabel(level) {
  if (level === 'high_potential') return 'High Potential';
  if (level === 'potential') return 'Potential';
  if (level === 'edge_watch') return 'Edge Watch';
  return level || 'Unknown';
}

function App() {
  const [view, setView] = useState('daily');
  const [payload, setPayload] = useState({ run_id: '', candidates: [], edge_watch: [] });
  const [levelFilter, setLevelFilter] = useState('all');
  const [error, setError] = useState('');

  useEffect(() => {
    fetch(`${API_BASE}/api/candidates`)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((data) => setPayload(data))
      .catch((err) => setError(String(err.message || err)));
  }, []);

  const candidateRows = useMemo(() => {
    const base = [
      ...(payload.candidates || []).map((row) => ({ ...row, pool_type: row.level })),
      ...(payload.edge_watch || []).map((row) => ({ ...row, level: 'edge_watch', pool_type: 'edge_watch' })),
    ];
    if (levelFilter === 'all') return base;
    return base.filter((row) => row.level === levelFilter);
  }, [payload, levelFilter]);

  return (
    <main className="app-shell">
      <aside className="rail">
        <div className="brand">HR</div>
        <button className="nav-button" disabled title="Layer 3, not in this slice">Explore</button>
        <button className="nav-button active">Feed</button>
        <button className="nav-button" disabled title="raw source dashboard">Sources</button>
        <button className="nav-button" disabled>Settings</button>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>Hero Radar</h1>
            <p>Run {payload.run_id || 'not loaded'}</p>
          </div>
        </header>

        <div className="inner-tabs">
          <button className={view === 'daily' ? 'active' : ''} onClick={() => setView('daily')}>
            Daily Feed
          </button>
          <button className={view === 'pool' ? 'active' : ''} onClick={() => setView('pool')}>
            Candidate Pool
          </button>
        </div>

        {error ? <div className="error">Failed to load candidates: {error}</div> : null}

        {view === 'daily' ? (
          <section className="empty-state">
            <h2>Daily Feed is not built in this slice.</h2>
            <p>Layer 2 will select cards for today_focus, secondary, backlog, and suppress. Use Candidate Pool to inspect the Layer 1 output.</p>
          </section>
        ) : (
          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>Candidate Pool</h2>
                <p>Transparent pre-Layer2 output: Potential, High Potential, and Edge Watch.</p>
              </div>
              <select value={levelFilter} onChange={(event) => setLevelFilter(event.target.value)}>
                <option value="all">All levels</option>
                <option value="high_potential">High Potential</option>
                <option value="potential">Potential</option>
                <option value="edge_watch">Edge Watch</option>
              </select>
            </div>

            <table>
              <thead>
                <tr>
                  <th>Entity</th>
                  <th>Level</th>
                  <th>Signals</th>
                  <th>First trigger</th>
                </tr>
              </thead>
              <tbody>
                {candidateRows.map((row) => (
                  <tr key={`${row.pool_type}:${row.entity_id}`}>
                    <td>
                      <strong>{row.canonical_entity || row.entity_id}</strong>
                      <code>{row.entity_id}</code>
                    </td>
                    <td><span className={`badge ${row.level}`}>{levelLabel(row.level)}</span></td>
                    <td>{(row.fired_families || row.reasons || []).join(', ')}</td>
                    <td>{row.first_trigger_at || ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}
      </section>
    </main>
  );
}

export default App;
