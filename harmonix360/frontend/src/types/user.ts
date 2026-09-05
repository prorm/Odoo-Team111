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
}
