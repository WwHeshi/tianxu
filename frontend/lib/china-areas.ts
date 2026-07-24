import {
  getDivisionChildren,
  getTopDivisions,
  isFinalDivision,
} from "@aurouscia/china-areas";
import specialRegionOptions from "@/lib/special-region-options.json";
import type { NormalizedBirthplace } from "@/lib/types";

interface SpecialRegionNode {
  code: string;
  name: string;
  location_id: string | null;
  children: SpecialRegionNode[];
}

interface SpecialRegionOptionsSnapshot {
  schema_version: string;
  generated_at: string;
  provinces: SpecialRegionNode[];
}

export interface ChinaAreaOption {
  code: string;
  name: string;
  isTerminal: boolean;
  locationId: string | null;
}

export interface ResolvedChinaBirthplace {
  location_id: string;
}

const EXCLUDED_UNVERIFIED_PROVINCES = new Set(["710000", "810000", "820000"]);
const SPECIAL_REGIONS = (specialRegionOptions as SpecialRegionOptionsSnapshot).provinces;

function toMainlandOption(division: { code: string; name: string }): ChinaAreaOption {
  const isTerminal = isFinalDivision(division.code);
  return {
    code: division.code,
    name: division.name,
    isTerminal,
    locationId: isTerminal ? `CN:${division.code}` : null,
  };
}

function toSpecialRegionOption(node: SpecialRegionNode): ChinaAreaOption {
  return {
    code: node.code,
    name: node.name,
    isTerminal: node.location_id !== null,
    locationId: node.location_id,
  };
}

function findSpecialRegionNode(code: string): SpecialRegionNode | null {
  for (const province of SPECIAL_REGIONS) {
    if (province.code === code) return province;
    for (const secondLevel of province.children) {
      if (secondLevel.code === code) return secondLevel;
      const district = secondLevel.children.find((item) => item.code === code);
      if (district) return district;
    }
  }
  return null;
}

export const CHINA_PROVINCES = [
  ...getTopDivisions()
    .filter((division) => !EXCLUDED_UNVERIFIED_PROVINCES.has(division.code))
    .map(toMainlandOption),
  ...SPECIAL_REGIONS.map(toSpecialRegionOption),
];

export function getChinaSecondLevelAreas(provinceCode: string): ChinaAreaOption[] {
  if (!provinceCode) return [];
  const specialProvince = SPECIAL_REGIONS.find((item) => item.code === provinceCode);
  if (specialProvince) return specialProvince.children.map(toSpecialRegionOption);
  return getDivisionChildren(provinceCode, true).map(toMainlandOption);
}

export function getChinaDistricts(secondLevelCode: string): ChinaAreaOption[] {
  if (!secondLevelCode) return [];
  const specialSecondLevel = findSpecialRegionNode(secondLevelCode);
  if (specialSecondLevel) return specialSecondLevel.children.map(toSpecialRegionOption);
  if (isFinalDivision(secondLevelCode)) return [];
  return getDivisionChildren(secondLevelCode, true).map(toMainlandOption);
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

  if (secondLevel.isTerminal && secondLevel.locationId) {
    return { location_id: secondLevel.locationId };
  }

  const district = getChinaDistricts(secondLevel.code).find(
    (item) => item.code === districtCode,
  );
  if (!district?.isTerminal || !district.locationId) return null;
  return { location_id: district.locationId };
}

export function formatChinaBirthplace(
  birthplace?: Pick<NormalizedBirthplace, "division_path"> | null,
): string {
  if (!birthplace || !Array.isArray(birthplace.division_path)) return "暂未提供";
  const names = birthplace.division_path.map((division) => division.name).filter(Boolean);
  return names.length > 0 ? names.join(" · ") : "暂未提供";
}
