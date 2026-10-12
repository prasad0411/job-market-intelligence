import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { Provider } from 'react-redux';
import { makeStore } from './store';
import './index.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Provider store={makeStore()}>
      <App />
    </Provider>
  </StrictMode>,
);
