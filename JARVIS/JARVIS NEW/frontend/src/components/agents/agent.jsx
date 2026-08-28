import React from 'react';
import { Cpu, Activity, AlertTriangle, CheckCircle, XCircle } from 'lucide-react';

export default function Agent({ agent, style = {}, className = '' }) {
  
  // Determine node styling based on agent ID and backend status
  const getStyles = (id, status) => {
    // 1. Handle universal system overrides first (Error / Warning / Success)
    if (status === 'WAITING_APPROVAL') {
      return {
        node: 'border-amber-400 bg-amber-950/80 text-amber-300 shadow-[0_0_15px_rgba(251,191,36,0.4)]',
        icon: <AlertTriangle size={20} className="animate-pulse" />,
        badge: 'text-amber-400 border-amber-400/50'
      };
    }
    if (status === 'FAILED') {
      return {
        node: 'border-red-500 bg-red-950/80 text-red-400 shadow-[0_0_15px_rgba(239,68,68,0.4)]',
        icon: <XCircle size={20} />,
        badge: 'text-red-400 border-red-500/50'
      };
    }
    if (status === 'COMPLETED') {
      return {
        node: 'border-green-500 bg-green-950/80 text-green-400',
        icon: <CheckCircle size={20} />,
        badge: 'text-green-400 border-green-500/50'
      };
    }

    // 2. Handle IDLE and ACTIVE states using the Agent's signature identity color
    const isActive = status === 'ACTIVE' || status === 'RUNNING';
    
    switch (id) {
      case 'FRIDAY': // Signature Purple/Fuchsia
        return {
          node: isActive 
            ? 'border-fuchsia-400 bg-fuchsia-950/80 text-fuchsia-300 shadow-[0_0_20px_rgba(232,121,249,0.5)] animate-pulse' 
            : 'border-fuchsia-900/50 bg-slate-900/80 text-fuchsia-500/70',
          icon: isActive ? <Activity size={20} /> : <Cpu size={20} />,
          badge: 'text-fuchsia-400 border-fuchsia-900/50'
        };
      case 'PLATO': // Signature Green
        return {
          node: isActive 
            ? 'border-green-400 bg-green-950/80 text-green-300 shadow-[0_0_20px_rgba(74,222,128,0.5)] animate-pulse' 
            : 'border-green-900/50 bg-slate-900/80 text-green-500/70',
          icon: isActive ? <Activity size={20} /> : <Cpu size={20} />,
          badge: 'text-green-400 border-green-900/50'
        };
      case 'VERONICA': // Signature Red/Rose
        return {
          node: isActive 
            ? 'border-rose-400 bg-rose-950/80 text-rose-300 shadow-[0_0_20px_rgba(251,113,133,0.5)] animate-pulse' 
            : 'border-rose-900/50 bg-slate-900/80 text-rose-500/70',
          icon: isActive ? <Activity size={20} /> : <Cpu size={20} />,
          badge: 'text-rose-400 border-rose-900/50'
        };
      case 'EDITH': // Signature Yellow/Amber
        return {
          node: isActive 
            ? 'border-yellow-400 bg-yellow-950/80 text-yellow-300 shadow-[0_0_20px_rgba(250,204,21,0.5)] animate-pulse' 
            : 'border-yellow-900/50 bg-slate-900/80 text-yellow-500/70',
          icon: isActive ? <Activity size={20} /> : <Cpu size={20} />,
          badge: 'text-yellow-400 border-yellow-900/50'
        };
      default: // Fallback
        return {
          node: isActive 
            ? 'border-cyan-400 bg-cyan-950/80 text-cyan-300 shadow-[0_0_20px_rgba(34,211,238,0.5)] animate-pulse' 
            : 'border-slate-600 bg-slate-900/80 text-slate-500',
          icon: isActive ? <Activity size={20} /> : <Cpu size={20} />,
          badge: 'text-cyan-400 border-cyan-900/50'
        };
    }
  };

  const styles = getStyles(agent.id, agent.status);

  return (
    <div 
      style={style}
      className={`absolute flex flex-col items-center justify-center w-16 h-16 rounded-full border-2 backdrop-blur-md transition-all duration-500 z-10 ${styles.node} ${className}`}
    >
      {styles.icon}
      
      {/* Agent Name Badge dynamically colored to match their identity */}
      <div className={`absolute -bottom-6 w-max px-2 py-0.5 bg-slate-950/90 border rounded font-hud text-[10px] tracking-widest font-bold whitespace-nowrap ${styles.badge}`}>
        {agent.name}
      </div>
    </div>
  );
}