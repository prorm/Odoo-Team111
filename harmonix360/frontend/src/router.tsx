import { createBrowserRouter, Navigate, RouterProvider } from 'react-router-dom';

import { AppShell } from './components/layout/AppShell';
import { RequireAuth } from './routes/RequireAuth';
import { LoginPage } from './routes/LoginPage';
import { SectionStub } from './routes/SectionStub';
import { ContractsPage } from './routes/contracts/ContractsPage';
import { EmployeeDetailPage } from './routes/employees/EmployeeDetailPage';
import { EmployeesPage } from './routes/employees/EmployeesPage';
import { SchedulesPage } from './routes/schedules/SchedulesPage';
import { AttendancePage } from './routes/attendance/AttendancePage';
import { TimeOffPage } from './routes/time-off/TimeOffPage';

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
  { path: '/login', element: <LoginPage /> },
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/employees" replace /> },
      { path: 'employees', element: <EmployeesPage /> },
      // Before ':employeeId', or "schedules" would be read as an employee id.
      { path: 'employees/schedules', element: <SchedulesPage /> },
      { path: 'employees/:employeeId', element: <EmployeeDetailPage /> },
      { path: 'contracts', element: <ContractsPage /> },
      {
        path: 'attendance',
        element: <AttendancePage />,
      },
      {
        path: 'time-off',
        element: <TimeOffPage />,
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
