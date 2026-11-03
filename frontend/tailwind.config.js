/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', '"Liberation Mono"', '"Courier New"', 'monospace'],
        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto', 'sans-serif'],
      },
      colors: {
        dark: {
          950: '#070a10',
          900: '#0b0f19',
          850: '#101726',
          800: '#141d30',
          700: '#1e293b',
          600: '#334155',
          500: '#475569',
        },
        accent: {
          500: '#0ea5e9', // Sky blue
          600: '#0284c7',
          // Backgrounds behind white text: 5.9:1 (600 gives 4.1:1, below the 4.5:1 WCAG AA asks)
          700: '#0369a1',
        }
      }
    },
  },
  plugins: [],
}
