"use client";

import {
  AlertCircle,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  Clock3,
  FileText,
  FlaskConical,
  Info,
  KeyRound,
  LibraryBig,
  LoaderCircle,
  LogOut,
  MapPin,
  PlugZap,
  RotateCcw,
  Settings,
  ShieldCheck,
  Sparkles,
  Trash2,
  Users,
  Workflow,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { AgentDebugModal as SharedAgentDebugModal } from "@/components/agent-debug-modal";
import {
  deleteModelSettings,
  generateReport,
  getModelSettings,
  previewChart,
  ReportGenerationError,
  saveModelSettings,
  testModelConnection,
} from "@/lib/api";
import { DateWheelPicker, TimeWheelPicker } from "@/components/date-wheel-picker";
import {
  formatSolarDate,
  lunarToSolar,
  solarToLunar,
  type CalendarType,
  type LunarDateParts,
} from "@/lib/lunar-calendar";
import {
  CHINA_PROVINCES,
  formatChinaBirthplace,
  getChinaDistricts,
  getChinaSecondLevelAreas,
  resolveChinaBirthplace,
} from "@/lib/china-areas";
import type {
  AgentDebugTrace,
  BirthplaceInput,
  AnnualFortuneDetail,
  BigLuckPeriodDetail,
  ChartPreview,
  ChartPreviewRequest,
  CurrentUser,
  FortuneCyclesDetail,
  FortunePillarDetail,
  Gender,
  MonthlyFortuneDetail,
  ModelApiProtocol,
  ModelSettings,
  PillarDetail,
  PillarKey,
  ReportGenerationResponse,
} from "@/lib/types";

const PILLARS: Array<{ key: PillarKey; label: string; description: string }> = [
  { key: "year", label: "年柱", description: "根基与早年" },
  { key: "month", label: "月柱", description: "环境与事业" },
  { key: "day", label: "日柱", description: "日主与关系" },
  { key: "hour", label: "时柱", description: "后天与志向" },
];

const DEFAULT_POLICY = {
  version: "v2" as const,
  year_boundary: "lichun" as const,
  month_boundary: "solar_terms" as const,
  day_boundary: "zi_hour_start" as const,
  time_basis: "beijing_standard_time" as const,
  true_solar_time: true as const,
};

function normalizeModelBaseUrl(value: string): string {
  return value.trim().replace(/\/(responses|chat\/completions)\/?$/, "");
}

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

function getPillar(chart: ChartPreview, key: PillarKey, index: number): Partial<PillarDetail> {
  const raw = chart.chart?.pillars as unknown;
  if (Array.isArray(raw)) return (raw[index] ?? {}) as PillarDetail;
  if (raw && typeof raw === "object") {
    const record = raw as Record<string, PillarDetail | undefined>;
    return record[key] ?? record[`${key}_pillar`] ?? {};
  }
  return {};
}

const BEIJING_OFFSET_MS = 8 * 60 * 60 * 1000;

function todayString(): string {
  const beijingNow = new Date(Date.now() + BEIJING_OFFSET_MS);
  return beijingNow.toISOString().slice(0, 10);
}

function beijingNowString(): string {
  return new Date(Date.now() + BEIJING_OFFSET_MS).toISOString().slice(0, 19);
}

function millisecondsUntilNextBeijingDay(): number {
  const beijingNow = new Date(Date.now() + BEIJING_OFFSET_MS);
  const nextMidnight = Date.UTC(
    beijingNow.getUTCFullYear(),
    beijingNow.getUTCMonth(),
    beijingNow.getUTCDate() + 1,
  );
  return Math.max(1_000, nextMidnight - beijingNow.getTime() + 1_000);
}

function useBeijingToday(): string {
  const [today, setToday] = useState(todayString);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const scheduleRefresh = () => {
      timer = setTimeout(() => {
        setToday(todayString());
        scheduleRefresh();
      }, millisecondsUntilNextBeijingDay());
    };
    scheduleRefresh();
    return () => clearTimeout(timer);
  }, []);

  return today;
}

