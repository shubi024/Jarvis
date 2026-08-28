import React, { useState, useEffect, useCallback } from 'react';
import { ShieldAlert, Check, X, RefreshCw } from 'lucide-react';

const API_BASE = ''; // same-origin; Vite dev proxy forwards /api to the backend

function authHeaders() {
  const token = window.__JARVIS_AUTH_TOKEN__;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * Approval Center — lists pending human-in-the-loop approval gates and lets
 * the operator approve or reject them. Polls periodically and can be forced
 * to refresh via the refreshKey prop.
 */
export default function NotificationPanel({ refreshKey = 0 }) {
  const [approvals, setApprovals] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const loadApprovals = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/approvals/pending`, {
        headers: { ...authHeaders() },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setApprovals(data.approvals || []);
    } catch (e) {
      setError('Unable to reach approval service.');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadApprovals();
    const interval = setInterval(loadApprovals, 10000); // poll every 10s
    return () => clearInterval(interval);
  }, [loadApprovals, refreshKey]);

  const resolveApproval = async (approvalId, approved) => {
    setBusyId(approvalId);
    try {
      const res = await fetch(`${API_BASE}/api/v1/approvals/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ approval_id: approvalId, approved, resolved_by: 'human_user' }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadApprovals();
    } catch (e) {
      setError('Failed to resolve approval.');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="h-full flex flex-col text-[11px] font-hud">
      <div className="flex items-center justify-between mb-2 shrink-0">
        <span className="text-cyan-600 uppercase tracking-widest">
          Pending Gates ({approvals.length})
        </span>
        <button
          onClick={loadApprovals}
          className="text-cyan-500 hover:text-cyan-300 transition-colors"
          title="Refresh"
        >
          <RefreshCw size={12} className={isLoading ? 'animate-spin' : ''} />
        </button>
      </div>

      {error && (
        <div className="mb-2 p-2 border border-red-900/60 bg-red-950/30 text-red-400 rounded-sm">
          {error}
        </div>
      )}

      <div className="flex-grow overflow-y-auto space-y-2 pr-1">
        {approvals.length === 0 && !isLoading && (
          <div className="h-full flex items-center justify-center text-cyan-800 border border-dashed border-cyan-900/50 bg-slate-950/50">
            <span className="uppercase tracking-widest">No pending approvals</span>
          </div>
        )}

        {approvals.map((a) => (
          <div
            key={a.approval_id}
            className="p-2 border border-amber-900/50 bg-amber-950/10 rounded-sm"
          >
            <div className="flex items-start gap-2 mb-2">
              <ShieldAlert size={14} className="text-amber-400 mt-0.5 shrink-0" />
              <div className="min-w-0">
                <div className="text-amber-300 truncate">{a.intent}</div>
                <div className="text-cyan-700 truncate">
                  {a.tool_name || 'system'} · risk: {a.risk_level}
                </div>
                <div className="text-cyan-800 truncate">task: {a.task_id}</div>
              </div>
            </div>
            <div className="flex gap-2">
              <button
                disabled={busyId === a.approval_id}
                onClick={() => resolveApproval(a.approval_id, true)}
                className="flex-1 flex items-center justify-center gap-1 py-1 border border-green-800/60 text-green-400 hover:bg-green-900/20 disabled:opacity-40 transition-colors"
              >
                <Check size={11} /> APPROVE
              </button>
              <button
                disabled={busyId === a.approval_id}
                onClick={() => resolveApproval(a.approval_id, false)}
                className="flex-1 flex items-center justify-center gap-1 py-1 border border-red-800/60 text-red-400 hover:bg-red-900/20 disabled:opacity-40 transition-colors"
              >
                <X size={11} /> REJECT
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}