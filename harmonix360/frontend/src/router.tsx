import { createBrowserRouter, Navigate, RouterProvider } from 'react-router-dom';

import { AppShell } from './components/layout/AppShell';
import { SectionStub } from './routes/SectionStub';

/**
 * Routes for PS B1's top navigation: Employees, Contracts, Attendance,
 * Time Off, Payroll, Reports.
 *
 * Every section is present and routed from Phase 0 so the navigation is real
 * rather than aspirational; each renders a stub naming the PS features it
 * covers and the phase that delivers them, and each is replaced in place as
 * that phase lands.
 *
 * `/` redirects to `/employees` instead of rendering a dashboard of its own.
 * PS B9's dashboard lives under Reports, and inventing a second landing page
 * would leave two screens competing to be "home".
 */
const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/employees" replace /> },
      {
        path: 'employees',
        element: <SectionStub name="Employees" features="A1 Employee Master, B2 Employee Form hub" phase="Phase 1" />,
      },
      {
        path: 'contracts',
        element: <SectionStub name="Contracts" features="A2 Contract Management" phase="Phase 1" />,
      },
      {
        path: 'attendance',
        element: <SectionStub name="Attendance" features="B3 Check In/Out, Worked Hours, corrections" phase="Phase 2" />,
      },
      {
        path: 'time-off',
        element: (
          <SectionStub
            name="Time Off"
            features="A4 Types & Allocations, B4 approve/refuse workflow"
            phase="Phase 2"
          />
        ),
      },
      {
        path: 'payroll',
        element: (
          <SectionStub
            name="Payroll"
            features="A5/A6 Salary Structures & Rules, B5-B8 Payrun, Payslip, PDF, email"
            phase="Phases 3-5"
          />
        ),
      },
      {
        path: 'reports',
        element: <SectionStub name="Reports" features="A7 Reporting Config, B9 Payroll Dashboard" phase="Phase 6" />,
      },
      // Anything else lands on Employees rather than a blank screen.
      { path: '*', element: <Navigate to="/employees" replace /> },
    ],
  },
]);

export function AppRouter() {
  return <RouterProvider router={router} />;
}