export function BaziWorkbench({
  currentUser,
  onLogout,
}: {
  currentUser: CurrentUser;
  onLogout: () => void;
}) {
  const [calendarType, setCalendarType] = useState<CalendarType>("solar");
  const [birthDate, setBirthDate] = useState<LunarDateParts>({
    year: 1990,
    month: 1,
    day: 1,
    isLeapMonth: false,
  });
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
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [modelSettings, setModelSettings] = useState<ModelSettings | null>(null);

  useEffect(() => {
    if (currentUser.role !== "admin") return;
    let active = true;
    void getModelSettings()
      .then((settings) => {
        if (active) setModelSettings(settings);
      })
      .catch(() => {
        // Settings may be intentionally disabled outside local development.
      });
    return () => {
      active = false;
    };
  }, [currentUser.role]);

  const maxDate = useBeijingToday();
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

  function handleCalendarTypeChange(nextType: CalendarType) {
    if (nextType === calendarType) return;
    setBirthDate((current) =>
      nextType === "lunar"
        ? solarToLunar(current)
        : { ...lunarToSolar(current), isLeapMonth: false },
    );
    setCalendarType(nextType);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError("");
    setError("");

    if (!birthTime) {
      setFormError("请选择完整的出生日期和时间。");
      return;
    }

    let birthplace: BirthplaceInput | null = null;
    if (provinceCode) {
      const resolvedBirthplace = resolveChinaBirthplace(
        provinceCode,
        secondLevelCode,
        districtCode,
      );
      if (!resolvedBirthplace) {
        setFormError("请选择完整的出生地区。");
        return;
      }
      birthplace = resolvedBirthplace;
    }

    const solarBirthDate =
      calendarType === "lunar" ? lunarToSolar(birthDate) : birthDate;
    const request: ChartPreviewRequest = {
      beijing_datetime: `${formatSolarDate(solarBirthDate)}T${birthTime}:00`,
      calendar_type: calendarType,
      lunar_date:
        calendarType === "lunar"
          ? {
              year: birthDate.year,
              month: birthDate.month,
              day: birthDate.day,
              is_leap_month: birthDate.isLeapMonth,
            }
          : null,
      birthplace,
      gender,
      calculation_policy: {
        ...DEFAULT_POLICY,
        true_solar_time: birthplace !== null,
      },
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
        <div className="topbar-actions">
          {currentUser.role === "admin" && (
            <>
              <Link className="settings-button" href="/admin/knowledge">
                <LibraryBig size={16} aria-hidden="true" />
                <span>知识库</span>
              </Link>
              <Link className="settings-button" href="/admin/evaluations">
                <FlaskConical size={16} aria-hidden="true" />
                <span>评测中心</span>
              </Link>
              <Link className="settings-button" href="/admin/users">
                <Users size={16} aria-hidden="true" />
                <span>用户管理</span>
              </Link>
              <button
                className="settings-button"
                type="button"
                onClick={() => setSettingsOpen(true)}
                aria-label="打开模型 API 设置"
              >
                <Settings size={16} aria-hidden="true" />
                <span>设置 API</span>
              </button>
            </>
          )}
          <span className="current-user" title={currentUser.username}>
            {currentUser.display_name}<small>{currentUser.role === "admin" ? "管理员" : "用户"}</small>
          </span>
          <button className="logout-button" type="button" onClick={onLogout} aria-label="退出登录">
            <LogOut size={16} aria-hidden="true" />
          </button>
        </div>
      </header>

      {settingsOpen && (
        <ModelSettingsModal
          current={modelSettings}
          onClose={() => setSettingsOpen(false)}
          onChange={setModelSettings}
        />
      )}

      <div className="workbench-grid">
        <aside className="input-panel" aria-labelledby="input-title">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">BIRTH PROFILE</p>
              <h1 id="input-title">输入出生信息</h1>
            </div>
          </div>
          <p className="panel-intro">选择出生地点后，系统会根据出生区县校正为真太阳时。</p>

          <form onSubmit={handleSubmit} noValidate>
            <div className="field-grid">
              <div className="field field-wide">
                <div className="field-label-row">
                  <span className="field-label" id="birth-date-label"><CalendarDays size={15} aria-hidden="true" /> 出生日期</span>
                  <div
                    className="segmented-control calendar-type-control"
                    role="radiogroup"
                    aria-label="出生日期历法"
                  >
                    {(["solar", "lunar"] as const).map((value) => (
                      <label
                        className={`segment ${calendarType === value ? "is-selected" : ""}`}
                        key={value}
                      >
                        <input
                          type="radio"
                          name="calendar-type"
                          value={value}
                          checked={calendarType === value}
                          onChange={() => handleCalendarTypeChange(value)}
                        />
                        <span>{value === "solar" ? "公历" : "农历"}</span>
                      </label>
                    ))}
                  </div>
                </div>
                <DateWheelPicker
                  key={calendarType}
                  calendarType={calendarType}
                  value={birthDate}
                  maxDate={maxDate}
                  onChange={setBirthDate}
                  labelledBy="birth-date-label"
                />
              </div>
              <div className="field field-wide">
                <span className="field-label" id="birth-time-label"><Clock3 size={15} aria-hidden="true" /> 出生时间</span>
                <TimeWheelPicker
                  value={birthTime}
                  onChange={setBirthTime}
                  labelledBy="birth-time-label"
                />
              </div>
              <div className="field field-wide">
                <span className="field-label"><MapPin size={15} aria-hidden="true" /> 出生地点</span>
                <div className="location-selects">
                  <select
                    value={provinceCode}
                    onChange={(event) => handleProvinceChange(event.target.value)}
                    aria-label="出生省份"
                  >
                    <option value="">未选择地点（按北京时间）</option>
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
          {!isSubmitting && !error && chart && lastRequest && (
            <ChartResult
              chart={chart}
              request={lastRequest}
              onOpenSettings={
                currentUser.role === "admin" ? () => setSettingsOpen(true) : undefined
              }
            />
          )}
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

function ModelSettingsModal({
  current,
  onClose,
  onChange,
}: {
  current: ModelSettings | null;
  onClose: () => void;
  onChange: (settings: ModelSettings) => void;
}) {
  const [model, setModel] = useState(current?.model ?? "");
  const [apiProtocol, setApiProtocol] = useState<ModelApiProtocol>(
    current?.api_protocol ?? "responses",
  );
  const [baseUrl, setBaseUrl] = useState(current?.base_url ?? "https://api.openai.com/v1");
  const [apiKey, setApiKey] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [isLoading, setIsLoading] = useState(current === null);

  useEffect(() => {
    if (current === null) return;
    setModel(current.model ?? "");
    setApiProtocol(current.api_protocol ?? "responses");
    setBaseUrl(current.base_url ?? "https://api.openai.com/v1");
    setIsLoading(false);
  }, [current]);

  useEffect(() => {
    if (current !== null) return;
    let active = true;
    void getModelSettings()
      .then((settings) => {
        if (!active) return;
        onChange(settings);
        setModel(settings.model ?? "");
        setApiProtocol(settings.api_protocol ?? "responses");
        setBaseUrl(settings.base_url ?? "https://api.openai.com/v1");
      })
      .catch((loadError) => {
        if (active) {
          setErrorMessage(loadError instanceof Error ? loadError.message : "无法读取模型设置。");
        }
      })
      .finally(() => {
        if (active) setIsLoading(false);
      });
    return () => {
      active = false;
    };
  }, [current, onChange]);

  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape" && !isSaving && !isTesting) onClose();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [isSaving, isTesting, onClose]);

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setErrorMessage("");
    setStatusMessage("");
    if (!model.trim() || !baseUrl.trim() || !apiKey.trim()) {
      setErrorMessage("请填写模型 ID、Base URL 和新的 API 密钥。");
      return;
    }
    setIsSaving(true);
    try {
      const saved = await saveModelSettings({
        provider: "openai",
        api_protocol: apiProtocol,
        model: model.trim(),
        base_url: normalizeModelBaseUrl(baseUrl),
        api_key: apiKey.trim(),
      });
      onChange(saved);
      setApiKey("");
      setStatusMessage("设置已保存。关闭窗口后即可生成报告。");
    } catch (saveError) {
      setErrorMessage(saveError instanceof Error ? saveError.message : "保存失败，请稍后重试。");
    } finally {
      setIsSaving(false);
    }
  }

  async function handleTestConnection() {
    setErrorMessage("");
    setStatusMessage("");
    if (!model.trim() || !baseUrl.trim()) {
      setErrorMessage("请先填写模型 ID 和 Base URL。");
      return;
    }
    if (!apiKey.trim() && !current?.configured) {
      setErrorMessage("请先输入 API 密钥。");
      return;
    }
    setIsTesting(true);
    try {
      const tested = await testModelConnection({
        provider: "openai",
        api_protocol: apiProtocol,
        model: model.trim(),
        base_url: normalizeModelBaseUrl(baseUrl),
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      });
      setStatusMessage(tested.message);
    } catch (testError) {
      setErrorMessage(
        testError instanceof Error ? testError.message : "连接测试失败，请稍后重试。",
      );
    } finally {
      setIsTesting(false);
    }
  }

  async function handleDelete() {
    if (!window.confirm("确定清除当前模型设置吗？此操作不会显示或恢复原密钥。")) return;
    setErrorMessage("");
    setStatusMessage("");
    setIsSaving(true);
    try {
      await deleteModelSettings();
      onChange({
        configured: false,
        provider: null,
        api_protocol: null,
        model: null,
        base_url: null,
        api_key_masked: null,
      });
      setModel("");
      setApiKey("");
      setStatusMessage("模型设置已清除。");
    } catch (deleteError) {
      setErrorMessage(deleteError instanceof Error ? deleteError.message : "清除失败，请稍后重试。");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !isSaving && !isTesting) onClose();
    }}>
      <section className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <div className="modal-heading">
          <div>
            <p className="eyebrow">MODEL CONNECTION</p>
            <h2 id="settings-title">模型 API 设置</h2>
          </div>
          <button className="icon-button" type="button" onClick={onClose} disabled={isSaving || isTesting} aria-label="关闭设置">
            <X size={18} aria-hidden="true" />
          </button>
        </div>

        {isLoading ? (
          <div className="settings-loading"><LoaderCircle className="spin" size={20} /> 正在读取设置</div>
        ) : (
          <form className="settings-form" onSubmit={handleSave}>
            <div className="credential-status" data-configured={current?.configured || undefined}>
              <KeyRound size={17} aria-hidden="true" />
              <div>
                <strong>{current?.configured ? "API 密钥已配置" : "尚未配置 API 密钥"}</strong>
                <span>{current?.configured ? current.api_key_masked : "密钥保存后不会再次返回到浏览器"}</span>
              </div>
            </div>

            <label className="settings-field">
              <span>API 协议</span>
              <select
                value={apiProtocol}
                onChange={(event) => setApiProtocol(event.target.value as ModelApiProtocol)}
              >
                <option value="responses">OpenAI Responses API</option>
                <option value="chat_completions">OpenAI Chat Completions</option>
              </select>
              <small>
                请求地址：{normalizeModelBaseUrl(baseUrl) || "Base URL"}
                {apiProtocol === "responses" ? "/responses" : "/chat/completions"}
              </small>
            </label>
            <label className="settings-field">
              <span>模型 ID</span>
              <input
                value={model}
                onChange={(event) => setModel(event.target.value)}
                placeholder="填写账户可用的模型 ID"
                autoComplete="off"
              />
            </label>
            <label className="settings-field">
              <span>Base URL</span>
              <input
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="https://api.openai.com/v1"
                inputMode="url"
                autoComplete="url"
              />
            </label>
            <label className="settings-field">
              <span>{current?.configured ? "新 API 密钥（保存时替换）" : "API 密钥"}</span>
              <input
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={current?.configured ? "输入新密钥以替换现有密钥" : "输入 API 密钥"}
                type="password"
                autoComplete="new-password"
              />
            </label>

            <p className="settings-security-note">
              密钥会在服务端加密后存入 PostgreSQL；测试连接会发送一条极短请求，可能产生少量 token。
            </p>
            {errorMessage && <p className="form-message" role="alert"><AlertCircle size={15} /> {errorMessage}</p>}
            {statusMessage && <p className="settings-success" role="status"><CheckCircle2 size={15} /> {statusMessage}</p>}

            <div className="settings-actions">
              {current?.configured && (
                <button className="danger-button" type="button" onClick={handleDelete} disabled={isSaving || isTesting}>
                  <Trash2 size={15} /> 清除设置
                </button>
              )}
              <button className="test-button" type="button" onClick={handleTestConnection} disabled={isSaving || isTesting}>
                {isTesting ? <LoaderCircle className="spin" size={16} /> : <PlugZap size={16} />}
                {isTesting ? "正在测试" : "测试连接"}
              </button>
              <button className="primary-button settings-save" type="submit" disabled={isSaving || isTesting}>
                {isSaving ? <LoaderCircle className="spin" size={16} /> : <ShieldCheck size={16} />}
                {isSaving ? "正在保存" : "保存"}
              </button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
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

function shortMonthDay(value: string): string {
  const match = value.match(/^\d{4}-(\d{2})-(\d{2})/);
  return match ? `${Number(match[1])}/${Number(match[2])}` : "—";
}

function transitionPhaseLabel(phase: "before" | "after" | null): string | null {
  if (phase === "before") return "交运前";
  if (phase === "after") return "交运后";
  return null;
}

function bigLuckName(ganZhi: string | null): string {
  return ganZhi ? `${ganZhi}大运` : "起运前";
}

function FortunePillarCell({ pillar }: { pillar: FortunePillarDetail }) {
  return (
    <div className="fortune-pillar">
      <span>
        <strong data-element={pillar.heavenly_stem.element}>{pillar.heavenly_stem.symbol}</strong>
        <small>{readable(pillar.heavenly_stem.ten_god, "—")}</small>
      </span>
      <span>
        <strong data-element={pillar.earthly_branch.element}>{pillar.earthly_branch.symbol}</strong>
        <small>{readable(pillar.earthly_branch.ten_god, "—")}</small>
      </span>
    </div>
  );
}

function useHorizontalScroller(selection: string | number) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const scroller = ref.current;
    const selected = scroller?.querySelector<HTMLElement>('[aria-pressed="true"]');
    if (!scroller || !selected) return;
    const scrollerRect = scroller.getBoundingClientRect();
    const selectedRect = selected.getBoundingClientRect();
    const selectedCenter = (
      selectedRect.left
      - scrollerRect.left
      + scroller.scrollLeft
      + selectedRect.width / 2
    );
    const maxScrollLeft = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
    const target = Math.min(maxScrollLeft, Math.max(0, selectedCenter - scroller.clientWidth / 2));
    scroller.scrollTo({ left: target, behavior: "smooth" });
  }, [selection]);

  useEffect(() => {
    const scrollElement = ref.current;
    if (!scrollElement) return;
    const scroller: HTMLDivElement = scrollElement;

    let pointerId: number | null = null;
    let startX = 0;
    let startScrollLeft = 0;
    let dragged = false;
    let suppressClick = false;
    let suppressClickTimer: number | null = null;

    function handlePointerDown(event: PointerEvent) {
      if (
        event.button !== 0
        || pointerId !== null
        || scroller.scrollWidth <= scroller.clientWidth
      ) return;
      pointerId = event.pointerId;
      startX = event.clientX;
      startScrollLeft = scroller.scrollLeft;
      dragged = false;
    }

    function handlePointerMove(event: PointerEvent) {
      if (event.pointerId !== pointerId) return;
      const distance = event.clientX - startX;
      if (!dragged && Math.abs(distance) < 4) return;
      dragged = true;
      scroller.dataset.dragging = "true";
      scroller.scrollLeft = startScrollLeft - distance;
      event.preventDefault();
    }

    function finishPointer(event: PointerEvent, shouldSuppressClick: boolean) {
      if (event.pointerId !== pointerId) return;
      pointerId = null;
      suppressClick = shouldSuppressClick && dragged;
      dragged = false;
      delete scroller.dataset.dragging;
      if (suppressClick) {
        if (suppressClickTimer !== null) window.clearTimeout(suppressClickTimer);
        suppressClickTimer = window.setTimeout(() => {
          suppressClick = false;
          suppressClickTimer = null;
        }, 0);
      }
    }

    function handlePointerUp(event: PointerEvent) {
      finishPointer(event, true);
    }

    function handlePointerCancel(event: PointerEvent) {
      finishPointer(event, false);
    }

    function handleClick(event: MouseEvent) {
      if (!suppressClick) return;
      suppressClick = false;
      event.preventDefault();
      event.stopPropagation();
    }

    function handleWheel(event: WheelEvent) {
      if (
        Math.abs(event.deltaY) <= Math.abs(event.deltaX)
        || scroller.scrollWidth <= scroller.clientWidth
      ) return;
      const maxScrollLeft = scroller.scrollWidth - scroller.clientWidth;
      const nextScrollLeft = Math.min(
        maxScrollLeft,
        Math.max(0, scroller.scrollLeft + event.deltaY),
      );
      if (nextScrollLeft === scroller.scrollLeft) return;
      event.preventDefault();
      scroller.scrollLeft = nextScrollLeft;
    }

    scroller.addEventListener("pointerdown", handlePointerDown);
    scroller.addEventListener("click", handleClick, true);
    scroller.addEventListener("wheel", handleWheel, { passive: false });
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("pointercancel", handlePointerCancel);
    return () => {
      if (suppressClickTimer !== null) window.clearTimeout(suppressClickTimer);
      scroller.removeEventListener("pointerdown", handlePointerDown);
      scroller.removeEventListener("click", handleClick, true);
      scroller.removeEventListener("wheel", handleWheel);
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("pointercancel", handlePointerCancel);
    };
  }, []);

  return ref;
}

interface InitialFortuneSelection {
  periodIndex: number;
  year: number;
  monthIndex: number;
  currentPeriodIndex: number;
  currentYear: number;
  currentMonthIndex: number;
}

function initialFortuneSelection(cycles: FortuneCyclesDetail): InitialFortuneSelection {
  const now = beijingNowString();
  const periods = cycles.big_luck_periods;
  const actualCurrentPeriod = periods.find(
    (period) => now >= period.start_solar_datetime && now < period.end_solar_datetime,
  ) ?? (now < (periods[0]?.start_solar_datetime ?? now) ? periods[0] : periods.at(-1));
  const activeAnnual = actualCurrentPeriod?.years.find(
    (annual) => now >= annual.segment_start_solar_datetime && now < annual.segment_end_solar_datetime,
  ) ?? (now < (actualCurrentPeriod?.years[0]?.segment_start_solar_datetime ?? now)
    ? actualCurrentPeriod?.years[0]
    : actualCurrentPeriod?.years.at(-1));
  const activeMonth =
    activeAnnual?.months.find(
      (month) => now >= month.segment_start_solar_datetime && now < month.segment_end_solar_datetime,
    )
    ?? activeAnnual?.months[0];

  return {
    periodIndex: actualCurrentPeriod?.index ?? 0,
    year: activeAnnual?.year ?? periods[0]?.start_year ?? new Date().getFullYear(),
    monthIndex: activeMonth?.index ?? 1,
    currentPeriodIndex: actualCurrentPeriod?.index ?? 0,
    currentYear: activeAnnual?.year ?? periods[0]?.start_year ?? new Date().getFullYear(),
    currentMonthIndex: activeMonth?.index ?? 1,
  };
}

function FortuneSelector({ cycles }: { cycles: FortuneCyclesDetail }) {
  const initial = useMemo(() => initialFortuneSelection(cycles), [cycles]);
  const [periodIndex, setPeriodIndex] = useState(initial.periodIndex);
  const [selectedYear, setSelectedYear] = useState(initial.year);
  const [monthIndex, setMonthIndex] = useState(initial.monthIndex);
  const period = cycles.big_luck_periods.find((item) => item.index === periodIndex)
    ?? cycles.big_luck_periods[0];
  const annual = period?.years.find((item) => item.year === selectedYear) ?? period?.years[0];
  const month = annual?.months.find((item) => item.index === monthIndex) ?? annual?.months[0];
  const bigLuckScrollRef = useHorizontalScroller(periodIndex);
  const annualScrollRef = useHorizontalScroller(selectedYear);
  const monthScrollRef = useHorizontalScroller(monthIndex);
  const offset = cycles.start_offset;

  function selectPeriod(nextPeriod: BigLuckPeriodDetail) {
    setPeriodIndex(nextPeriod.index);
    if (nextPeriod.index === initial.currentPeriodIndex) {
      const currentAnnual = nextPeriod.years.find(
        (item) => item.year === initial.currentYear,
      ) ?? nextPeriod.years[0];
      if (!currentAnnual) return;
      setSelectedYear(currentAnnual.year);
      const currentMonth = currentAnnual.months.find(
        (item) => item.index === initial.currentMonthIndex,
      ) ?? currentAnnual.months[0];
      setMonthIndex(currentMonth?.index ?? 1);
      return;
    }
    const nextAnnual = nextPeriod.years.find((item) => item.year === selectedYear)
      ?? nextPeriod.years.find((item) => item.year === initial.currentYear)
      ?? nextPeriod.years[0];
    if (!nextAnnual) return;
    setSelectedYear(nextAnnual.year);
    const nextMonth = nextAnnual.months.find((item) => item.index === monthIndex)
      ?? (nextAnnual.year === initial.currentYear
        ? nextAnnual.months.find((item) => item.index === initial.currentMonthIndex)
        : undefined)
      ?? nextAnnual.months[0];
    setMonthIndex(nextMonth?.index ?? 1);
  }

  function selectAnnual(nextAnnual: AnnualFortuneDetail) {
    setSelectedYear(nextAnnual.year);
    const nextMonth = nextAnnual.year === initial.currentYear
      ? nextAnnual.months.find((item) => item.index === initial.currentMonthIndex)
      : nextAnnual.months[0];
    setMonthIndex(nextMonth?.index ?? 1);
  }

  if (!period || !annual || !month) return null;
  const selectedTransition = month.transition;
  const selectedTransitionPhase = transitionPhaseLabel(month.transition_phase);

  return (
    <section className="fortune-section" aria-labelledby="fortune-title">
      <div className="section-heading">
        <h3 id="fortune-title">运势周期</h3>
        <span>大运 · 流年 · 流月</span>
      </div>
      <div className="fortune-summary">
        <span>起运：出生后 {offset.years} 年 {offset.months} 月 {offset.days} 天 {offset.hours} 小时</span>
        <span>交运：{formatDateTime(cycles.start_solar_datetime)}</span>
        <span>年龄：虚岁</span>
        <strong>{cycles.direction === "forward" ? "顺行" : "逆行"}</strong>
      </div>
      <div className={`fortune-boundary-summary ${selectedTransitionPhase ? "is-transition" : ""}`}>
        <strong>{selectedTransitionPhase ?? "所处大运"}</strong>
        {selectedTransition ? (
          <span>
            {bigLuckName(selectedTransition.from_gan_zhi)} → {bigLuckName(selectedTransition.to_gan_zhi)}
            <small>{formatDateTime(selectedTransition.solar_datetime)} 交运</small>
          </span>
        ) : (
          <span>{bigLuckName(month.big_luck_gan_zhi_at_start)}</span>
        )}
      </div>

      <div className="fortune-row">
        <div className="fortune-row-label" aria-hidden="true"><span>大</span><span>运</span></div>
        <div className="fortune-scroll" ref={bigLuckScrollRef} role="group" aria-label="选择大运">
          {cycles.big_luck_periods.map((item) => {
            const selected = item.index === period.index;
            const current = item.index === initial.currentPeriodIndex;
            return (
              <button
                className="fortune-cell fortune-big-cell"
                data-current={current || undefined}
                aria-pressed={selected}
                type="button"
                key={item.index}
                title={item.is_before_start ? "出生至首次交运" : `${formatDateTime(item.start_solar_datetime)} 交运`}
                onClick={() => selectPeriod(item)}
              >
                <span className="fortune-cell-year">{item.start_year}</span>
                <small>{item.is_before_start ? `${item.start_nominal_age}~${item.end_nominal_age}岁` : `${item.start_nominal_age}岁`}</small>
                {item.pillar ? <FortunePillarCell pillar={item.pillar} /> : <strong className="before-luck-label">起运前</strong>}
                {current && <i className="fortune-current-dot" aria-label="当前大运" />}
              </button>
            );
          })}
        </div>
      </div>

      <div className="fortune-row">
        <div className="fortune-row-label" aria-hidden="true"><span>流</span><span>年</span></div>
        <div className="fortune-scroll" ref={annualScrollRef} role="group" aria-label="选择流年">
          {period.years.map((item) => {
            const selected = item.year === annual.year;
            const current = period.index === initial.currentPeriodIndex && item.year === initial.currentYear;
            const phaseLabel = transitionPhaseLabel(item.transition_phase);
            return (
              <button
                className="fortune-cell"
                data-current={current || undefined}
                aria-pressed={selected}
                type="button"
                key={item.year}
                onClick={() => selectAnnual(item)}
              >
                <span className="fortune-cell-year">{item.year}</span>
                <small>{item.nominal_age}岁</small>
                <FortunePillarCell pillar={item.pillar} />
                {phaseLabel && <em className="fortune-transition-badge">{phaseLabel}</em>}
                {current && <i className="fortune-current-dot" aria-label="当前流年" />}
              </button>
            );
          })}
        </div>
      </div>

      <div className="fortune-row">
        <div className="fortune-row-label" aria-hidden="true"><span>流</span><span>月</span></div>
        <div className="fortune-scroll" ref={monthScrollRef} role="group" aria-label="选择流月">
          {annual.months.map((item: MonthlyFortuneDetail) => {
            const selected = item.index === month.index;
            const current = period.index === initial.currentPeriodIndex
              && annual.year === initial.currentYear
              && item.index === initial.currentMonthIndex;
            const phaseLabel = transitionPhaseLabel(item.transition_phase);
            return (
              <button
                className="fortune-cell"
                data-current={current || undefined}
                aria-pressed={selected}
                type="button"
                key={item.index}
                onClick={() => setMonthIndex(item.index)}
              >
                <span className="fortune-cell-year">{item.solar_term}</span>
                <small>{shortMonthDay(item.start_solar_datetime)}</small>
                <FortunePillarCell pillar={item.pillar} />
                {phaseLabel && <em className="fortune-transition-badge">{phaseLabel}</em>}
                {current && <i className="fortune-current-dot" aria-label="当前流月" />}
              </button>
            );
          })}
        </div>
      </div>
    </section>
  );
}

const REPORT_SECTIONS: Array<{
  key: keyof ReportGenerationResponse["report"];
  label: string;
}> = [
  { key: "chart_overview", label: "命盘概览" },
  { key: "temperament", label: "性情特点" },
  { key: "career", label: "能力与事业" },
  { key: "finance", label: "财务倾向" },
  { key: "relationships", label: "关系模式" },
  { key: "current_fortune", label: "当前运势周期" },
  { key: "recommendations", label: "综合建议" },
  { key: "limitations", label: "局限说明" },
];

function AgentDebugModal({
  trace,
  onClose,
}: {
  trace: AgentDebugTrace;
  onClose: () => void;
}) {
  const protocolLabel = trace.request.api_protocol === "responses"
    ? "OpenAI Responses"
    : "OpenAI Chat Completions";

  return (
    <SharedAgentDebugModal
      apiProtocol={trace.request.api_protocol}
      protocolLabel={protocolLabel}
      model={trace.request.model}
      modelCallCount={trace.request.request_count}
      toolExecutionCount={trace.tool_executions.length}
      endpoint={trace.request.endpoint}
      modelCalls={trace.model_calls}
      redacted={trace.redacted}
      onClose={onClose}
    />
  );
}

function ReportGenerator({
  request,
  onOpenSettings,
}: {
  request: ChartPreviewRequest;
  onOpenSettings?: () => void;
}) {
  const [result, setResult] = useState<ReportGenerationResponse | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [reportError, setReportError] = useState("");
  const [failedTrace, setFailedTrace] = useState<AgentDebugTrace | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);

  async function handleGenerate() {
    setReportError("");
    setFailedTrace(null);
    setResult(null);
    setTraceOpen(false);
    setIsGenerating(true);
    try {
      setResult(await generateReport(request));
    } catch (generationError) {
      if (generationError instanceof ReportGenerationError) {
        setFailedTrace(generationError.debugTrace);
      }
      setReportError(
        generationError instanceof Error ? generationError.message : "报告生成失败，请稍后重试。",
      );
    } finally {
      setIsGenerating(false);
    }
  }

  const activeTrace = result?.debug_trace ?? failedTrace;

  return (
    <section className="report-section" aria-labelledby="report-title">
      <div className="report-entry">
        <div className="report-entry-copy">
          <span className="report-icon"><FileText size={19} aria-hidden="true" /></span>
          <div>
            <h3 id="report-title">八字分析报告</h3>
          </div>
        </div>
        <button className="report-button" type="button" onClick={handleGenerate} disabled={isGenerating}>
          {isGenerating ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />}
          {isGenerating ? "正在生成报告" : result ? "重新生成" : "生成分析报告"}
        </button>
      </div>

      {isGenerating && (
        <div className="report-progress" role="status">
          <LoaderCircle className="spin" size={18} aria-hidden="true" />
          <div><strong>正在撰写报告</strong><span>模型会读取精简命盘、当前运势及相关知识库原文，通常需要几十秒。</span></div>
        </div>
      )}

      {reportError && (
        <div className="report-error" role="alert">
          <AlertCircle size={17} aria-hidden="true" />
          <div><strong>暂时无法生成报告</strong><span>{reportError}</span></div>
          {failedTrace && (
            <button type="button" onClick={() => setTraceOpen(true)}>
              <Workflow size={13} aria-hidden="true" />查看执行链路
            </button>
          )}
          {onOpenSettings && <button type="button" onClick={onOpenSettings}>检查 API 设置</button>}
        </div>
      )}

      {traceOpen && activeTrace && (
        <AgentDebugModal
          trace={activeTrace}
          onClose={() => setTraceOpen(false)}
        />
      )}

      {result && !isGenerating && (
        <article className="generated-report">
          <header>
            <div>
              <p className="eyebrow">GENERATED REPORT</p>
              <h3>命盘分析</h3>
            </div>
            {result.debug_trace && (
              <button
                className="agent-trace-button"
                type="button"
                aria-expanded={traceOpen}
                onClick={() => setTraceOpen((open) => !open)}
              >
                <Workflow size={15} aria-hidden="true" />
                {traceOpen ? "收起执行链路" : "查看执行链路"}
              </button>
            )}
          </header>
          <div className="report-sections">
            {REPORT_SECTIONS.map((section, index) => (
              <section key={section.key}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div>
                  <h4>{section.label}</h4>
                  <p>{result.report[section.key]}</p>
                </div>
              </section>
            ))}
          </div>
          <footer>
            本报告属于传统文化视角下的模型生成内容，仅供参考
          </footer>
        </article>
      )}
    </section>
  );
}

