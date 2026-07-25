"use client";

import { ChevronDown } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  formatLunarDate,
  formatSolarDate,
  getLunarMonths,
  lunarDayLabel,
  parseSolarDate,
  solarToLunar,
  type CalendarType,
  type LunarDateParts,
  type LunarMonthInfo,
  type SolarDateParts,
} from "@/lib/lunar-calendar";

const ITEM_HEIGHT = 40;
const MIN_SOLAR_DATE: SolarDateParts = { year: 1900, month: 1, day: 1 };
const MIN_LUNAR_DATE = solarToLunar(MIN_SOLAR_DATE);

export type CalendarDateValue = LunarDateParts;

type WheelDateDraft = SolarDateParts;

interface WheelOption {
  value: number;
  label: string;
}

interface WheelColumnProps {
  label: string;
  options: WheelOption[];
  value: number;
  onChange: (value: number) => void;
}

interface PointerDragState {
  pointerId: number;
  startY: number;
  startScrollTop: number;
  moved: boolean;
}

interface DateWheelPickerProps {
  calendarType: CalendarType;
  value: CalendarDateValue;
  maxDate: string;
  onChange: (value: CalendarDateValue) => void;
  labelledBy: string;
}

interface TimeParts {
  hour: number;
  minute: number;
}

interface TimeWheelPickerProps {
  value: string;
  onChange: (value: string) => void;
  labelledBy: string;
}

function range(start: number, end: number): number[] {
  return Array.from({ length: end - start + 1 }, (_, index) => start + index);
}

function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate();
}

function clampSolarDate(parts: WheelDateDraft, maximum: SolarDateParts): WheelDateDraft {
  const year = Math.min(Math.max(parts.year, MIN_SOLAR_DATE.year), maximum.year);
  const lastMonth = year === maximum.year ? maximum.month : 12;
  const month = Math.min(Math.max(parts.month, 1), lastMonth);
  const naturalLastDay = daysInMonth(year, month);
  const lastDay =
    year === maximum.year && month === maximum.month
      ? Math.min(naturalLastDay, maximum.day)
      : naturalLastDay;
  return {
    year,
    month,
    day: Math.min(Math.max(parts.day, 1), lastDay),
  };
}

function signedLunarMonth(date: LunarDateParts): number {
  return date.isLeapMonth ? -date.month : date.month;
}

function lunarMonthIndex(months: readonly LunarMonthInfo[], date: LunarDateParts): number {
  const index = months.findIndex(
    (month) => month.signedMonth === signedLunarMonth(date),
  );
  if (index < 0) {
    throw new RangeError(`无法匹配农历边界月份：${date.year}年${signedLunarMonth(date)}月`);
  }
  return index;
}

function lunarMonthsInRange(
  year: number,
  minimum: LunarDateParts,
  maximum: LunarDateParts,
): LunarMonthInfo[] {
  if (year < minimum.year || year > maximum.year) return [];

  const months = getLunarMonths(year);
  let firstIndex = 0;
  let lastIndex = months.length - 1;

  if (year === minimum.year) {
    firstIndex = lunarMonthIndex(months, minimum);
  }
  if (year === maximum.year) {
    lastIndex = lunarMonthIndex(months, maximum);
  }

  return months.slice(firstIndex, lastIndex + 1);
}

function lunarDaysInRange(
  year: number,
  month: LunarMonthInfo,
  minimum: LunarDateParts,
  maximum: LunarDateParts,
): number[] {
  let firstDay = 1;
  let lastDay = month.dayCount;
  if (year === minimum.year && month.signedMonth === signedLunarMonth(minimum)) {
    firstDay = minimum.day;
  }
  if (year === maximum.year && month.signedMonth === signedLunarMonth(maximum)) {
    lastDay = maximum.day;
  }
  return range(firstDay, lastDay);
}

function nearestNumber(values: number[], requested: number): number {
  return values.reduce((nearest, value) =>
    Math.abs(value - requested) < Math.abs(nearest - requested) ? value : nearest,
  );
}

