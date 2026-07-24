"use client";

import {
  AlertCircle,
  CalendarDays,
  Check,
  ChevronRight,
  Clock3,
  Info,
  LoaderCircle,
  MapPin,
  RotateCcw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { FormEvent, useMemo, useState } from "react";
import { previewChart } from "@/lib/api";
import {
  CHINA_PROVINCES,
  formatChinaBirthplace,
  getChinaDistricts,
  getChinaSecondLevelAreas,
  resolveChinaBirthplace,
} from "@/lib/china-areas";
import type {
  BirthplaceInput,
  CalculationPolicy,
  ChartPreview,
  ChartPreviewRequest,
  Gender,
  PillarDetail,
  PillarKey,
} from "@/lib/types";

const PILLARS: Array<{ key: PillarKey; label: string; description: string }> = [
  { key: "year", label: "年柱", description: "根基与早年" },
  { key: "month", label: "月柱", description: "环境与事业" },
  { key: "day", label: "日柱", description: "日主与关系" },
  { key: "hour", label: "时柱", description: "后天与志向" },
];

const DEFAULT_POLICY = {
  version: "v1" as const,
  year_boundary: "lichun" as const,
  month_boundary: "solar_terms" as const,
  day_boundary: "midnight" as const,
  time_basis: "beijing_standard_time" as const,
  true_solar_time: true as const,
};

const POLICY_LABELS: Record<string, string> = {
  v1: "规则 v1",
  lichun: "立春分年",
  solar_terms: "节气分月",
  midnight: "真太阳时 00:00 换日",
  beijing_standard_time: "北京时间",
};

const LOCATION_PRECISION_LABELS: Record<string, string> = {
  district_centroid: "区县中心点",
  district_center: "区县中心点",
  administrative_center: "行政中心点",
  city_center: "城市行政中心",
  district_service_point: "区级官方服务点",
  geographic_area_representative_point: "地理区域代表点",
  township_area_centroid: "乡镇市区边界质心",
};

const COORDINATE_MATCH_LABELS: Record<string, string> = {
  direct_code: "正式区划代码",
  official_mca_api: "民政部国家地名信息库",
  official_had_service_point: "香港民政事务总署",
  geonames_adm1_direct_id: "GeoNames 澳门地理区域",
  official_boundary_derived_centroid: "台湾官方区界派生数据",
};

function readable(value: unknown, fallback = "暂未提供"): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    for (const key of ["name", "label", "value", "symbol", "text", "char"]) {
      const candidate = readable(record[key], "");
      if (candidate) return candidate;
    }
  }
  return fallback;
}

function formatDateTime(value: unknown): string {
  const text = readable(value, "");
  if (!text) return "暂未提供";
  return text
    .replace("T", " ")
    .replace(/([+-]\d{2}:\d{2}|Z)$/, "")
    .replace(/:00$/, "");
}

function formatLongitude(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "暂未提供";
  const direction = value >= 0 ? "E" : "W";
  const degrees = Math.abs(value).toFixed(4).replace(/\.?0+$/, "");
  return `${degrees}°${direction}`;
}

function formatMinutes(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "暂未提供";
  const minutes = value.toFixed(2);
  return `${value > 0 ? "+" : ""}${minutes} 分钟`;
}

function formatLocationPrecision(value: unknown): string {
  const precision = readable(value, "");
  if (!precision) return "暂未提供";
  return LOCATION_PRECISION_LABELS[precision] ?? precision;
}

function formatCoordinateMatch(value: unknown): string {
  const match = readable(value, "");
  if (!match) return "暂未提供";
  return COORDINATE_MATCH_LABELS[match] ?? match;
}

function getPillar(chart: ChartPreview, key: PillarKey, index: number): Partial<PillarDetail> {
  const raw = chart.chart?.pillars as unknown;
  if (Array.isArray(raw)) return (raw[index] ?? {}) as PillarDetail;
  if (raw && typeof raw === "object") {
    const record = raw as Record<string, PillarDetail | undefined>;
    return record[key] ?? record[`${key}_pillar`] ?? {};
  }
  return {};
}

