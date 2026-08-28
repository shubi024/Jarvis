import React, { useState, useEffect, useCallback } from 'react';
import { Zap, Database, Cloud, ShieldCheck } from 'lucide-react';

const API_BASE = ''; // same-origin; Vite dev proxy forwards /api to the backend

function authHeaders() {
  const token = window.__JARVIS_AUTH_TOKEN__;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * PowerGrid — provider health / API key health / system resource state
 * (JARVIS_MAIN.md §20). Polls the backend diagnostics endpoint and renders
 * a compact subsystem health grid.
 */
export default function PowerGrid() {
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);

  const loadHealth = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/health`, { headers: { ...authHeaders() } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setReport(await res.json());
      setError(null);
    } catch (e) {
      setError('Diagnostics offline');
    }
  }, []);

  useEffect(() => {
    loadHealth();
    const interval = setInterval(loadHealth, 30000); // poll every 30s
    return () => clearInterval(interval);
  }, [loadHealth]);

  const statusColor = (status) => {
    if (!status) return 'text-cyan-800';
    const s = String(status).toLowerCase();
    if (s.includes('healthy') || s.includes('ok') || s === 'true' || s === 'connected') return 'text-green-400';
    if (s.includes('degraded') || s.includes('warn')) return 'text-amber-400';
    return 'text-red-400';
  };

  // Extract compact rows from whatever shape the diagnostics report provides.
  const rows = [];
  if (report && typeof report === 'object') {
    const pushRow = (label, value) => rows.push({ label, value });
    pushRow('Overall', report.status || 'unknown');
    for (const [key, value] of Object.entries(report)) {
      if (key === 'status') continue;
      if (value && typeof value === 'object' && 'status' in value) {
        pushRow(key.replace(/_/g, ' '), value.status);
      } else if (typeof value === 'string' || typeof value === 'boolean') {
        pushRow(key.replace(/_/g, ' '), value);
      }
    }
  }

  return (
    <div className="h-full flex flex-col text-[11px] font-hud">
      {error && (
        <div className="p-2 border border-red-900/60 bg-red-950/30 text-red-400 rounded-sm mb-2">
          {error}
        </div>
      )}

      {!report && !error && (
        <div className="flex-grow flex items-center justify-center text-cyan-700 uppercase tracking-widest">
          Scanning grid...
        </div>
      )}

      {report && (
        <div className="flex-grow overflow-y-auto space-y-1 pr-1">
          {rows.slice(0, 10).map((row, idx) => (
            <div key={idx} className="flex items-center justify-between px-2 py-1 border border-cyan-900/40 bg-slate-950/40 rounded-sm">
              <span className="text-cyan-600 capitalize truncate">{row.label}</span>
              <span className={`${statusColor(row.value)} truncate max-w-[55%]`}>
                {String(row.value).slice(0, 24)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}