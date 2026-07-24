declare module "lunar-javascript" {
  interface SolarInstance {
    getYear(): number;
    getMonth(): number;
    getDay(): number;
    getLunar(): LunarInstance;
  }

  interface LunarInstance {
    getYear(): number;
    getMonth(): number;
    getDay(): number;
    getSolar(): SolarInstance;
  }

  interface LunarMonthInstance {
    getMonth(): number;
    isLeap(): boolean;
    getDayCount(): number;
  }

  interface LunarYearInstance {
    getMonthsInYear(): LunarMonthInstance[];
  }

  export const Solar: {
    fromYmd(year: number, month: number, day: number): SolarInstance;
  };

  export const Lunar: {
    fromYmd(year: number, month: number, day: number): LunarInstance;
  };

  export const LunarYear: {
    fromYear(year: number): LunarYearInstance;
  };
}
