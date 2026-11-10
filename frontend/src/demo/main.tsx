// Entry point of the browser demo (demo.html): the real app, with the fake backend installed first.
import './install.ts';
import React from 'react';
import ReactDOM from 'react-dom/client';
import { AuthProvider } from '../context/AuthContext.tsx';
import { App } from '../App.tsx';
import { DemoBanner } from './DemoBanner.tsx';
import '../index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
    <DemoBanner />
  </React.StrictMode>
);
