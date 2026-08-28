import React, { useState, useEffect, useRef, useCallback } from 'react';
import { jarvisWS } from './services/websocket';
import CommandPanel from './components/panels/CommandPanel';
import OperationsPanel from './components/panels/OperationsPanel';
import NotificationPanel from './components/panels/NotificationPanel';
import LiveActivityPanel from './components/panels/LiveActivityPanel';

// HUD Component Imports
import TopStatusBar from './components/hud/TopStatusBar';
import PowerGrid from './components/hud/PowerGrid';
import VoiceButton from './components/hud/VoiceButton';

// Agent Imports
import { INITIAL_AGENTS } from './data/agents';
import AgentStatus from './components/agents/AgentStatus';
import AgentOrbit from './components/agents/AgentOrbit';

// Backend telemetry topics arrive as:
//   { type: "telemetry", topic: "task.queued" | "task.completed" | ..., payload: {...}, task_id }
// Map a backend topic to the HUD's canonical agent/task status vocabulary.
function topicToStatus(topic) {
  if (!topic) return null;
  if (topic.includes('completed')) return 'COMPLETED';
  if (topic.includes('failed')) return 'FAILED';
  if (topic.includes('executing')) return 'EXECUTING';
  if (topic.includes('verifying')) return 'VERIFYING';
  if (topic.includes('retrying')) return 'RETRYING';
  if (topic.includes('blocked') || topic.includes('approval')) return 'WAITING_APPROVAL';
  if (topic.includes('resumed') || topic.includes('queued')) return 'QUEUED';
  if (topic.includes('cancelled')) return 'CANCELLED';
  return null;
}