function clampLunarDate(
  parts: WheelDateDraft,
  minimum: LunarDateParts,
  maximum: LunarDateParts,
): WheelDateDraft {
  const year = Math.min(Math.max(parts.year, minimum.year), maximum.year);
  const months = lunarMonthsInRange(year, minimum, maximum);
  const selectedMonth =
    months.find((month) => month.signedMonth === parts.month) ??
    months.reduce((nearest, month) =>
      Math.abs(Math.abs(month.signedMonth) - Math.abs(parts.month)) <
      Math.abs(Math.abs(nearest.signedMonth) - Math.abs(parts.month))
        ? month
        : nearest,
    );
  const days = lunarDaysInRange(year, selectedMonth, minimum, maximum);
  return {
    year,
    month: selectedMonth.signedMonth,
    day: nearestNumber(days, parts.day),
  };
}

function parseTime(value: string): TimeParts | null {
  const match = /^(\d{2}):(\d{2})$/.exec(value);
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
  return { hour, minute };
}

function formatTime(parts: TimeParts): string {
  return `${`${parts.hour}`.padStart(2, "0")}:${`${parts.minute}`.padStart(2, "0")}`;
}

function WheelColumn({ label, options, value, onChange }: WheelColumnProps) {
  const listRef = useRef<HTMLDivElement>(null);
  const settleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentValue = useRef(value);
  const pendingInternalValue = useRef<number | null>(null);
  const pointerDrag = useRef<PointerDragState | null>(null);
  const suppressClick = useRef(false);
  const optionIdPrefix = useId();
  currentValue.current = value;

  useEffect(() => {
    if (pendingInternalValue.current === value) {
      pendingInternalValue.current = null;
      return;
    }
    pendingInternalValue.current = null;
    const index = options.findIndex((option) => option.value === currentValue.current);
    if (index >= 0 && listRef.current) {
      listRef.current.scrollTo({ top: index * ITEM_HEIGHT });
    }
  }, [options, value]);

  useEffect(
    () => () => {
      if (settleTimer.current) clearTimeout(settleTimer.current);
    },
    [],
  );

  function selectNearest(list: HTMLDivElement, align = false) {
    const index = Math.min(
      options.length - 1,
      Math.max(0, Math.round(list.scrollTop / ITEM_HEIGHT)),
    );
    const option = options[index];
    if (!option) return;
    if (option.value !== currentValue.current) {
      currentValue.current = option.value;
      pendingInternalValue.current = option.value;
      onChange(option.value);
    }
    if (align) list.scrollTo({ top: index * ITEM_HEIGHT, behavior: "smooth" });
  }

  function handleScroll(event: React.UIEvent<HTMLDivElement>) {
    if (settleTimer.current) clearTimeout(settleTimer.current);
    const list = event.currentTarget;
    settleTimer.current = setTimeout(() => selectNearest(list), 160);
  }

  function handlePointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (event.pointerType !== "mouse" || event.button !== 0) return;
    const list = event.currentTarget;
    pointerDrag.current = {
      pointerId: event.pointerId,
      startY: event.clientY,
      startScrollTop: list.scrollTop,
      moved: false,
    };
    list.setPointerCapture(event.pointerId);
    list.classList.add("is-dragging");
  }

  function handlePointerMove(event: React.PointerEvent<HTMLDivElement>) {
    const drag = pointerDrag.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const distance = event.clientY - drag.startY;
    if (!drag.moved && Math.abs(distance) < 3) return;
    drag.moved = true;
    event.preventDefault();
    event.currentTarget.scrollTop = drag.startScrollTop - distance;
  }

  function finishPointerDrag(event: React.PointerEvent<HTMLDivElement>) {
    const drag = pointerDrag.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const list = event.currentTarget;
    pointerDrag.current = null;
    list.classList.remove("is-dragging");
    if (list.hasPointerCapture(event.pointerId)) list.releasePointerCapture(event.pointerId);
    if (!drag.moved) return;
    event.preventDefault();
    suppressClick.current = true;
    setTimeout(() => {
      suppressClick.current = false;
    }, 0);
    if (settleTimer.current) clearTimeout(settleTimer.current);
    selectNearest(list, true);
  }

  function selectOption(option: WheelOption, index: number) {
    if (option.value !== currentValue.current) {
      currentValue.current = option.value;
      pendingInternalValue.current = option.value;
      onChange(option.value);
    }
    listRef.current?.scrollTo({ top: index * ITEM_HEIGHT, behavior: "smooth" });
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const index = options.findIndex((option) => option.value === currentValue.current);
    const offset = event.key === "ArrowUp" ? -1 : 1;
    const nextIndex = Math.min(options.length - 1, Math.max(0, index + offset));
    const next = options[nextIndex];
    if (next) selectOption(next, nextIndex);
  }

  return (
    <div className="date-wheel-column">
      <span className="date-wheel-column-label">{label}</span>
      <div className="date-wheel-frame">
        <div className="date-wheel-selection" aria-hidden="true" />
        <div
          ref={listRef}
          className="date-wheel-list"
          role="listbox"
          tabIndex={0}
          aria-label={label}
          aria-activedescendant={`${optionIdPrefix}-${value}`}
          onScroll={handleScroll}
          onKeyDown={handleKeyDown}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={finishPointerDrag}
          onPointerCancel={finishPointerDrag}
          onLostPointerCapture={finishPointerDrag}
        >
          {options.map((option, index) => (
            <button
              id={`${optionIdPrefix}-${option.value}`}
              className={`date-wheel-option ${option.value === value ? "is-selected" : ""}`}
              type="button"
              role="option"
              aria-selected={option.value === value}
              tabIndex={-1}
              key={option.value}
              onClick={(event) => {
                if (suppressClick.current) {
                  event.preventDefault();
                  return;
                }
                selectOption(option, index);
              }}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export function DateWheelPicker({
  calendarType,
  value,
  maxDate,
  onChange,
  labelledBy,
}: DateWheelPickerProps) {
  const maximum = useMemo(() => {
    const now = new Date();
    return (
      parseSolarDate(maxDate) ?? {
        year: now.getFullYear(),
        month: now.getMonth() + 1,
        day: now.getDate(),
      }
    );
  }, [maxDate]);
  const maximumLunar = useMemo(() => solarToLunar(maximum), [maximum]);
  const initial = useMemo(() => {
    const draft = {
      year: value.year,
      month: calendarType === "lunar" && value.isLeapMonth ? -value.month : value.month,
      day: value.day,
    };
    return calendarType === "lunar"
      ? clampLunarDate(draft, MIN_LUNAR_DATE, maximumLunar)
      : clampSolarDate(draft, maximum);
  }, [calendarType, maximum, maximumLunar, value]);
  const [isOpen, setIsOpen] = useState(false);
  const [draft, setDraft] = useState(initial);
  const rootRef = useRef<HTMLDivElement>(null);
  const dialogId = useId();

  const yearOptions = useMemo(() => {
    const firstYear = calendarType === "lunar" ? MIN_LUNAR_DATE.year : MIN_SOLAR_DATE.year;
    const lastYear = calendarType === "lunar" ? maximumLunar.year : maximum.year;
    return range(firstYear, lastYear).map((year) => ({ value: year, label: `${year}` }));
  }, [calendarType, maximum.year, maximumLunar.year]);
  const lunarMonths = useMemo(
    () =>
      calendarType === "lunar"
        ? lunarMonthsInRange(draft.year, MIN_LUNAR_DATE, maximumLunar)
        : [],
    [calendarType, draft.year, maximumLunar],
  );
  const monthOptions = useMemo(() => {
    if (calendarType === "lunar") {
      return lunarMonths.map((month) => ({
        value: month.signedMonth,
        label: month.label,
      }));
    }
    const lastMonth = draft.year === maximum.year ? maximum.month : 12;
    return range(1, lastMonth).map((month) => ({ value: month, label: `${month}` }));
  }, [calendarType, draft.year, lunarMonths, maximum.month, maximum.year]);
  const dayOptions = useMemo(() => {
    if (calendarType === "lunar") {
      const month = lunarMonths.find((item) => item.signedMonth === draft.month);
      if (!month) return [];
      return lunarDaysInRange(draft.year, month, MIN_LUNAR_DATE, maximumLunar).map((day) => ({
        value: day,
        label: lunarDayLabel(day),
      }));
    }
    const naturalLastDay = daysInMonth(draft.year, draft.month);
    const lastDay =
      draft.year === maximum.year && draft.month === maximum.month
        ? Math.min(naturalLastDay, maximum.day)
        : naturalLastDay;
    return range(1, lastDay).map((day) => ({ value: day, label: `${day}` }));
  }, [calendarType, draft.month, draft.year, lunarMonths, maximum, maximumLunar]);

  useEffect(() => {
    if (!isOpen) return;
    function closeWhenOutside(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setIsOpen(false);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setIsOpen(false);
    }
    document.addEventListener("pointerdown", closeWhenOutside, true);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeWhenOutside, true);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [isOpen]);

  function openPicker() {
    setDraft(initial);
    setIsOpen(true);
  }

  function updateDraft(next: Partial<WheelDateDraft>) {
    setDraft((current) => {
      const candidate = { ...current, ...next };
      return calendarType === "lunar"
        ? clampLunarDate(candidate, MIN_LUNAR_DATE, maximumLunar)
        : clampSolarDate(candidate, maximum);
    });
  }

  function confirmDate() {
    onChange({
      year: draft.year,
      month: Math.abs(draft.month),
      day: draft.day,
      isLeapMonth: calendarType === "lunar" && draft.month < 0,
    });
    setIsOpen(false);
  }

  const displayValue =
    calendarType === "lunar"
      ? formatLunarDate(value)
      : formatSolarDate(value);

  return (
    <div className="date-wheel-picker" ref={rootRef}>
      <button
        className="date-picker-trigger"
        type="button"
        aria-labelledby={labelledBy}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        aria-controls={isOpen ? dialogId : undefined}
        onClick={() => (isOpen ? setIsOpen(false) : openPicker())}
      >
        <span>{displayValue}</span>
        <ChevronDown size={16} aria-hidden="true" />
      </button>

      {isOpen && (
        <div className="date-wheel-popover" id={dialogId} role="dialog" aria-labelledby={labelledBy}>
          <div className="date-wheel-columns">
            <WheelColumn
              label="年"
              options={yearOptions}
              value={draft.year}
              onChange={(year) => updateDraft({ year })}
            />
            <WheelColumn
              label="月"
              options={monthOptions}
              value={draft.month}
              onChange={(month) => updateDraft({ month })}
            />
            <WheelColumn
              label="日"
              options={dayOptions}
              value={draft.day}
              onChange={(day) => updateDraft({ day })}
            />
          </div>
          <div className="date-wheel-actions">
            <button type="button" onClick={() => setIsOpen(false)}>取消</button>
            <button className="is-primary" type="button" onClick={confirmDate}>确定</button>
          </div>
        </div>
      )}
    </div>
  );
}

export function TimeWheelPicker({ value, onChange, labelledBy }: TimeWheelPickerProps) {
  const initial = useMemo(() => parseTime(value) ?? { hour: 12, minute: 0 }, [value]);
  const [isOpen, setIsOpen] = useState(false);
  const [draft, setDraft] = useState(initial);
  const rootRef = useRef<HTMLDivElement>(null);
  const dialogId = useId();
  const hourOptions = useMemo(
    () => range(0, 23).map((hour) => ({ value: hour, label: `${hour}`.padStart(2, "0") })),
    [],
  );
  const minuteOptions = useMemo(
    () => range(0, 59).map((minute) => ({ value: minute, label: `${minute}`.padStart(2, "0") })),
    [],
  );

  useEffect(() => {
    if (!isOpen) return;
    function closeWhenOutside(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setIsOpen(false);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setIsOpen(false);
    }
    document.addEventListener("pointerdown", closeWhenOutside, true);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeWhenOutside, true);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [isOpen]);

  function openPicker() {
    setDraft(initial);
    setIsOpen(true);
  }

  function confirmTime() {
    onChange(formatTime(draft));
    setIsOpen(false);
  }

  return (
    <div className="date-wheel-picker" ref={rootRef}>
      <button
        className="date-picker-trigger"
        type="button"
        aria-labelledby={labelledBy}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        aria-controls={isOpen ? dialogId : undefined}
        onClick={() => (isOpen ? setIsOpen(false) : openPicker())}
      >
        <span>{value}</span>
        <ChevronDown size={16} aria-hidden="true" />
      </button>

      {isOpen && (
        <div className="date-wheel-popover" id={dialogId} role="dialog" aria-labelledby={labelledBy}>
          <div className="date-wheel-columns time-wheel-columns">
            <WheelColumn
              label="时"
              options={hourOptions}
              value={draft.hour}
              onChange={(hour) => setDraft((current) => ({ ...current, hour }))}
            />
            <WheelColumn
              label="分"
              options={minuteOptions}
              value={draft.minute}
              onChange={(minute) => setDraft((current) => ({ ...current, minute }))}
            />
          </div>
          <div className="date-wheel-actions">
            <button type="button" onClick={() => setIsOpen(false)}>取消</button>
            <button className="is-primary" type="button" onClick={confirmTime}>确定</button>
          </div>
        </div>
      )}
    </div>
  );
}
