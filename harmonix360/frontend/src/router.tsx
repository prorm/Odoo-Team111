import { createBrowserRouter, Navigate, RouterProvider } from 'react-router-dom';

import { AppShell } from './components/layout/AppShell';
import { RequireAuth } from './routes/RequireAuth';
import { LoginPage } from './routes/LoginPage';
import { ContractsPage } from './routes/contracts/ContractsPage';
import { EmployeeDetailPage } from './routes/employees/EmployeeDetailPage';
import { EmployeesPage } from './routes/employees/EmployeesPage';
import { SchedulesPage } from './routes/schedules/SchedulesPage';
import { AttendancePage } from './routes/attendance/AttendancePage';
import { TimeOffPage } from './routes/time-off/TimeOffPage';
import { PayrollPage } from './routes/payroll/PayrollPage';
import { PayrunDetailPage } from './routes/payroll/PayrunDetailPage';
import { SalarySetupPage } from './routes/salary-setup/SalarySetupPage';
import { ReportsPage } from './routes/reports/ReportsPage';
import { MyProfilePage } from './routes/profile/MyProfilePage';
import { AssistantPage } from './routes/assistant/AssistantPage';
import { AnomaliesPage } from './routes/insights/AnomaliesPage';

/**
 * Routes for PS B1's top navigation: Employees, Contracts, Attendance,
 * Time Off, Payroll, Reports.
 *
 * Every section was present and routed from Phase 0 so the navigation was
 * real rather than aspirational; each rendered a stub naming the PS features
 * it covers and the phase that delivers them, and each was replaced in place
 * as that phase landed. Phase 4 replaced the Payroll stub and Phase 6 the
 * Reports one, so no stub remains here — `SectionStub` itself is kept for any
 * section a later phase adds ahead of its implementation.
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
      // PRD §4's Employee user story opens with "view own profile". It is not
      // one of B1's six HR sections, and it is not the HR employee screen with
      // a filter on it either: it reads `/employees/me`, which scopes by the
      // signed claim, so there is no id in the URL to point at someone else.
      { path: 'my-profile', element: <MyProfilePage /> },
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
      { path: 'payroll', element: <PayrollPage /> },
      { path: 'payroll/:payrunId', element: <PayrunDetailPage /> },
      { path: 'salary-setup', element: <SalarySetupPage /> },
      {
        path: 'reports',
        element: <ReportsPage />,
      },
      // PS §5.1's AI layer. Not one of B1's six HR sections — it explains what
      // those sections already contain rather than owning records of its own,
      // so it sits after them rather than competing for the same place.
      { path: 'assistant', element: <AssistantPage /> },
      // PRD §5.7. Deterministic checks over real records, alongside the live
      // payroll feed — a read-only screen, like everything else Phase 10 adds.
      { path: 'anomalies', element: <AnomaliesPage /> },
      // Anything else lands on Employees rather than a blank screen.
      { path: '*', element: <Navigate to="/employees" replace /> },
    ],
  },
]);

export function AppRouter() {
  return <RouterProvider router={router} />;
}
