/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    './index.html',
    './src/**/*.{js,jsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        hud: ['Orbitron', 'sans-serif'],
        sans: ['Inter', 'sans-serif'],
      },
      colors: {
        hud: {
          cyan: '#22d3ee',
          panel: 'rgba(15, 23, 42, 0.2)',
        },
      },
      boxShadow: {
        'glow-cyan': '0 0 15px rgba(34, 211, 238, 0.25), inset 0 0 15px rgba(34, 211, 238, 0.1)',
      },
    },
  },
  plugins: [],
}