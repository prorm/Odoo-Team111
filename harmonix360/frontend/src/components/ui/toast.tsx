import * as React from 'react';
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface ToastMessage {
  id: string;
  type?: 'success' | 'error' | 'info';
  title: string;
  description?: string;
}

interface ToastContextType {
  toast: (msg: Omit<ToastMessage, 'id'>) => void;
}

const ToastContext = React.createContext<ToastContextType>({ toast: () => {} });

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [messages, setMessages] = React.useState<ToastMessage[]>([]);

  const toast = React.useCallback((msg: Omit<ToastMessage, 'id'>) => {
    const id = Math.random().toString(36).substring(2, 9);
    setMessages((prev) => [...prev, { ...msg, id }]);
    setTimeout(() => {
      setMessages((prev) => prev.filter((m) => m.id !== id));
    }, 4000);
  }, []);

  const removeToast = (id: string) => {
    setMessages((prev) => prev.filter((m) => m.id !== id));
  };

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div
        className="fixed bottom-4 right-4 z-50 flex w-full max-w-md flex-col space-y-2 px-4 sm:px-0"
        aria-live="polite"
        aria-atomic="true"
      >
        {messages.map((msg) => (
          <div
            key={msg.id}
            role={msg.type === 'error' ? 'alert' : 'status'}
            className={cn(
              'flex items-start rounded-md border bg-white p-4 text-slate-100 shadow-[0_12px_30px_rgba(19,36,58,0.16)]',
              msg.type === 'success' && 'border-emerald-800',
              msg.type === 'error' && 'border-red-800',
              (!msg.type || msg.type === 'info') && 'border-indigo-800'
            )}
          >
            {msg.type === 'success' && <CheckCircle2 className="h-5 w-5 text-emerald-400 mr-3 mt-0.5 flex-shrink-0" />}
            {msg.type === 'error' && <AlertCircle className="h-5 w-5 text-red-400 mr-3 mt-0.5 flex-shrink-0" />}
            {(!msg.type || msg.type === 'info') && <Info className="h-5 w-5 text-indigo-400 mr-3 mt-0.5 flex-shrink-0" />}
            <div className="flex-1">
              <div className="font-semibold text-sm text-slate-100">{msg.title}</div>
              {msg.description && <div className="text-xs text-slate-400 mt-0.5">{msg.description}</div>}
            </div>
            <button
              onClick={() => removeToast(msg.id)}
              className="ml-2 text-slate-400 transition-colors hover:text-slate-100"
              aria-label="Dismiss notification"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return React.useContext(ToastContext);
}
