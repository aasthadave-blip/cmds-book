import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { GoogleOAuthProvider } from '@react-oauth/google';

import App from './App';
import { AuthProvider } from './auth/AuthProvider';
import './styles/globals.css';

// Real client ID in production. A placeholder in dev-stub mode so the
// useGoogleLogin hook still has a context to bind to — the placeholder is
// never actually used because the Login page short-circuits to the dev
// stub when VITE_GOOGLE_CLIENT_ID is absent.
const GOOGLE_CLIENT_ID =
  (import.meta.env.VITE_GOOGLE_CLIENT_ID ?? '').trim() || 'dev-stub.invalid';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </GoogleOAuthProvider>
  </React.StrictMode>
);
