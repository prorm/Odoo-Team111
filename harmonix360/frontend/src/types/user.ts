import { UserRole } from './enums';

export interface Token {
  access_token: string;
  token_type: string;
}

export interface UserLogin {
  email: string;
  password: string;
}

export interface UserResponse {
  id: string;
  email: string;
  name: string;
  role: UserRole;
  tenant_id: string;
  /** The Employee record this login owns, when one exists. Null for a
   *  payroll-only or admin login with no HR record of their own. */
  employee_id: string | null;
}
