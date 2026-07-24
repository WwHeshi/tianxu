import {
  getDivisionChildren,
  getTopDivisions,
  isFinalDivision,
} from "@aurouscia/china-areas";

export interface ChinaAreaOption {
  code: string;
  name: string;
  isTerminal: boolean;
}

export interface ResolvedChinaBirthplace {
  province_code: string;
  province_name: string;
  city_code: string | null;
  city_name: string | null;
  district_code: string;
  district_name: string;
}

interface BirthplaceNames {
  province_name: string;
  city_name?: string | null;
  district_name: string;
}

const EXCLUDED_UNVERIFIED_PROVINCES = new Set(["710000", "810000", "820000"]);

function toOption(division: { code: string; name: string }): ChinaAreaOption {
  return {
    code: division.code,
    name: division.name,
    isTerminal: isFinalDivision(division.code),
  };
}

export const CHINA_PROVINCES = getTopDivisions()
  .filter((division) => !EXCLUDED_UNVERIFIED_PROVINCES.has(division.code))
  .map(toOption);

export function getChinaSecondLevelAreas(provinceCode: string): ChinaAreaOption[] {
  if (!provinceCode) return [];
  return getDivisionChildren(provinceCode, true).map(toOption);
}

export function getChinaDistricts(secondLevelCode: string): ChinaAreaOption[] {
  if (!secondLevelCode || isFinalDivision(secondLevelCode)) return [];
  return getDivisionChildren(secondLevelCode, true).map(toOption);
}

export function resolveChinaBirthplace(
  provinceCode: string,
  secondLevelCode: string,
  districtCode: string,
): ResolvedChinaBirthplace | null {
  const province = CHINA_PROVINCES.find((item) => item.code === provinceCode);
  const secondLevel = getChinaSecondLevelAreas(provinceCode).find(
    (item) => item.code === secondLevelCode,
  );
  if (!province || !secondLevel) return null;

  if (secondLevel.isTerminal) {
    return {
      province_code: province.code,
      province_name: province.name,
      city_code: null,
      city_name: null,
      district_code: secondLevel.code,
      district_name: secondLevel.name,
    };
  }

  const district = getChinaDistricts(secondLevel.code).find(
    (item) => item.code === districtCode,
  );
  if (!district) return null;

  return {
    province_code: province.code,
    province_name: province.name,
    city_code: secondLevel.code,
    city_name: secondLevel.name,
    district_code: district.code,
    district_name: district.name,
  };
}

export function formatChinaBirthplace(birthplace?: BirthplaceNames | null): string {
  if (!birthplace) return "暂未提供";
  return [birthplace.province_name, birthplace.city_name, birthplace.district_name]
    .filter((name): name is string => Boolean(name))
    .join(" · ");
}
