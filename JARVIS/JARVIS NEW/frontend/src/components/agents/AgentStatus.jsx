// src/components/agents/AgentStatus.jsx
import React from 'react';
import { Cpu, Activity, AlertTriangle, CheckCircle, XCircle } from 'lucide-react';

export default function AgentStatus({ agents = [] }) {
  
  // Determine dynamic visual styles based on backend status
  const getAgentStyles = (status) => {
    switch (status) {
      case 'ACTIVE':
      case 'RUNNING':
        return {
          wrapper: 'border-cyan-500/50 bg-cyan-950/20 shadow-[0_0_10px_rgba(34,211,238,0.2)]',
          text: 'text-cyan-300',
          indicator: <Activity size={14} className="text-cyan-400 animate-pulse" />
        };
      case 'WAITING_APPROVAL':
        return {
          wrapper: 'border-amber-500/50 bg-amber-950/20 animate-pulse',
          text: 'text-amber-400',
          indicator: <AlertTriangle size={14} className="text-amber-400" />
        };
      case 'COMPLETED':
        return {
          wrapper: 'border-green-500/30 bg-green-950/10',
          text: 'text-green-400',
          indicator: <CheckCircle size={14} className="text-green-400" />
        };
      case 'FAILED':
        return {
          wrapper: 'border-red-500/40 bg-red-950/20',
          text: 'text-red-400',
          indicator: <XCircle size={14} className="text-red-500" />
        };
      case 'IDLE':
      default:
        return {
          wrapper: 'border-slate-700/30 bg-slate-900/30 opacity-70',
          text: 'text-slate-400',
          indicator: <Cpu size={14} className="text-slate-500" />
        };
    }
  };

  return (
    <div className="flex-grow flex flex-col gap-3 overflow-y-auto pr-2" style={{ scrollbarWidth: 'none' }}>
      {agents.map((agent) => {
        const styles = getAgentStyles(agent.status);
        
        return (
          <div 
            key={agent.id} 
            className={`p-3 border rounded-sm flex items-center justify-between transition-all duration-300 ${styles.wrapper}`}
          >
            <div className="flex flex-col">
              <span className={`font-hud font-bold tracking-widest text-sm ${styles.text}`}>
                {agent.name}
              </span>
              <span className="text-[10px] font-mono text-slate-500 tracking-wider">
                {agent.role}
              </span>
            </div>
            
            <div className="flex items-center gap-2">
              <span className={`text-xs font-mono font-bold tracking-wider ${styles.text}`}>
                {agent.status}
              </span>
              {styles.indicator}
            </div>
          </div>
        );
      })}
    </div>
  );
}