import { useContext } from 'react';
import { StoreContext, type Store } from './storeContext';

export function useStore(): Store {
  const s = useContext(StoreContext);
  if (!s) throw new Error('useStore must be used inside <StoreProvider>');
  return s;
}
