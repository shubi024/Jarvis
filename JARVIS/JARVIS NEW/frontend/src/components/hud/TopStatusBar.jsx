import React, { useState, useEffect } from 'react';
import { Cpu, Activity, Network, Mic, MicOff } from 'lucide-react';

/**
 * TopStatusBar — canonical HUD header (JARVIS_MAIN.md §20).
 * Shows system identity, online/offline state, voice listening state,
 * session lock indicator and a live clock.
 */
export default function TopStatusBar({ isConnected, isListening, isLocked }) {
  const [clock, setClock] = useState(new Date());

  useEffect(() => {
    const interval = setInterval(() => setClock(new Date()), 1000);
    return () => clearInterval(interval);
  }, []);

  return (
    <header className="flex justify-between items-center pb-4 border-b border-cyan-900/50 mb-4 shrink-0 h-12 w-full">
      <div className="flex items-center gap-3">
        <Cpu className="text-cyan-400" size={24} />
        <h1 className="text-2xl font-hud tracking-widest font-bold glow-text">J.A.R.V.I.S.</h1>
      </div>

      <div className="flex items-center gap-6 font-hud text-sm tracking-wider">
        <span className={`flex items-center gap-2 ${isListening ? 'text-cyan-300' : 'text-cyan-700'}`}>
          {isListening ? <Mic size={16} /> : <MicOff size={16} />}
          {isListening ? 'LISTENING' : 'VOICE IDLE'}
        </span>
        <span className={`flex items-center gap-2 ${isLocked ? 'text-amber-500' : 'text-green-600'}`}>
          {isLocked ? 'SESSION LOCKED' : 'SESSION ACTIVE'}
        </span>
        <span className={`flex items-center gap-2 ${isConnected ? 'text-green-400' : 'text-red-500'}`}>
          <Activity size={16} />
          {isConnected ? 'SYSTEM ONLINE' : 'SYSTEM OFFLINE'}
        </span>
        <span className="flex items-center gap-2 text-cyan-600">
          <Network size={16} />
          SECURE
        </span>
        <span className="text-cyan-500 tabular-nums">
          {clock.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
        </span>
      </div>
    </header>
  );
}