function App() {
  const [isConnected, setIsConnected] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [messages, setMessages] = useState([
    { sender: 'SYSTEM', text: 'System initialized. Awaiting operator input.' }
  ]);
  const [tasks, setTasks] = useState([]);
  const [agents, setAgents] = useState(INITIAL_AGENTS);
  const [approvalRefreshKey, setApprovalRefreshKey] = useState(0);
  const [isListening, setIsListening] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [sessionLocked, setSessionLocked] = useState(true);
  const [activityEvents, setActivityEvents] = useState([]);

  // Mirror of `tasks` for synchronous reads inside event callbacks
  // (avoids referencing updater-scoped variables from other setters).
  const tasksRef = useRef([]);
  useEffect(() => {
    tasksRef.current = tasks;
  }, [tasks]);

  useEffect(() => {
    const unsubscribeStatus = jarvisWS.addStatusListener((status) => {
      setIsConnected(status);
    });

    const unsubscribeMessage = jarvisWS.addMessageListener((data) => {
      if (data.type === 'pong') {
        console.log('%c[J.A.R.V.I.S.] PONG RECEIVED: ' + data.message, 'color: #00ff00; font-weight: bold;');
        return;
      }

      if (data.type === 'command_result') {
        setMessages(prev => [...prev, { sender: 'SYSTEM', text: data.response }]);
        setIsLoading(false);
        // A new command may have registered an approval gate.
        setApprovalRefreshKey(k => k + 1);
        return;
      }

      // Voice lifecycle state pushed by the backend voice_toggle/voice_status actions.
      if (data.type === 'voice_state') {
        setIsListening(Boolean(data.listening));
        setVoiceBusy(false);
        return;
      }

      // Canonical backend telemetry envelope.
      if (data.type === 'telemetry' && data.topic) {
        const payload = data.payload || {};
        const taskId = data.task_id || payload.task_id;
        const status = topicToStatus(data.topic);

        // Rolling live-activity feed (right column, newest first).
        setActivityEvents(prev => [
          {
            id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
            topic: data.topic,
            summary: payload.summary || payload.reason || '',
            time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
          },
          ...prev,
        ].slice(0, 50));

        // Track session lock state for the status bar.
        if (data.topic.startsWith('session.') && typeof payload.is_locked === 'boolean') {
          setSessionLocked(payload.is_locked);
        }

        // Approval lifecycle events refresh the Approval Center immediately.
        if (data.topic.startsWith('approval.') || (data.topic.startsWith('task.') && status === 'WAITING_APPROVAL')) {
          setApprovalRefreshKey(k => k + 1);
        }

        if (taskId && status && data.topic.startsWith('task.')) {
          // Resolve the current task record ONCE, outside any state updater,
          // so both setters below read a consistent snapshot (fixes the previous
          // ReferenceError where `existing` was scoped to setTasks() only).
          const existing = tasksRef.current.find(t => t.id === taskId);
          const assignedAgentIds = Array.from(new Set([
            ...(existing?.agents || []),
            ...((payload.target_agents || []).map(a => String(a).toUpperCase())),
          ]));

          setTasks(prevTasks => {
            const existingInPrev = prevTasks.find(t => t.id === taskId);
            const incomingTask = {
              id: taskId,
              intent: payload.intent || existingInPrev?.intent || 'TASK',
              agents: assignedAgentIds,
              status,
              updatedAt: data.timestamp,
            };
            if (existingInPrev) {
              return prevTasks.map(t => (t.id === taskId ? { ...t, ...incomingTask } : t));
            }
            return [...prevTasks, incomingTask];
          });

          // Reflect task state on assigned agents in the orbit/status widgets.
          setAgents(prevAgents => prevAgents.map(agent => (
            assignedAgentIds.includes(agent.id) ? { ...agent, status } : agent
          )));
        }
      }
    });

    jarvisWS.connect();
    return () => {
      unsubscribeStatus();
      unsubscribeMessage();
      jarvisWS.disconnect();
    };
  }, []);

  const handleCommand = (command) => {
    setMessages(prev => [...prev, { sender: 'OPERATOR', text: command }]);
    setIsLoading(true);
    const sent = jarvisWS.send('execute_command', { command });
    if (!sent) {
      setIsLoading(false);
      setMessages(prev => [...prev, { sender: 'SYSTEM', text: 'Unable to dispatch command. WebSocket is offline.' }]);
    }
  };

  const handleVoiceToggle = useCallback(() => {
    setVoiceBusy(true);
    const sent = jarvisWS.send('voice_toggle', { enable: !isListening });
    if (!sent) {
      setVoiceBusy(false);
    }
  }, [isListening]);

  return (
    <div className="h-screen w-screen p-4 flex flex-col overflow-hidden bg-slate-950 text-cyan-500">

      {/* Top Status Bar (canonical HUD header) */}
      <TopStatusBar isConnected={isConnected} isListening={isListening} isLocked={sessionLocked} />

      {/* Master HUD Layout - Bulletproof Flexbox with strict inline widths */}
      <main className="flex-1 w-full flex flex-row gap-4 min-h-0 overflow-hidden">

        {/* LEFT COLUMN - 25% */}
        <section className="flex flex-col gap-4 h-full min-h-0" style={{ width: '25%' }}>

          <div className="hud-border p-3 flex flex-col overflow-hidden bg-slate-900/20 backdrop-blur-sm" style={{ height: '15%' }}>
            <h2 className="font-hud text-sm border-b border-cyan-900/50 pb-1 mb-2 text-cyan-300 shrink-0 uppercase tracking-widest text-center">Power Grid</h2>
            <PowerGrid />
          </div>

          <div className="hud-border p-4 flex flex-col overflow-hidden bg-slate-900/20 backdrop-blur-sm" style={{ height: '52%' }}>
            <h2 className="font-hud text-sm border-b border-cyan-900/50 pb-2 mb-4 text-cyan-300 shrink-0 uppercase tracking-widest text-center">Command Center</h2>
            <CommandPanel messages={messages} onCommand={handleCommand} isLoading={isLoading} />
          </div>

          {/* Voice listening toggle */}
          <div className="hud-border p-3 flex flex-col justify-center bg-slate-900/20 backdrop-blur-sm shrink-0">
            <VoiceButton
              isListening={isListening}
              isProcessing={voiceBusy}
              onToggle={handleVoiceToggle}
            />
          </div>

          <div className="hud-border p-3 flex flex-col overflow-hidden bg-slate-900/20 backdrop-blur-sm" style={{ height: '22%' }}>
            <h2 className="font-hud text-sm border-b border-cyan-900/50 pb-1 mb-2 text-cyan-300 shrink-0 uppercase tracking-widest text-center">Active Agents</h2>
            <AgentStatus agents={agents} />
          </div>

        </section>

        {/* CENTER COLUMN - 50% */}
        <section className="relative flex items-center justify-center h-full min-h-0 border-x-0 border-t-0 border-b-0 shadow-none" style={{ width: '50%' }}>

          {/* Absolute positioning container for AgentOrbit */}
          <div className="absolute inset-0 flex items-center justify-center">
            <AgentOrbit agents={agents} />

            {/* Core Matrix Spinner */}
            <div className="relative z-20 w-40 h-40 rounded-full border border-cyan-500/30 glow-cyan flex flex-col items-center justify-center bg-slate-950/80 backdrop-blur-md shadow-[0_0_30px_rgba(34,211,238,0.1)]">
              <div className="absolute inset-0 rounded-full border-t border-cyan-400 animate-spin" style={{ animationDuration: '3s' }}></div>
              <span className="font-hud text-lg text-cyan-300 font-bold tracking-widest z-30 mb-1">JARVIS</span>
              <span className="font-hud text-[10px] text-cyan-600 tracking-widest z-30">{isLoading ? 'PROCESSING' : 'ONLINE'}</span>
            </div>
          </div>

        </section>

        {/* RIGHT COLUMN - 25% */}
        <section className="flex flex-col gap-4 h-full min-h-0" style={{ width: '25%' }}>

          <div className="hud-border p-3 flex flex-col overflow-hidden bg-slate-900/20 backdrop-blur-sm" style={{ height: '40%' }}>
            <h2 className="font-hud text-sm border-b border-amber-900/50 pb-1 mb-2 text-amber-300 shrink-0 uppercase tracking-widest text-center">Approval Center</h2>
            <NotificationPanel refreshKey={approvalRefreshKey} />
          </div>

          <div className="hud-border p-4 flex flex-col overflow-hidden bg-slate-900/20 backdrop-blur-sm" style={{ height: '35%' }}>
            <h2 className="font-hud text-sm border-b border-cyan-900/50 pb-2 mb-4 text-cyan-300 shrink-0 uppercase tracking-widest text-center">Workforce</h2>
            <OperationsPanel tasks={tasks} />
          </div>

          <div className="hud-border p-3 flex flex-col overflow-hidden bg-slate-900/20 backdrop-blur-sm" style={{ height: '25%' }}>
            <h2 className="font-hud text-sm border-b border-cyan-900/50 pb-1 mb-2 text-cyan-300 shrink-0 uppercase tracking-widest text-center">Live Activity</h2>
            <LiveActivityPanel events={activityEvents} />
          </div>

        </section>

      </main>
    </div>
  );
}

export default App;