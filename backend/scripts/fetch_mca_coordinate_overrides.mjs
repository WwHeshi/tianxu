import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const outputPath = path.join(
  scriptDirectory,
  "..",
  "app",
  "bazi",
  "data",
  "mca_coordinate_overrides.json",
);

const endpointRoot = "https://dmfw.mca.gov.cn/9095";
const searchEndpoint = `${endpointRoot}/stname/listPub`;
const detailsEndpoint = `${endpointRoot}/stname/detailsPub`;
const placeTypeCode = "21400";
const targets = [
  ["460302", "西沙区"],
  ["460303", "南沙区"],
  ["500157", "两江新区"],
  ["540481", "米林市"],
  ["540581", "错那市"],
  ["653228", "和康县"],
  ["653229", "和安县"],
  ["659012", "白杨市"],
];

const headers = {
  Accept: "application/json, text/javascript, */*; q=0.01",
  Origin: "https://dmfw.mca.gov.cn",
  Referer: "https://dmfw.mca.gov.cn/search.html",
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36",
  "X-Requested-With": "XMLHttpRequest",
};

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function pause(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function postForm(url, values) {
  const response = await fetch(url, {
    method: "POST",
    headers,
    body: new URLSearchParams(values),
  });
  const responseText = await response.text();
  if (!response.ok) {
    throw new Error(`${url} returned ${response.status}: ${responseText.slice(0, 300)}`);
  }
  try {
    return JSON.parse(responseText);
  } catch (error) {
    throw new Error(`${url} returned invalid JSON`, { cause: error });
  }
}

function validateOfficialRecord(districtCode, districtName, record) {
  if (record.standard_name !== districtName) {
    throw new Error(`Name mismatch for ${districtCode}: ${record.standard_name}`);
  }
  if (record.place_type_code !== placeTypeCode || record.place_type !== "县级行政区") {
    throw new Error(`Unexpected place type for ${districtCode} ${districtName}`);
  }
  if (record.area !== `${districtCode}999`) {
    throw new Error(`Administrative code mismatch for ${districtCode}: ${record.area}`);
  }
  const coordinates = record.gdm?.coordinates?.[0];
  if (
    !Array.isArray(coordinates) ||
    coordinates.length !== 2 ||
    !coordinates.every(Number.isFinite)
  ) {
    throw new Error(`Missing coordinate for ${districtCode} ${districtName}`);
  }
  return coordinates;
}

const records = {};
for (const [districtCode, districtName] of targets) {
  const searchResponse = await postForm(searchEndpoint, {
    stName: districtName,
    placeTypeCode,
    code: districtCode,
    page: "1",
    size: "20",
    year: "0",
    searchType: "精确匹配",
  });
  const exactMatches = (searchResponse.records ?? []).filter(
    (record) =>
      record.standard_name === districtName &&
      record.place_type_code === placeTypeCode &&
      record.area === `${districtCode}999`,
  );
  if (exactMatches.length !== 1) {
    throw new Error(
      `Expected one official match for ${districtCode} ${districtName}, got ${exactMatches.length}`,
    );
  }

  const detailsResponse = await postForm(detailsEndpoint, {
    id: exactMatches[0].id,
    year: "0",
  });
  const [longitude, latitude] = validateOfficialRecord(
    districtCode,
    districtName,
    detailsResponse,
  );
  records[districtCode] = {
    district_code: districtCode,
    district_name: districtName,
    source_record_id: detailsResponse.id,
    place_code: detailsResponse.place_code,
    standard_name: detailsResponse.standard_name,
    place_type: detailsResponse.place_type,
    place_type_code: detailsResponse.place_type_code,
    province_name: detailsResponse.province_name,
    city_name: detailsResponse.city_name,
    area_name: detailsResponse.area_name,
    area_code: detailsResponse.area,
    longitude,
    latitude,
    detail_response_sha256: sha256(JSON.stringify(detailsResponse)),
  };
  await pause(500);
}

const payload = {
  schema_version: "tianxu.mca-coordinate-overrides.v1",
  queried_at: new Date().toISOString(),
  source: {
    name: "中华人民共和国民政部国家地名信息库",
    website: "https://dmfw.mca.gov.cn/",
    search_endpoint: searchEndpoint,
    details_endpoint: detailsEndpoint,
    coordinate_reference: "EPSG:4326 as exposed by the official web client",
  },
  query: {
    place_type: "县级行政区",
    place_type_code: placeTypeCode,
    search_type: "精确匹配",
    year: 0,
  },
  records,
};

await writeFile(outputPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
console.log(`Wrote ${Object.keys(records).length} official coordinates to ${outputPath}`);
