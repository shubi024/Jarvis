import React from 'react';
import Agent from './Agent';

export default function AgentOrbit({ agents = [] }) {
  // Responsive layout positioning forming a staggered arc below the JARVIS core.
  // Using percentages ensures the UI never breaks, regardless of screen resolution.
  const positions = [
    'top-[75%] left-[20%]', // 0: F.R.I.D.A.Y. (Far left, lower)
    'top-[60%] left-[38%]', // 1: P.L.A.T.O.  (Mid left, higher)
    'top-[60%] left-[62%]', // 2: V.E.R.O.N.I.C.A. (Mid right, higher)
    'top-[75%] left-[80%]', // 3: E.D.I.T.H.  (Far right, lower)
  ];

  return (
    <div className="absolute inset-0 pointer-events-none">
      {agents.map((agent, index) => {
        // -translate-x-1/2 and -translate-y-1/2 perfectly centers the agent 
        // directly on its exact coordinate percentage.
        const positionClass = positions[index] || 'top-[85%] left-[50%]';

        return (
          <Agent 
            key={agent.id} 
            agent={agent} 
            className={`${positionClass} -translate-x-1/2 -translate-y-1/2`} 
          />
        );
      })}
    </div>
  );
}