function getEngineVersion(engine: ChartPreview["engine"]): string {
  if (engine && typeof engine === "object") {
    return [engine.name, engine.version].filter(Boolean).join(" · ") || "已记录";
  }
  return "已记录";
}

function policyValue(policy: CalculationPolicy | undefined, key: keyof CalculationPolicy): string {
  const value = policy?.[key];
  if (typeof value === "boolean") return value ? "开启" : "关闭";
  const text = readable(value, "未说明");
  return POLICY_LABELS[text] ?? text;
}

function formatDistributionValue(value: unknown): string {
  if (typeof value === "number") return `${value}`;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    const count = record.count ?? record.value ?? record.weight;
    if (typeof count === "number") return `${count}`;
  }
  return readable(value, "0");
}

function todayString(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = `${now.getMonth() + 1}`.padStart(2, "0");
  const day = `${now.getDate()}`.padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function BaziWorkbench() {
  const [birthDate, setBirthDate] = useState("1990-01-01");
  const [birthTime, setBirthTime] = useState("12:00");
  const [provinceCode, setProvinceCode] = useState("");
  const [secondLevelCode, setSecondLevelCode] = useState("");
  const [districtCode, setDistrictCode] = useState("");
  const [gender, setGender] = useState<Gender>("male");
  const [chart, setChart] = useState<ChartPreview | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");
  const [lastRequest, setLastRequest] = useState<ChartPreviewRequest | null>(null);

  const maxDate = useMemo(() => todayString(), []);
  const secondLevelOptions = useMemo(
    () => getChinaSecondLevelAreas(provinceCode),
    [provinceCode],
  );
  const selectedSecondLevel = secondLevelOptions.find(
    (item) => item.code === secondLevelCode,
  );
  const districtOptions = useMemo(
    () => getChinaDistricts(secondLevelCode),
    [secondLevelCode],
  );

  function handleProvinceChange(nextProvinceCode: string) {
    const nextAreas = getChinaSecondLevelAreas(nextProvinceCode);
    setProvinceCode(nextProvinceCode);
    setSecondLevelCode(nextAreas.length === 1 ? nextAreas[0].code : "");
    setDistrictCode("");
  }

  function handleSecondLevelChange(nextSecondLevelCode: string) {
    const nextDistricts = getChinaDistricts(nextSecondLevelCode);
    setSecondLevelCode(nextSecondLevelCode);
    setDistrictCode(nextDistricts.length === 1 ? nextDistricts[0].code : "");
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError("");
    setError("");

    if (!birthDate || !birthTime) {
      setFormError("请选择完整的出生日期和时间。");
      return;
    }

    const resolvedBirthplace = resolveChinaBirthplace(
      provinceCode,
      secondLevelCode,
      districtCode,
    );
    if (!resolvedBirthplace) {
      setFormError("请选择完整的出生地区。");
      return;
    }

    const birthplace: BirthplaceInput = resolvedBirthplace;

    const request: ChartPreviewRequest = {
      beijing_datetime: `${birthDate}T${birthTime}:00`,
      birthplace,
      gender,
      calculation_policy: DEFAULT_POLICY,
    };

    setLastRequest(request);
    setIsSubmitting(true);
    setChart(null);
    try {
      setChart(await previewChart(request));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "排盘失败，请稍后重试。");
    } finally {
      setIsSubmitting(false);
    }
  }

  function resetChart() {
    setChart(null);
    setError("");
    setFormError("");
    setLastRequest(null);
  }

  return (
    <main className="workbench-shell">
      <header className="topbar">
        <div className="brand-lockup" aria-label="天序八字排盘">
          <span className="brand-mark" aria-hidden="true"><Sparkles size={17} strokeWidth={1.8} /></span>
          <span className="brand-name">天序</span>
          <span className="brand-divider" aria-hidden="true" />
          <span className="brand-section">八字排盘</span>
        </div>
        <div className="topbar-note"><ShieldCheck size={15} aria-hidden="true" /> 规则可追溯</div>
      </header>

      <div className="workbench-grid">
        <aside className="input-panel" aria-labelledby="input-title">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">BIRTH PROFILE</p>
              <h1 id="input-title">输入出生信息</h1>
            </div>
            <span className="step-badge">01 / 01</span>
          </div>
          <p className="panel-intro">出生时间按北京时间填写，系统会根据出生区县校正为真太阳时。</p>

          <form onSubmit={handleSubmit} noValidate>
            <div className="field-grid">
              <label className="field field-wide">
                <span className="field-label"><CalendarDays size={15} aria-hidden="true" /> 出生日期</span>
                <input
                  type="date"
                  value={birthDate}
                  max={maxDate}
                  onChange={(event) => setBirthDate(event.target.value)}
                  required
                  aria-required="true"
                />
              </label>
              <label className="field field-wide">
                <span className="field-label"><Clock3 size={15} aria-hidden="true" /> 出生时间（北京时间）</span>
                <input
                  type="time"
                  value={birthTime}
                  onChange={(event) => setBirthTime(event.target.value)}
                  required
                  aria-required="true"
                />
              </label>
              <div className="field field-wide">
                <span className="field-label"><MapPin size={15} aria-hidden="true" /> 出生地点</span>
                <div className="location-selects">
                  <select
                    value={provinceCode}
                    onChange={(event) => handleProvinceChange(event.target.value)}
                    aria-label="出生省份"
                  >
                    <option value="">请选择省份 / 直辖市</option>
                    {CHINA_PROVINCES.map((province) => (
                      <option key={province.code} value={province.code}>{province.name}</option>
                    ))}
                  </select>
                  <select
                    value={secondLevelCode}
                    onChange={(event) => handleSecondLevelChange(event.target.value)}
                    disabled={!provinceCode}
                    aria-label="出生城市或区县"
                  >
                    <option value="">请选择城市 / 区县</option>
                    {secondLevelOptions.map((area) => (
                      <option key={area.code} value={area.code}>{area.name}</option>
                    ))}
                  </select>
                  {selectedSecondLevel && !selectedSecondLevel.isTerminal && (
                    <select
                      value={districtCode}
                      onChange={(event) => setDistrictCode(event.target.value)}
                      aria-label="出生区县"
                    >
                      <option value="">请选择区 / 县</option>
                      {districtOptions.map((district) => (
                        <option key={district.code} value={district.code}>{district.name}</option>
                      ))}
                    </select>
                  )}
                </div>
              </div>
              <fieldset className="field field-wide gender-field">
                <legend className="field-label">性别</legend>
                <div className="segmented-control" role="radiogroup" aria-label="性别">
                  {(["male", "female"] as const).map((value) => (
                    <label className={`segment ${gender === value ? "is-selected" : ""}`} key={value}>
                      <input
                        type="radio"
                        name="gender"
                        value={value}
                        checked={gender === value}
                        onChange={() => setGender(value)}
                      />
                      <span>{value === "male" ? "男" : "女"}</span>
                      {gender === value && <Check size={14} aria-hidden="true" />}
                    </label>
                  ))}
                </div>
              </fieldset>
            </div>

            {formError && <p className="form-message" role="alert"><AlertCircle size={15} aria-hidden="true" /> {formError}</p>}

            <button className="primary-button" type="submit" disabled={isSubmitting}>
              {isSubmitting ? <LoaderCircle className="spin" size={17} aria-hidden="true" /> : <ChevronRight size={17} aria-hidden="true" />}
              {isSubmitting ? "正在排盘" : "生成命盘"}
            </button>
          </form>

          <div className="privacy-note">
            <Info size={15} aria-hidden="true" />
            <span>出生资料仅用于本次计算，不会展示在公共页面。</span>
          </div>
        </aside>

        <section className="result-panel" aria-labelledby="result-title" aria-live="polite">
          <div className="result-heading">
            <div>
              <p className="eyebrow">CHART PREVIEW</p>
              <h2 id="result-title">命盘结果</h2>
            </div>
            {chart && (
              <button className="icon-button" type="button" onClick={resetChart} title="重新输入" aria-label="重新输入">
                <RotateCcw size={17} aria-hidden="true" />
              </button>
            )}
          </div>

          {isSubmitting && <LoadingState />}
          {!isSubmitting && error && <ErrorState message={error} onRetry={() => lastRequest && void submitAgain(lastRequest)} />}
          {!isSubmitting && !error && !chart && <EmptyState />}
          {!isSubmitting && !error && chart && <ChartResult chart={chart} />}
        </section>
      </div>
    </main>
  );

  async function submitAgain(request: ChartPreviewRequest) {
    setError("");
    setIsSubmitting(true);
    try {
      setChart(await previewChart(request));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "排盘失败，请稍后重试。");
    } finally {
      setIsSubmitting(false);
    }
  }
}