function ChartResult({
  chart,
  request,
  onOpenSettings,
}: {
  chart: ChartPreview;
  request: ChartPreviewRequest;
  onOpenSettings?: () => void;
}) {
  const calendar = chart.chart?.calendar;
  const normalized = chart.normalized_input ?? {};
  const warnings = Array.isArray(chart.warnings) ? chart.warnings : [];
  const birthplace = normalized.birthplace
    ? formatChinaBirthplace(normalized.birthplace)
    : "未选择（按北京时间）";
  const usesTrueSolarTime = chart.calculation_policy?.true_solar_time !== false;
  const genderLabel = normalized.gender === "female" ? "女" : "男";
  const dayMasterLabel = normalized.gender === "female" ? "元女" : "元男";
  const pillars = PILLARS.map((definition, index) => ({
    ...definition,
    pillar: getPillar(chart, definition.key, index),
  }));

  return (
    <div className="chart-content">
      <section className="chart-calendar-card" aria-label="命盘日期摘要">
        <div className="zodiac-badge">
          <span>生肖</span>
          <strong>{readable(calendar?.zodiac)}</strong>
        </div>
        <dl className="calendar-summary-lines">
          <div>
            <dt>农历</dt>
            <dd>{readable(calendar?.lunar_text)} {readable(calendar?.time_branch)}时 · {readable(calendar?.destiny_type)}</dd>
          </div>
          <div>
            <dt>公历</dt>
            <dd>{formatDateTime(calendar?.solar_datetime)}</dd>
          </div>
        </dl>
      </section>

      <div className="meta-strip">
        <div><span>北京时间</span><strong>{formatDateTime(normalized.beijing_datetime)}</strong></div>
        <div><span>{usesTrueSolarTime ? "真太阳时" : "排盘时间"}</span><strong>{formatDateTime(normalized.true_solar_datetime)}</strong></div>
        <div><span>出生地点</span><strong className="meta-value-wrap">{birthplace}</strong></div>
        <div><span>性别</span><strong>{genderLabel}</strong></div>
      </div>

      <section className="chart-section" aria-labelledby="pillars-title">
        <div className="section-heading"><h3 id="pillars-title">四柱</h3><span>日主 {readable(chart.chart?.day_master)}</span></div>
        <div className="pillar-table-wrap">
          <table className="pillar-table">
            <thead>
              <tr>
                <th className="pillar-row-label" scope="col">日期</th>
                {pillars.map(({ key, label, description }) => (
                  <th key={key} scope="col" title={description}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <th className="pillar-row-label" scope="row">主星</th>
                {pillars.map(({ key, pillar }) => (
                  <td className="primary-star" key={key}>
                    {key === "day" ? dayMasterLabel : readable(pillar.heavenly_stem?.ten_god, "—")}
                  </td>
                ))}
              </tr>
              <tr>
                <th className="pillar-row-label" scope="row">天干</th>
                {pillars.map(({ key, pillar }) => (
                  <td key={key}>
                    <strong className="pillar-symbol" data-element={pillar.heavenly_stem?.element}>
                      {readable(pillar.heavenly_stem, "—")}
                    </strong>
                  </td>
                ))}
              </tr>
              <tr>
                <th className="pillar-row-label" scope="row">地支</th>
                {pillars.map(({ key, pillar }) => (
                  <td key={key}>
                    <strong className="pillar-symbol" data-element={pillar.earthly_branch?.element}>
                      {readable(pillar.earthly_branch, "—")}
                    </strong>
                  </td>
                ))}
              </tr>
              <tr>
                <th className="pillar-row-label" scope="row">藏干</th>
                {pillars.map(({ key, pillar }) => {
                  const hiddenStems = Array.isArray(pillar.earthly_branch?.hidden_stems)
                    ? pillar.earthly_branch.hidden_stems
                    : [];
                  return (
                    <td key={key}>
                      <div className="pillar-cell-stack">
                        {hiddenStems.length > 0 ? hiddenStems.map((item, hiddenIndex) => (
                          <span className="hidden-stem" key={`${item.symbol}-${hiddenIndex}`}>
                            <strong data-element={item.element}>{item.symbol}</strong>
                            <small>{item.element}</small>
                          </span>
                        )) : <span>—</span>}
                      </div>
                    </td>
                  );
                })}
              </tr>
              <tr>
                <th className="pillar-row-label" scope="row">副星</th>
                {pillars.map(({ key, pillar }) => {
                  const hiddenStems = Array.isArray(pillar.earthly_branch?.hidden_stems)
                    ? pillar.earthly_branch.hidden_stems
                    : [];
                  return (
                    <td key={key}>
                      <div className="pillar-cell-stack">
                        {hiddenStems.length > 0 ? hiddenStems.map((item, hiddenIndex) => (
                          <span key={`${item.symbol}-${hiddenIndex}`}>{readable(item.ten_god, "—")}</span>
                        )) : <span>—</span>}
                      </div>
                    </td>
                  );
                })}
              </tr>
              <tr>
                <th className="pillar-row-label" scope="row">星运</th>
                {pillars.map(({ key, pillar }) => <td key={key}>{readable(pillar.growth_stage, "—")}</td>)}
              </tr>
              <tr>
                <th className="pillar-row-label" scope="row">自坐</th>
                {pillars.map(({ key, pillar }) => <td key={key}>{readable(pillar.self_growth_stage, "—")}</td>)}
              </tr>
              <tr>
                <th className="pillar-row-label" scope="row">空亡</th>
                {pillars.map(({ key, pillar }) => <td key={key}>{readable(pillar.xun_kong, "—")}</td>)}
              </tr>
              <tr>
                <th className="pillar-row-label" scope="row">纳音</th>
                {pillars.map(({ key, pillar }) => <td key={key}>{readable(pillar.na_yin, "—")}</td>)}
              </tr>
              <tr className="shen-sha-row">
                <th className="pillar-row-label" scope="row">神煞</th>
                {pillars.map(({ key, pillar }) => {
                  const shenSha = Array.isArray(pillar.shen_sha) ? pillar.shen_sha : [];
                  return (
                    <td key={key}>
                      <div className="pillar-cell-stack">
                        {shenSha.length > 0
                          ? shenSha.map((item) => <span key={item}>{item}</span>)
                          : <span>—</span>}
                      </div>
                    </td>
                  );
                })}
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      {chart.chart?.fortune_cycles && (
        <FortuneSelector
          key={`${normalized.beijing_datetime}-${normalized.gender}`}
          cycles={chart.chart.fortune_cycles}
        />
      )}

      {warnings.length > 0 && (
        <aside className="warnings" aria-label="计算提示">
          <AlertCircle size={16} aria-hidden="true" />
          <div><strong>计算提示</strong><ul>{warnings.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div>
        </aside>
      )}

      <ReportGenerator request={request} onOpenSettings={onOpenSettings} />
    </div>
  );
}
