import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import { AppShell } from './components/layout/AppShell';
import { NotesPage } from './routes/notes/NotesPage';

function EmptyDashboard() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center gap-2 py-24">
      <h1 className="text-xl font-semibold text-slate-200">No Product Surface registered</h1>
      <p className="text-sm text-slate-500 max-w-md">
        This is a bare scaffold. Domain pages get built fresh per problem statement and routed
        here.
      </p>
    </div>
  );
}

const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      {
        index: true,
        element: <EmptyDashboard />,
      },
      {
        path: 'notes',
        element: <NotesPage />,
      },
    ],
  },
]);

export function AppRouter() {
  return <RouterProvider router={router} />;
}
