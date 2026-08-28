import React, { useState, useRef, useEffect } from 'react';

export default function CommandPanel({ messages, onCommand, isLoading }) {
  const [input, setInput] = useState('');
  const scrollRef = useRef(null);

  // Auto-scroll to the bottom whenever a new message arrives or loading state changes
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, isLoading]);

  const handleCommandSubmit = (e) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    onCommand(input.trim());
    setInput('');
  };

  return (
    <div className="flex-grow flex flex-col font-mono relative h-full overflow-hidden">
      
      {/* Scrollable Message History Area */}
      <div 
        ref={scrollRef}
        className="flex-grow overflow-y-auto mb-4 space-y-3 pr-2"
        style={{ scrollbarWidth: 'thin', scrollbarColor: '#0891b2 transparent' }}
      >
        {messages.map((msg, idx) => (
          <div key={idx} className={`text-sm ${msg.sender === 'OPERATOR' ? 'text-cyan-200' : 'text-cyan-400'}`}>
            <span className="opacity-50 mr-2 font-bold">
              {msg.sender === 'OPERATOR' ? 'USR >' : 'SYS >'}
            </span>
            {/* whitespace-pre-wrap preserves paragraphs and line breaks from LLM responses */}
            <span className="whitespace-pre-wrap leading-relaxed">{msg.text}</span>
          </div>
        ))}

        {/* Live Loading Indicator during backend failover/processing */}
        {isLoading && (
          <div className="text-sm text-cyan-500 animate-pulse flex items-center space-x-2">
            <span className="opacity-50 font-bold">SYS &gt;</span>
            <span className="inline-block">Synthesizing cognitive matrix response...</span>
          </div>
        )}
      </div>
      
      {/* Text Input Form */}
      <form onSubmit={handleCommandSubmit} className="relative shrink-0 mt-auto">
        <span className="absolute left-0 top-2 text-cyan-400 opacity-70">$&gt;</span>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={isLoading}
          autoFocus
          className="w-full bg-slate-950/50 border border-cyan-900/50 py-2 pl-8 pr-4 text-cyan-300 focus:outline-none focus:border-cyan-400 focus:shadow-[0_0_10px_rgba(34,211,238,0.2)] transition-all placeholder-cyan-800/50 disabled:opacity-50"
          placeholder={isLoading ? "J.A.R.V.I.S. is processing..." : "Enter system command..."}
          autoComplete="off"
        />
      </form>
    </div>
  );
}