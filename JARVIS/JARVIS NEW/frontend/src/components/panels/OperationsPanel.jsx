import React from 'react';
import { CheckCircle, XCircle, Clock, Activity, AlertTriangle } from 'lucide-react';

export default function OperationsPanel({ tasks = [] }) {
  // Dynamically assign colors based on task status
  const getStatusColor = (status) => {
    switch (status) {
      case 'COMPLETED': return 'text-green-400 border-green-500/30 bg-green-950/20';
      case 'FAILED': return 'text-red-400 border-red-500/30 bg-red-950/20';
      case 'RUNNING': return 'text-cyan-400 border-cyan-500/50 bg-cyan-950/20 shadow-[0_0_10px_rgba(34,211,238,0.1)]';
      case 'QUEUED': return 'text-slate-400 border-slate-500/30 bg-slate-900/50';
      case 'WAITING_APPROVAL': return 'text-amber-400 border-amber-500/50 bg-amber-950/20 animate-pulse';
      default: return 'text-cyan-600 border-cyan-900/50 bg-slate-950/50';
    }
  };

  // Dynamically assign icons based on task status
  const getStatusIcon = (status) => {
    switch (status) {
      case 'COMPLETED': return <CheckCircle size={14} />;
      case 'FAILED': return <XCircle size={14} />;
      case 'RUNNING': return <Activity size={14} className="animate-pulse" />;
      case 'QUEUED': return <Clock size={14} />;
      case 'WAITING_APPROVAL': return <AlertTriangle size={14} />;
      default: return <Clock size={14} />;
    }
  };

  return (
    <div className="flex-grow flex flex-col overflow-hidden font-mono text-sm relative">
      <div 
        className="flex-grow overflow-y-auto space-y-3 pr-2 pb-2" 
        style={{ scrollbarWidth: 'thin', scrollbarColor: '#0891b2 transparent' }}
      >
        {tasks.length === 0 ? (
          <div className="flex h-full items-center justify-center text-cyan-800 border border-dashed border-cyan-900/50 bg-slate-950/50">
            <span className="font-hud text-xs tracking-widest">NO ACTIVE OPERATIONS</span>
          </div>
        ) : (
          // Reverse array to always show the newest tasks at the top
          [...tasks].reverse().map((task) => (
            <div 
              key={task.id} 
              className={`p-3 border rounded-sm flex flex-col gap-2 transition-all duration-300 ${getStatusColor(task.status)}`}
            >
              {/* Header: Task ID & Status */}
              <div className="flex justify-between items-center border-b border-inherit pb-1 mb-1">
                <span className="font-bold tracking-wider text-xs">ID: {task.id.slice(0, 8)}...</span>
                <div className="flex items-center gap-1 text-xs uppercase tracking-wider font-bold">
                  {getStatusIcon(task.status)}
                  <span>{task.status}</span>
                </div>
              </div>

              {/* Body: Intent & Agent */}
              <div className="flex flex-col gap-1 text-xs">
                <span className="truncate"><strong>OP:</strong> {task.intent || 'SYSTEM_PROCESS'}</span>
                <span className="opacity-80">
                  <strong>AGENT:</strong> {task.agents?.join(', ') || task.agent || 'Orchestrator'}
                </span>
              </div>

              {/* Footer: Timestamps */}
              <div className="flex justify-between items-center pt-1 mt-1 border-t border-inherit opacity-70 text-[10px]">
                <span>START: {task.startTime || '--:--:--'}</span>
                <span>{task.completedTime ? `END: ${task.completedTime}` : ''}</span>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}