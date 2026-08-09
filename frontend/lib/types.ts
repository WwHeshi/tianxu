export type Gender = "male" | "female";

export type UserRole = "user" | "admin";
export type UserStatus = "active" | "disabled";

export interface CurrentUser {
  id: string;
  username: string;
  display_name: string;
  role: UserRole;
  status: UserStatus;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface LoginResponse {
  user: CurrentUser;
}

export interface BootstrapStatus {
  required: boolean;
}

export interface UserListResponse {
  items: CurrentUser[];
  total: number;
  offset: number;
  limit: number;
}

export interface AdminUserCreate {
  username: string;
  display_name: string;
  temporary_password: string;
  role: UserRole;
}

export interface AdminUserUpdate {
  display_name?: string;
  role?: UserRole;
  status?: UserStatus;
}

export interface CalculationPolicyInput {
  version: "v1";
  year_boundary: "lichun";
  month_boundary: "solar_terms";
  day_boundary: "midnight";
  time_basis: "beijing_standard_time";
  true_solar_time: boolean;
}

export interface ChartPreviewRequest {
  beijing_datetime: string;
  calendar_type: "solar" | "lunar";
  lunar_date: LunarDateInput | null;
  birthplace: BirthplaceInput | null;
  gender: Gender;
  calculation_policy?: CalculationPolicyInput;
}

export interface LunarDateInput {
  year: number;
  month: number;
  day: number;
  is_leap_month: boolean;
}

export interface BirthplaceInput {
  location_id: string;
}

export interface DivisionPathItem {
  code: string;
  name: string;
  type: string;
}

export interface NormalizedBirthplace {
  location_id: string;
  region_code: string;
  timezone: string;
  division_path: DivisionPathItem[];
}

export interface PillarDetail {
  name: PillarKey;
  gan_zhi: string;
  heavenly_stem: ComponentDetail;
  earthly_branch: BranchDetail;
  growth_stage: string;
  self_growth_stage: string;
  xun_kong: string;
  na_yin: string;
  shen_sha: string[];
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

export interface FortunePillarDetail {
  gan_zhi: string;
  heavenly_stem: ComponentDetail;
  earthly_branch: ComponentDetail;
}

export interface BigLuckTransitionDetail {
  solar_datetime: string;
  from_index: number;
  from_gan_zhi: string | null;
  to_index: number;
  to_gan_zhi: string;
}

export interface MonthlyFortuneDetail {
  index: number;
  solar_term: string;
  start_solar_datetime: string;
  segment_start_solar_datetime: string;
  segment_end_solar_datetime: string;
  pillar: FortunePillarDetail;
  big_luck_index_at_start: number;
  big_luck_gan_zhi_at_start: string | null;
  transition_phase: "before" | "after" | null;
  transition: BigLuckTransitionDetail | null;
}

export interface AnnualFortuneDetail {
  index: number;
  year: number;
  nominal_age: number;
  segment_start_solar_datetime: string;
  segment_end_solar_datetime: string;
  pillar: FortunePillarDetail;
  months: MonthlyFortuneDetail[];
  big_luck_index_at_start: number;
  big_luck_gan_zhi_at_start: string | null;
  transition_phase: "before" | "after" | null;
  transition: BigLuckTransitionDetail | null;
}

export interface BigLuckPeriodDetail {
  index: number;
  is_before_start: boolean;
  start_year: number;
  end_year: number;
  start_nominal_age: number;
  end_nominal_age: number;
  start_solar_datetime: string;
  end_solar_datetime: string;
  pillar: FortunePillarDetail | null;
  years: AnnualFortuneDetail[];
}

export interface FortuneCyclesDetail {
  policy_version: string;
  direction: "forward" | "backward";
  start_offset: {
    years: number;
    months: number;
    days: number;
    hours: number;
  };
  start_solar_datetime: string;
  big_luck_periods: BigLuckPeriodDetail[];
}

export interface ChartCalendarDetail {
  solar_datetime: string;
  lunar_year: number;
  lunar_month: number;
  lunar_day: number;
  is_leap_month: boolean;
  lunar_text: string;
  time_branch: string;
  zodiac: string;
  destiny_type: "乾造" | "坤造" | "命造";
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
  beijing_datetime: string;
  true_solar_datetime: string;
  calendar_type: "solar" | "lunar";
  lunar_date: LunarDateInput | null;
  birthplace: NormalizedBirthplace | null;
  gender: Gender | "other";
}

export interface SolarTimeAdjustment {
  longitude_degrees: number;
  latitude_degrees: number | null;
  reference_meridian_degrees: number;
  longitude_correction_minutes: number;
  equation_of_time_minutes: number;
  total_correction_minutes: number;
  location_precision: string;
  coordinate_match: string;
  coordinate_source: string;
}

export interface ChartPreview {
  normalized_input: NormalizedBirthInfo;
  chart: {
    calendar: ChartCalendarDetail;
    pillars: Record<PillarKey, PillarDetail>;
    day_master: ComponentDetail;
    element_distribution: ElementDistribution;
    fortune_cycles: FortuneCyclesDetail | null;
  };
  calculation_policy: CalculationPolicy;
  solar_time_adjustment: SolarTimeAdjustment | null;
  engine: {
    name: string;
    version: string;
    policy_version: string;
    shen_sha_policy_version: string;
    fortune_policy_version: string;
    solar_time_note: string;
  };
  warnings: string[];
  limitations: string[];
}

export type ModelApiProtocol = "responses" | "chat_completions";

export interface ModelSettings {
  configured: boolean;
  provider: string | null;
  api_protocol: ModelApiProtocol | null;
  model: string | null;
  base_url: string | null;
  api_key_masked: string | null;
}

export interface ModelSettingsUpdate {
  provider: "openai";
  api_protocol: ModelApiProtocol;
  model: string;
  base_url: string;
  api_key: string;
}

export interface ModelConnectionTestRequest {
  provider: "openai";
  api_protocol: ModelApiProtocol;
  model: string;
  base_url: string;
  api_key?: string;
}

export interface ModelConnectionTestResponse {
  ok: true;
  provider: string;
  api_protocol: ModelApiProtocol;
  model: string;
  message: string;
}

export interface BaziReport {
  chart_overview: string;
  temperament: string;
  career: string;
  finance: string;
  relationships: string;
  current_fortune: string;
  recommendations: string;
  limitations: string;
}

export interface AgentTraceStep {
  id: string;
  title: string;
  category: "deterministic" | "context" | "prompt" | "model" | "validation";
  status: "completed" | "failed";
  detail: string;
  duration_ms: number | null;
}

export interface AgentDebugTrace {
  steps: AgentTraceStep[];
  system_prompt: string;
  user_prompt: string;
  request: {
    method: "POST";
    endpoint: string;
    provider: string;
    api_protocol: ModelApiProtocol;
    model: string;
    request_count: 1;
    body: Record<string, unknown>;
  };
  raw_response: Record<string, unknown>;
  redacted: string[];
}

export interface ReportGenerationResponse {
  chart: ChartPreview;
  report: BaziReport;
  metadata: {
    provider: string;
    api_protocol: ModelApiProtocol;
    model: string;
    prompt_version: string;
    schema_version: string;
    engine_version: string;
  };
  debug_trace: AgentDebugTrace | null;
}
