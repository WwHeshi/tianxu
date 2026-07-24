"use client";

import { ChevronDown } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";

const ITEM_HEIGHT = 40;
const MIN_YEAR = 1900;

interface DateParts {
  year: number;
  month: number;
  day: number;
}

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

interface DateWheelPickerProps {
  value: string;
  maxDate: string;
  onChange: (value: string) => void;
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

function parseDate(value: string): DateParts | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const parts = {
    year: Number(match[1]),
    month: Number(match[2]),
    day: Number(match[3]),
  };
  if (
    parts.month < 1 ||
    parts.month > 12 ||
    parts.day < 1 ||
    parts.day > daysInMonth(parts.year, parts.month)
  ) {
    return null;
  }
  return parts;
}

function clampDate(parts: DateParts, maximum: DateParts): DateParts {
  const year = Math.min(Math.max(parts.year, MIN_YEAR), maximum.year);
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

function formatDate(parts: DateParts): string {
  return [
    `${parts.year}`.padStart(4, "0"),
    `${parts.month}`.padStart(2, "0"),
    `${parts.day}`.padStart(2, "0"),
  ].join("-");
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
  const optionIdPrefix = useId();

  useEffect(() => {
    const index = options.findIndex((option) => option.value === value);
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

  function selectNearest(list: HTMLDivElement) {
    const index = Math.min(
      options.length - 1,
      Math.max(0, Math.round(list.scrollTop / ITEM_HEIGHT)),
    );
    const option = options[index];
    if (!option) return;
    if (option.value !== value) onChange(option.value);
    list.scrollTo({ top: index * ITEM_HEIGHT, behavior: "smooth" });
  }

  function handleScroll(event: React.UIEvent<HTMLDivElement>) {
    if (settleTimer.current) clearTimeout(settleTimer.current);
    const list = event.currentTarget;
    settleTimer.current = setTimeout(() => selectNearest(list), 90);
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const index = options.findIndex((option) => option.value === value);
    const offset = event.key === "ArrowUp" ? -1 : 1;
    const next = options[Math.min(options.length - 1, Math.max(0, index + offset))];
    if (next) onChange(next.value);
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
        >
          {options.map((option) => (
            <button
              id={`${optionIdPrefix}-${option.value}`}
              className={`date-wheel-option ${option.value === value ? "is-selected" : ""}`}
              type="button"
              role="option"
              aria-selected={option.value === value}
              tabIndex={-1}
              key={option.value}
              onClick={() => onChange(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export function DateWheelPicker({ value, maxDate, onChange, labelledBy }: DateWheelPickerProps) {
  const maximum = useMemo(
    () => parseDate(maxDate) ?? { year: new Date().getFullYear(), month: 12, day: 31 },
    [maxDate],
  );
  const initial = useMemo(
    () => clampDate(parseDate(value) ?? maximum, maximum),
    [maximum, value],
  );
  const [isOpen, setIsOpen] = useState(false);
  const [draft, setDraft] = useState(initial);
  const rootRef = useRef<HTMLDivElement>(null);
  const dialogId = useId();

  const yearOptions = useMemo(
    () => range(MIN_YEAR, maximum.year).map((year) => ({ value: year, label: `${year}` })),
    [maximum.year],
  );
  const monthOptions = useMemo(() => {
    const lastMonth = draft.year === maximum.year ? maximum.month : 12;
    return range(1, lastMonth).map((month) => ({ value: month, label: `${month}` }));
  }, [draft.year, maximum.month, maximum.year]);
  const dayOptions = useMemo(() => {
    const naturalLastDay = daysInMonth(draft.year, draft.month);
    const lastDay =
      draft.year === maximum.year && draft.month === maximum.month
        ? Math.min(naturalLastDay, maximum.day)
        : naturalLastDay;
    return range(1, lastDay).map((day) => ({ value: day, label: `${day}` }));
  }, [draft.month, draft.year, maximum.day, maximum.month, maximum.year]);

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

  function updateDraft(next: Partial<DateParts>) {
    setDraft((current) => clampDate({ ...current, ...next }, maximum));
  }

  function confirmDate() {
    onChange(formatDate(draft));
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
