export type Gender = "male" | "female";

export interface CalculationPolicyInput {
  version: "v1";
  year_boundary: "lichun";
  month_boundary: "solar_terms";
  day_boundary: "midnight";
  time_basis: "local_civil_time";
  true_solar_time: false;
}

export interface ChartPreviewRequest {
  local_datetime: string;
  timezone: string;
  gender: Gender;
  longitude?: number;
  calculation_policy?: CalculationPolicyInput;
}

export interface PillarDetail {
  name: PillarKey;
  gan_zhi: string;
  heavenly_stem: ComponentDetail;
  earthly_branch: BranchDetail;
  na_yin: string;
}

export type PillarKey = "year" | "month" | "day" | "hour";

export interface ComponentDetail {
  symbol: string;
  element: string;
  polarity: "yang" | "yin";
  ten_god?: string | null;
}

export interface BranchDetail extends ComponentDetail {
  hidden_stems: ComponentDetail[];
}

export interface ElementDistribution {
  visible: Record<string, number>;
  hidden_stems: Record<string, number>;
  total: Record<string, number>;
}

export interface CalculationPolicy {
  version?: string;
  year_boundary?: string;
  month_boundary?: string;
  day_boundary?: string;
  time_basis?: string;
  true_solar_time?: boolean;
  policy_version?: string;
}

export interface NormalizedBirthInfo {
  local_datetime: string;
  utc_datetime: string;
  timezone: string;
  gender: Gender | "other";
  longitude?: number | null;
}

export interface ChartPreview {
  normalized_input: NormalizedBirthInfo;
  chart: {
    pillars: Record<PillarKey, PillarDetail>;
    day_master: ComponentDetail;
    element_distribution: ElementDistribution;
  };
  calculation_policy: CalculationPolicy;
  engine: {
    name: string;
    version: string;
    policy_version: string;
    timezone_note: string;
  };
  warnings: string[];
  limitations: string[];
}
