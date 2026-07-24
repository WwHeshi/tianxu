import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, "..", "..");
const frontendModules = path.join(repositoryRoot, "frontend", "node_modules");
const divisionPath = path.join(
  frontendModules,
  "@aurouscia",
  "china-areas",
  "dist",
  "json",
  "data-nested.json",
);
const coordinatePath = path.join(frontendModules, "tmap-citycoordinate", "index.js");
const mcaCoordinatePath = path.join(
  repositoryRoot,
  "backend",
  "app",
  "bazi",
  "data",
  "mca_coordinate_overrides.json",
);
const outputPath = path.join(
  repositoryRoot,
  "backend",
  "app",
  "bazi",
  "data",
  "district_longitudes.json",
);

const excludedProvinceCodes = new Set(["710000", "810000", "820000"]);

function sha256(content) {
  return createHash("sha256").update(content).digest("hex");
}

function extractCoordinates(moduleSource) {
  const match = moduleSource.match(
    /const locations = (\[[\s\S]*?\]);\s*\/\/根据id获取地区/,
  );
  if (!match) throw new Error("Unable to parse tmap-citycoordinate data");
  return JSON.parse(match[1]);
}

function collectOfficialLeaves(tree) {
  const leaves = [];
  for (const province of tree) {
    if (excludedProvinceCodes.has(province.code) || !province.children?.length) continue;
    for (const secondLevel of province.children) {
      if (secondLevel.children?.length) {
        for (const district of secondLevel.children) {
          leaves.push({ province, city: secondLevel, district });
        }
      } else {
        leaves.push({ province, city: null, district: secondLevel });
      }
    }
  }
  return leaves;
}

function precisionFor(source) {
  if (source.type === "city") return "city_center";
  return "district_center";
}

function validateOfficialCoordinate({ province, city, district }, record) {
  if (record.district_code !== district.code || record.district_name !== district.name) {
    throw new Error(`Official coordinate identity mismatch for ${district.code} ${district.name}`);
  }
  if (record.standard_name !== district.name || record.area_code !== `${district.code}999`) {
    throw new Error(`Official coordinate code mismatch for ${district.code} ${district.name}`);
  }
  if (record.place_type_code !== "21400" || record.place_type !== "县级行政区") {
    throw new Error(`Official coordinate type mismatch for ${district.code} ${district.name}`);
  }
  if (record.province_name !== province.name) {
    throw new Error(`Official coordinate province mismatch for ${district.code} ${district.name}`);
  }
  const expectedCityName = city?.name ?? district.name;
  if (record.city_name !== expectedCityName) {
    throw new Error(`Official coordinate city mismatch for ${district.code} ${district.name}`);
  }
  if (!Number.isFinite(record.longitude) || !Number.isFinite(record.latitude)) {
    throw new Error(`Official coordinate is invalid for ${district.code} ${district.name}`);
  }
}

const [divisionSource, coordinateSource, mcaCoordinateSource] = await Promise.all([
  readFile(divisionPath, "utf8"),
  readFile(coordinatePath, "utf8"),
  readFile(mcaCoordinatePath, "utf8"),
]);
const divisions = JSON.parse(divisionSource);
const coordinates = extractCoordinates(coordinateSource);
const coordinatesByCode = new Map(coordinates.map((item) => [item.id, item]));
const mcaCoordinates = JSON.parse(mcaCoordinateSource);
if (mcaCoordinates.schema_version !== "tianxu.mca-coordinate-overrides.v1") {
  throw new Error(`Unsupported MCA coordinate schema: ${mcaCoordinates.schema_version}`);
}
const officialCoordinatesByCode = new Map(Object.entries(mcaCoordinates.records));
const usedOfficialCoordinateCodes = new Set();
const records = {};
const coverage = {
  total: 0,
  direct_code: 0,
  official_mca_api: 0,
  fallback: 0,
};

for (const officialLeaf of collectOfficialLeaves(divisions)) {
  const { province, city, district } = officialLeaf;
  const officialCoordinate = officialCoordinatesByCode.get(district.code);
  const packageCoordinate = coordinatesByCode.get(district.code);
  if (!officialCoordinate && !packageCoordinate) {
    throw new Error(`Missing coordinate for ${district.code} ${district.name}`);
  }

  let longitude;
  let latitude;
  let precision;
  let match;
  let sourceId;
  let sourceCode;
  let sourcePlaceCode;
  let sourceName;
  let coordinateSourceName;
  if (officialCoordinate) {
    validateOfficialCoordinate(officialLeaf, officialCoordinate);
    usedOfficialCoordinateCodes.add(district.code);
    longitude = officialCoordinate.longitude;
    latitude = officialCoordinate.latitude;
    precision = "district_center";
    match = "official_mca_api";
    sourceId = officialCoordinate.source_record_id;
    sourceCode = officialCoordinate.area_code;
    sourcePlaceCode = officialCoordinate.place_code;
    sourceName = officialCoordinate.standard_name;
    coordinateSourceName = `中华人民共和国民政部国家地名信息库@${mcaCoordinates.queried_at.slice(0, 10)}`;
  } else {
    [longitude, latitude] = packageCoordinate.location.split(",").map(Number);
    precision = precisionFor(packageCoordinate);
    match = "direct_code";
    sourceId = packageCoordinate.id;
    sourceCode = packageCoordinate.id;
    sourcePlaceCode = null;
    sourceName = packageCoordinate.name;
    coordinateSourceName = "tmap-citycoordinate@1.0.1";
  }

  coverage.total += 1;
  coverage[match] += 1;
  records[district.code] = {
    province_code: province.code,
    province_name: province.name,
    city_code: city?.code ?? null,
    city_name: city?.name ?? null,
    district_code: district.code,
    district_name: district.name,
    longitude,
    latitude,
    precision,
    coordinate_match: match,
    fallback: false,
    coordinate_source: coordinateSourceName,
    source_id: sourceId,
    source_code: sourceCode,
    source_place_code: sourcePlaceCode,
    source_name: sourceName,
  };
}

if (usedOfficialCoordinateCodes.size !== officialCoordinatesByCode.size) {
  const unusedCodes = [...officialCoordinatesByCode.keys()].filter(
    (code) => !usedOfficialCoordinateCodes.has(code),
  );
  throw new Error(`Unused official coordinate records: ${unusedCodes.join(", ")}`);
}

const payload = {
  schema_version: "tianxu.mainland-district-longitudes.v3",
  generated_at: mcaCoordinates.queried_at.slice(0, 10),
  scope: "Chinese mainland official county-level administrative divisions",
  lookup_key: "district_code",
  standard_meridian_longitude: 120,
  administrative_source: {
    name: "@aurouscia/china-areas",
    version: "0.7.0",
    published_at: "2026-05-18",
    upstream: "Ministry of Civil Affairs National Database for Geographical Names",
    license: "MIT",
    sha256: sha256(divisionSource),
  },
  coordinate_sources: {
    tmap_citycoordinate: {
      name: "tmap-citycoordinate",
      version: "1.0.1",
      upstream: "Tencent Maps administrative center coordinates",
      coordinate_reference: "Not declared by the package",
      license: "MIT",
      record_count: coverage.direct_code,
      sha256: sha256(coordinateSource),
    },
    mca_national_geographical_names: {
      ...mcaCoordinates.source,
      queried_at: mcaCoordinates.queried_at,
      record_count: coverage.official_mca_api,
      sha256: sha256(mcaCoordinateSource),
    },
  },
  coverage,
  records,
};

await writeFile(outputPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
console.log(`Wrote ${coverage.total} official locations to ${outputPath}`);
console.log(JSON.stringify(coverage));
