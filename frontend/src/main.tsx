import React from 'react';
import ReactDOM from 'react-dom/client';
import { AuthProvider } from './context/AuthContext.tsx';
import { App } from './App.tsx';
import { StartupGate } from './components/recovery/StartupGate.tsx';
import './index.css';
import { preventIosFocusZoom } from './utils/iosViewport.ts';

preventIosFocusZoom();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthProvider>
      <StartupGate>
        <App />
      </StartupGate>
    </AuthProvider>
  </React.StrictMode>
);
