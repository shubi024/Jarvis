import React from 'react';
import { Radio } from 'lucide-react';

const TOPIC_COLORS = {
  task: 'text-cyan-400',
  workflow: 'text-cyan-300',
  agent: 'text-green-400',
  tool: 'text-cyan-500',
  approval: 'text-amber-400',
  verification: 'text-blue-400',
  security: 'text-red-400',
  voice: 'text-purple-400',
  observation: 'text-teal-400',
  briefing: 'text-yellow-300',
};

function topicColor(topic = '') {
  const prefix = topic.split('.')[0];
  return TOPIC_COLORS[prefix] || 'text-cyan-600';
}

/**
 * LiveActivityPanel — real-time backend event stream (JARVIS_MAIN.md §14 right column).
 * Receives the rolling telemetry feed maintained by App.jsx.
 */
export default function LiveActivityPanel({ events = [] }) {
  return (
    <div className="h-full flex flex-col text-[10px] font-hud">
      {events.length === 0 ? (
        <div className="h-full flex items-center justify-center text-cyan-800 border border-dashed border-cyan-900/50 bg-slate-950/50 uppercase tracking-widest">
          Awaiting telemetry
        </div>
      ) : (
        <div className="flex-grow overflow-y-auto space-y-1 pr-1">
          {events.map((evt) => (
            <div
              key={evt.id}
              className="px-2 py-1 border border-cyan-900/40 bg-slate-950/40 rounded-sm flex items-start gap-2"
            >
              <Radio size={10} className={`${topicColor(evt.topic)} mt-0.5 shrink-0`} />
              <div className="min-w-0 flex-grow">
                <div className={`${topicColor(evt.topic)} truncate`}>{evt.topic}</div>
                {evt.summary && (
                  <div className="text-cyan-800 truncate">{evt.summary}</div>
                )}
              </div>
              <span className="text-cyan-900 shrink-0 tabular-nums">{evt.time}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}