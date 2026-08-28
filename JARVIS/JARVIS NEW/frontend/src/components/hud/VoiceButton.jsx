import React from 'react';
import { Mic, MicOff, Loader2 } from 'lucide-react';

/**
 * VoiceButton — microphone / listening / processing states (JARVIS_MAIN.md §20).
 * Toggles wake-word listening via the backend `voice_toggle` WS action.
 */
export default function VoiceButton({ isListening, isProcessing, onToggle }) {
  const stateClass = isProcessing
    ? 'border-amber-500/60 text-amber-300 animate-pulse'
    : isListening
      ? 'border-cyan-400/70 text-cyan-300 glow-cyan'
      : 'border-cyan-900/60 text-cyan-700 hover:text-cyan-400 hover:border-cyan-700/60';

  return (
    <button
      onClick={onToggle}
      disabled={isProcessing}
      title={isListening ? 'Deactivate voice listening' : 'Activate voice listening'}
      className={`w-full flex items-center justify-center gap-2 py-2 border rounded-sm font-hud text-xs tracking-widest uppercase transition-colors ${stateClass} disabled:opacity-50`}
    >
      {isProcessing ? (
        <Loader2 size={14} className="animate-spin" />
      ) : isListening ? (
        <Mic size={14} />
      ) : (
        <MicOff size={14} />
      )}
      {isProcessing ? 'SWITCHING' : isListening ? 'VOICE ACTIVE' : 'VOICE OFF'}
    </button>
  );
}