function EmptyState() {
  return (
    <div className="state-block empty-state">
      <div className="state-icon"><Sparkles size={22} strokeWidth={1.6} aria-hidden="true" /></div>
      <p className="state-title">等待排盘</p>
      <p className="state-copy">完成左侧信息后，四柱结果会显示在这里。</p>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="state-block loading-state">
      <LoaderCircle className="spin" size={24} strokeWidth={1.7} aria-hidden="true" />
      <p className="state-title">正在计算四柱</p>
      <p className="state-copy">正在计算出生地经度修正与均时差。</p>
    </div>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="state-block error-state" role="alert">
      <div className="state-icon error-icon"><AlertCircle size={22} strokeWidth={1.7} aria-hidden="true" /></div>
      <p className="state-title">暂时无法完成排盘</p>
      <p className="state-copy">{message}</p>
      <button className="secondary-button" type="button" onClick={onRetry}><RotateCcw size={15} aria-hidden="true" /> 重试</button>
    </div>
  );
}

function ChartResult({ chart }: { chart: ChartPreview }) {
  const distribution = chart.chart?.element_distribution?.total ?? {};
  const policy = chart.calculation_policy ?? {};
  const normalized = chart.normalized_input ?? {};
  const limitations = Array.isArray(chart.limitations) ? chart.limitations : [];
  const warnings = Array.isArray(chart.warnings) ? chart.warnings : [];
  const maxElementCount = Math.max(1, ...Object.values(distribution));
  const birthplace = formatChinaBirthplace(normalized.birthplace);
  const solarTime = chart.solar_time_adjustment;

  return (
    <div className="chart-content">
      <div className="meta-strip">
        <div><span>北京时间</span><strong>{formatDateTime(normalized.beijing_datetime)}</strong></div>
        <div><span>真太阳时</span><strong>{formatDateTime(normalized.true_solar_datetime)}</strong></div>
        <div><span>出生地点</span><strong className="meta-value-wrap">{birthplace}</strong></div>
        <div><span>性别</span><strong>{normalized.gender === "female" ? "女" : "男"}</strong></div>
      </div>

      <section className="chart-section" aria-labelledby="pillars-title">
        <div className="section-heading"><h3 id="pillars-title">四柱</h3><span>日主 {readable(chart.chart?.day_master)}</span></div>
        <div className="pillars-grid">
          {PILLARS.map(({ key, label, description }, index) => {
            const pillar = getPillar(chart, key, index);
            const stem = readable(pillar.heavenly_stem, "—");
            const branch = readable(pillar.earthly_branch, "—");
            const hidden = Array.isArray(pillar.earthly_branch?.hidden_stems)
              ? pillar.earthly_branch.hidden_stems.map((item) => readable(item, "")).filter(Boolean).join(" · ")
              : "暂未提供";
            const tenGod = readable(pillar.heavenly_stem?.ten_god, "");
            return (
              <article className="pillar" key={key}>
                <div className="pillar-topline"><span className="pillar-label">{label}</span><span>{description}</span></div>
                <div className="pillar-glyphs"><span>{stem}</span><span>{branch}</span></div>
                <div className="pillar-rule" />
                <div className="pillar-detail"><span>藏干</span><strong>{hidden}</strong></div>
                {tenGod && <div className="pillar-detail"><span>十神</span><strong>{tenGod}</strong></div>}
                {pillar.na_yin && <div className="pillar-detail"><span>纳音</span><strong>{readable(pillar.na_yin)}</strong></div>}
              </article>
            );
          })}
        </div>
      </section>

      <section className="solar-time-section" aria-labelledby="solar-time-title">
        <div className="section-heading">
          <h3 id="solar-time-title">真太阳时校正</h3>
          <span>{formatDateTime(normalized.beijing_datetime)} → {formatDateTime(normalized.true_solar_datetime)}</span>
        </div>
        <dl className="solar-time-grid">
          <div><dt>出生地经度</dt><dd>{formatLongitude(solarTime?.longitude_degrees)}</dd></div>
          <div><dt>标准经线</dt><dd>{formatLongitude(solarTime?.reference_meridian_degrees)}</dd></div>
          <div><dt>经度修正</dt><dd>{formatMinutes(solarTime?.longitude_correction_minutes)}</dd></div>
          <div><dt>均时差</dt><dd>{formatMinutes(solarTime?.equation_of_time_minutes)}</dd></div>
          <div><dt>总修正</dt><dd>{formatMinutes(solarTime?.total_correction_minutes)}</dd></div>
          <div><dt>地点精度</dt><dd>{formatLocationPrecision(solarTime?.location_precision)}</dd></div>
          <div><dt>坐标匹配</dt><dd>{formatCoordinateMatch(solarTime?.coordinate_match)}</dd></div>
          <div><dt>坐标数据</dt><dd>{readable(solarTime?.coordinate_source)}</dd></div>
        </dl>
      </section>

      <div className="detail-grid">
        <section className="detail-section" aria-labelledby="elements-title">
          <div className="section-heading"><h3 id="elements-title">五行分布</h3><span>命盘基础信息</span></div>
          {Object.keys(distribution).length > 0 ? (
            <div className="elements-list">
              {Object.entries(distribution).map(([element, value]) => (
                <div className="element-row" key={element}>
                  <span>{element}</span>
                  <div className="element-track"><span style={{ width: `${Math.max(4, Math.round((Number(value) / maxElementCount) * 100))}%` }} /></div>
                  <strong>{formatDistributionValue(value)}</strong>
                </div>
              ))}
            </div>
          ) : <p className="muted-copy">后端暂未返回五行统计。</p>}
        </section>

        <section className="detail-section" aria-labelledby="policy-title">
          <div className="section-heading"><h3 id="policy-title">计算口径</h3><span>本次排盘记录</span></div>
          <dl className="policy-list">
            <div><dt>年份边界</dt><dd>{policyValue(policy, "year_boundary")}</dd></div>
            <div><dt>月份边界</dt><dd>{policyValue(policy, "month_boundary")}</dd></div>
            <div><dt>换日方式</dt><dd>{policyValue(policy, "day_boundary")}</dd></div>
            <div><dt>时间基准</dt><dd>{policyValue(policy, "time_basis")}</dd></div>
            <div><dt>真太阳时</dt><dd>{policyValue(policy, "true_solar_time")}</dd></div>
            <div><dt>引擎版本</dt><dd>{getEngineVersion(chart.engine)}</dd></div>
          </dl>
        </section>
      </div>

      {warnings.length > 0 && (
        <aside className="warnings" aria-label="计算提示">
          <AlertCircle size={16} aria-hidden="true" />
          <div><strong>计算提示</strong><ul>{warnings.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div>
        </aside>
      )}

      {limitations.length > 0 && (
        <aside className="limitations" aria-label="结果说明">
          <Info size={16} aria-hidden="true" />
          <div><strong>结果说明</strong><ul>{limitations.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div>
        </aside>
      )}
    </div>
  );
}
