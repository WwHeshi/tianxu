import { Lunar, LunarYear, Solar } from "lunar-javascript";

export type CalendarType = "solar" | "lunar";

export interface SolarDateParts {
  year: number;
  month: number;
  day: number;
}

export interface LunarDateParts extends SolarDateParts {
  isLeapMonth: boolean;
}

export interface LunarMonthInfo {
  readonly month: number;
  readonly signedMonth: number;
  readonly isLeapMonth: boolean;
  readonly dayCount: number;
  readonly label: string;
}

const LUNAR_MONTH_NAMES = [
  "",
  "正月",
  "二月",
  "三月",
  "四月",
  "五月",
  "六月",
  "七月",
  "八月",
  "九月",
  "十月",
  "冬月",
  "腊月",
];

const LUNAR_DAY_NAMES = [
  "",
  "初一",
  "初二",
  "初三",
  "初四",
  "初五",
  "初六",
  "初七",
  "初八",
  "初九",
  "初十",
  "十一",
  "十二",
  "十三",
  "十四",
  "十五",
  "十六",
  "十七",
  "十八",
  "十九",
  "二十",
  "廿一",
  "廿二",
  "廿三",
  "廿四",
  "廿五",
  "廿六",
  "廿七",
  "廿八",
  "廿九",
  "三十",
];

const SOLAR_TO_LUNAR_CACHE = new Map<string, LunarDateParts>();
const LUNAR_TO_SOLAR_CACHE = new Map<string, SolarDateParts>();
const LUNAR_MONTHS_CACHE = new Map<number, readonly LunarMonthInfo[]>();

function solarDateKey(date: SolarDateParts): string {
  return `${date.year}-${date.month}-${date.day}`;
}

function lunarDateKey(date: LunarDateParts): string {
  return `${date.year}-${date.isLeapMonth ? -date.month : date.month}-${date.day}`;
}

export function solarToLunar(date: SolarDateParts): LunarDateParts {
  const key = solarDateKey(date);
  const cached = SOLAR_TO_LUNAR_CACHE.get(key);
  if (cached) return { ...cached };

  const lunar = Solar.fromYmd(date.year, date.month, date.day).getLunar();
  const signedMonth = lunar.getMonth();
  const result = {
    year: lunar.getYear(),
    month: Math.abs(signedMonth),
    day: lunar.getDay(),
    isLeapMonth: signedMonth < 0,
  };
  SOLAR_TO_LUNAR_CACHE.set(key, result);
  return { ...result };
}

export function lunarToSolar(date: LunarDateParts): SolarDateParts {
  const key = lunarDateKey(date);
  const cached = LUNAR_TO_SOLAR_CACHE.get(key);
  if (cached) return { ...cached };

  const signedMonth = date.isLeapMonth ? -date.month : date.month;
  const solar = Lunar.fromYmd(date.year, signedMonth, date.day).getSolar();
  const result = {
    year: solar.getYear(),
    month: solar.getMonth(),
    day: solar.getDay(),
  };
  LUNAR_TO_SOLAR_CACHE.set(key, result);
  return { ...result };
}

export function getLunarMonths(year: number): readonly LunarMonthInfo[] {
  const cached = LUNAR_MONTHS_CACHE.get(year);
  if (cached) return cached;

  const months = Object.freeze(
    LunarYear.fromYear(year)
      .getMonthsInYear()
      .map((month) => {
        const signedMonth = month.getMonth();
        const monthNumber = Math.abs(signedMonth);
        const isLeapMonth = month.isLeap() || signedMonth < 0;
        return Object.freeze({
          month: monthNumber,
          signedMonth,
          isLeapMonth,
          dayCount: month.getDayCount(),
          label: `${isLeapMonth ? "闰" : ""}${LUNAR_MONTH_NAMES[monthNumber]}`,
        });
      }),
  );
  LUNAR_MONTHS_CACHE.set(year, months);
  return months;
}

export function formatSolarDate(date: SolarDateParts): string {
  return [
    `${date.year}`.padStart(4, "0"),
    `${date.month}`.padStart(2, "0"),
    `${date.day}`.padStart(2, "0"),
  ].join("-");
}

export function parseSolarDate(value: string): SolarDateParts | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const date = { year: Number(match[1]), month: Number(match[2]), day: Number(match[3]) };
  const parsed = new Date(date.year, date.month - 1, date.day);
  if (
    parsed.getFullYear() !== date.year ||
    parsed.getMonth() + 1 !== date.month ||
    parsed.getDate() !== date.day
  ) {
    return null;
  }
  return date;
}

export function compareSolarDates(left: SolarDateParts, right: SolarDateParts): number {
  return (
    left.year * 10_000 + left.month * 100 + left.day -
    (right.year * 10_000 + right.month * 100 + right.day)
  );
}

export function lunarMonthLabel(month: number, isLeapMonth: boolean): string {
  return `${isLeapMonth ? "闰" : ""}${LUNAR_MONTH_NAMES[month] ?? `${month}月`}`;
}

export function lunarDayLabel(day: number): string {
  return LUNAR_DAY_NAMES[day] ?? `${day}日`;
}

export function formatLunarDate(date: LunarDateParts): string {
  return `${date.year}年${lunarMonthLabel(date.month, date.isLeapMonth)}${lunarDayLabel(date.day)}`;
}
