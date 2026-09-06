export enum SalaryRuleCategory {
  BASIC = 'basic',
  ALLOWANCE = 'allowance',
  GROSS = 'gross',
  DEDUCTION = 'deduction',
  NET = 'net',
}

export enum SalaryRuleComputation {
  FIXED = 'fixed',
  PERCENTAGE = 'percentage',
  FORMULA = 'formula',
}

export const SALARY_CATEGORY_ORDER: readonly SalaryRuleCategory[] = [
  SalaryRuleCategory.BASIC,
  SalaryRuleCategory.ALLOWANCE,
  SalaryRuleCategory.GROSS,
  SalaryRuleCategory.DEDUCTION,
  SalaryRuleCategory.NET,
];

export const SALARY_CATEGORY_LABELS: Record<SalaryRuleCategory, string> = {
  [SalaryRuleCategory.BASIC]: 'Basic',
  [SalaryRuleCategory.ALLOWANCE]: 'Allowance',
  [SalaryRuleCategory.GROSS]: 'Gross',
  [SalaryRuleCategory.DEDUCTION]: 'Deduction',
  [SalaryRuleCategory.NET]: 'Net',
};

export const SALARY_COMPUTATION_LABELS: Record<SalaryRuleComputation, string> = {
  [SalaryRuleComputation.FIXED]: 'Fixed amount',
  [SalaryRuleComputation.PERCENTAGE]: 'Percentage',
  [SalaryRuleComputation.FORMULA]: 'Formula',
};

export interface SalaryRule {
  id: string;
  name: string;
  code: string;
  category: SalaryRuleCategory;
  sequence: number;
  computation_method: SalaryRuleComputation;
  amount: string | null;
  percentage_base_code: string | null;
  expression: string | null;
  is_active: boolean;
  description: string | null;
  version: number;
}

export interface SalaryRuleInput {
  name: string;
  code: string;
  category: SalaryRuleCategory;
  sequence: number;
  computation_method: SalaryRuleComputation;
  amount: string | null;
  percentage_base_code: string | null;
  expression: string | null;
  is_active: boolean;
  description: string | null;
  version?: number;
}

export interface SalaryStructureRuleLink {
  id: string;
  sequence: number;
  salary_rule: Pick<
    SalaryRule,
    'id' | 'name' | 'code' | 'category' | 'computation_method' | 'is_active'
  >;
}

export interface SalaryStructure {
  id: string;
  name: string;
  code: string;
  is_active: boolean;
  description: string | null;
  rules: SalaryStructureRuleLink[];
  rule_count: number;
  contract_usage_count: number;
  version: number;
}

export interface SalaryStructureInput {
  name: string;
  code: string;
  is_active: boolean;
  description: string | null;
  rules: Array<{ salary_rule_id: string; sequence: number }>;
}
