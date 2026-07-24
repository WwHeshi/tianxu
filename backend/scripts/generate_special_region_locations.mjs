import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, "..", "..");
const frontendModules = path.join(repositoryRoot, "frontend", "node_modules");
const frontendRequire = createRequire(path.join(repositoryRoot, "frontend", "package.json"));
const OpenCC = frontendRequire("opencc-js");
const taiwanTopologyPath = path.join(frontendModules, "taiwan-atlas", "towns-10t.json");
const backendOutputPath = path.join(
  repositoryRoot,
  "backend",
  "app",
  "bazi",
  "data",
  "special_region_locations.json",
);
const frontendOutputPath = path.join(
  repositoryRoot,
  "frontend",
  "lib",
  "special-region-options.json",
);

const HONG_KONG_DISTRICT_URL =
  "https://portal.csdi.gov.hk/server/rest/services/common/" +
  "had_rcd_1634523272907_75218/FeatureServer/0/query";
const HONG_KONG_CENTRE_URL =
  "https://portal.csdi.gov.hk/server/rest/services/common/" +
  "had_rcd_1629267205214_43393/FeatureServer/0/query";
const GEONAMES_RDF_ROOT = "https://sws.geonames.org";
const GEONAMES_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/";

const generatedAt =
  process.env.SNAPSHOT_DATE ??
  new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Hong_Kong",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
if (!/^\d{4}-\d{2}-\d{2}$/.test(generatedAt)) {
  throw new Error(`Invalid SNAPSHOT_DATE: ${generatedAt}`);
}
const traditionalTaiwanToSimplified = OpenCC.Converter({ from: "tw", to: "cn" });

// [official admin ID, area code, traditional name, display name, centre OBJECTID, centre name]
const HONG_KONG_DISTRICTS = new Map([
  ["A", ["2016010101", "CW", "中西區", "中西区", 19, "中西區民政諮詢中心"]],
  ["B", ["2016010102", "WC", "灣仔區", "湾仔区", 16, "灣仔民政諮詢中心"]],
  ["C", ["2016010103", "EST", "東區", "东区", 18, "東區民政諮詢中心"]],
  ["D", ["2016010104", "STH", "南區", "南区", 17, "南區民政諮詢中心"]],
  ["E", ["2016010105", "YTM", "油尖旺區", "油尖旺区", 12, "油尖旺民政諮詢中心"]],
  ["F", ["2016010106", "SSP", "深水埗區", "深水埗区", 14, "深水埗民政諮詢中心"]],
  ["G", ["2016010107", "KLC", "九龍城區", "九龙城区", 1, "九龍城民政事務處諮詢服務中心"]],
  ["H", ["2016010108", "WTS", "黃大仙區", "黄大仙区", 13, "黃大仙民政諮詢中心"]],
  ["J", ["2016010110", "KT", "觀塘區", "观塘区", 15, "觀塘民政諮詢中心"]],
  ["K", ["2016010111", "TW", "荃灣區", "荃湾区", 3, "荃灣民政諮詢中心"]],
  ["L", ["2016010112", "TM", "屯門區", "屯门区", 2, "屯門民政諮詢中心"]],
  ["M", ["2016010113", "YL", "元朗區", "元朗区", 20, "元朗民政諮詢中心"]],
  ["N", ["2016010114", "NTH", "北區", "北区", 7, "北區民政諮詢中心"]],
  ["P", ["2016010116", "TP", "大埔區", "大埔区", 4, "大埔民政諮詢中心"]],
  ["Q", ["2016010117", "SK", "西貢區", "西贡区", 6, "西貢民政諮詢中心"]],
  ["R", ["2016010118", "ST", "沙田區", "沙田区", 5, "沙田民政諮詢中心"]],
  ["S", ["2016010119", "KC", "葵青區", "葵青区", 8, "葵青民政諮詢中心"]],
  ["T", ["2016010120", "ILD", "離島區", "离岛区", 9, "離島民政諮詢中心 (東涌)"]],
]);

// [location code, GeoNames ID, source name, traditional display name, display name, type]
const MACAU_GEOGRAPHIC_AREAS = [
  ["01", "11875154", "Nossa Senhora de Fátima", "花地瑪堂區", "花地玛堂区", "traditional_parish"],
  ["02", "11875155", "Santo António", "聖安多尼堂區", "圣安多尼堂区", "traditional_parish"],
  ["03", "11875157", "Sé", "大堂區", "大堂区", "traditional_parish"],
  ["04", "11875156", "São Lázaro", "望德堂區", "望德堂区", "traditional_parish"],
  ["05", "11875158", "São Lourenço", "風順堂區", "风顺堂区", "traditional_parish"],
  ["06", "11875159", "Our Lady of Carmo", "嘉模堂區", "嘉模堂区", "traditional_parish"],
  ["07", "11875160", "Cotai", "路氹城", "路氹城", "geographic_area"],
  ["08", "11875161", "Saint Francis Xavier", "聖方濟各堂區", "圣方济各堂区", "traditional_parish"],
];

function sha256(content) {
  return createHash("sha256").update(content).digest("hex");
}

function roundCoordinate(value) {
  return Number(value.toFixed(8));
}

function arcGisUrl(base, parameters) {
  const url = new URL(base);
  for (const [key, value] of Object.entries(parameters)) {
    url.searchParams.set(key, String(value));
  }
  return url;
}

async function fetchText(url, accept) {
  const response = await fetch(url, {
    headers: {
      Accept: accept,
      "User-Agent": "Tianxu location snapshot generator/1.0",
    },
    signal: AbortSignal.timeout(30_000),
  });
  if (!response.ok) {
    throw new Error(`Unable to download ${url}: HTTP ${response.status}`);
  }
  return response.text();
}

async function fetchJson(url) {
  const source = await fetchText(url, "application/json");
  const payload = JSON.parse(source);
  if (payload.error) {
    throw new Error(`ArcGIS request failed for ${url}: ${JSON.stringify(payload.error)}`);
  }
  return { payload, source };
}

function xmlText(source, tag) {
  const match = source.match(new RegExp(`<${tag}(?:\\s[^>]*)?>([^<]+)</${tag}>`));
  if (!match) throw new Error(`Missing ${tag} in GeoNames RDF`);
  return match[1].trim();
}

function xmlResource(source, tag) {
  const match = source.match(new RegExp(`<${tag}\\s+rdf:resource="([^"]+)"\\s*/>`));
  if (!match) throw new Error(`Missing ${tag} resource in GeoNames RDF`);
  return match[1];
}

function areaCentroid(rings) {
  let crossTotal = 0;
  let longitudeTotal = 0;
  let latitudeTotal = 0;
  const longitudeOrigin = 120;
  const latitudeOrigin = 23;

  for (const ring of rings) {
    if (!Array.isArray(ring) || ring.length < 3) continue;
    for (let index = 0; index < ring.length; index += 1) {
      const current = ring[index];
      const next = ring[(index + 1) % ring.length];
      const x1 = current[0] - longitudeOrigin;
      const y1 = current[1] - latitudeOrigin;
      const x2 = next[0] - longitudeOrigin;
      const y2 = next[1] - latitudeOrigin;
      const cross = x1 * y2 - x2 * y1;
      crossTotal += cross;
      longitudeTotal += (x1 + x2) * cross;
      latitudeTotal += (y1 + y2) * cross;
    }
  }

  if (Math.abs(crossTotal) < Number.EPSILON) {
    throw new Error("Cannot calculate an area centroid for an empty polygon");
  }
  return [
    longitudeOrigin + longitudeTotal / (3 * crossTotal),
    latitudeOrigin + latitudeTotal / (3 * crossTotal),
  ];
}

function createTopoJsonArcDecoder(topology) {
  const { scale, translate } = topology.transform;
  const cache = new Map();

  function forwardArc(arcIndex) {
    if (cache.has(arcIndex)) return cache.get(arcIndex);
    let x = 0;
    let y = 0;
    const decoded = topology.arcs[arcIndex].map(([deltaX, deltaY]) => {
      x += deltaX;
      y += deltaY;
      return [x * scale[0] + translate[0], y * scale[1] + translate[1]];
    });
    cache.set(arcIndex, decoded);
    return decoded;
  }

  return function decodeArc(signedArcIndex) {
    const reverse = signedArcIndex < 0;
    const arcIndex = reverse ? ~signedArcIndex : signedArcIndex;
    const coordinates = forwardArc(arcIndex);
    return reverse ? [...coordinates].reverse() : coordinates;
  };
}

function topoJsonGeometryRings(topology, geometry, decodeArc) {
  const polygonArcs = geometry.type === "Polygon" ? [geometry.arcs] : geometry.arcs;
  if (!Array.isArray(polygonArcs)) {
    throw new Error(`Unsupported Taiwan geometry type: ${geometry.type}`);
  }

  return polygonArcs.flatMap((polygon) =>
    polygon.map((ringArcIndexes) => {
      const ring = [];
      for (const signedArcIndex of ringArcIndexes) {
        const arc = decodeArc(signedArcIndex);
        ring.push(...(ring.length === 0 ? arc : arc.slice(1)));
      }
      return ring;
    }),
  );
}

function canonicalRecord({
  locationId,
  regionCode,
  timezone,
  divisionPath,
  longitude,
  latitude,
  precision,
  coordinateMatch,
  coordinateSource,
  source,
}) {
  if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) {
    throw new Error(`Invalid coordinate for ${locationId}`);
  }
  return {
    location_id: locationId,
    region_code: regionCode,
    timezone,
    division_path: divisionPath,
    longitude: roundCoordinate(longitude),
    latitude: roundCoordinate(latitude),
    coordinate_crs: "EPSG:4326",
    precision,
    coordinate_match: coordinateMatch,
    fallback: false,
    coordinate_source: coordinateSource,
    ...source,
  };
}

async function hongKongRecords() {
  const districtRequest = await fetchJson(
    arcGisUrl(HONG_KONG_DISTRICT_URL, {
      where: "END_LIFESPAN IS NULL",
      outFields:
        "OBJECTID,CSDI_ADMIN_AREA_ID,AREA_TYPE,AREA_ID,AREA_CODE,NAME_TC,NAME_EN," +
        "BEGIN_LIFESPAN,END_LIFESPAN",
      returnGeometry: false,
      orderByFields: "CSDI_ADMIN_AREA_ID",
      f: "json",
    }),
  );
  const centreRequest = await fetchJson(
    arcGisUrl(HONG_KONG_CENTRE_URL, {
      where: "1=1",
      outFields: "OBJECTID,NAME_TC,NAME_EN,LATITUDE,LONGITUDE,LASTUPDATE",
      returnGeometry: true,
      outSR: 4326,
      orderByFields: "OBJECTID",
      f: "json",
    }),
  );

  if (districtRequest.payload.features?.length !== 18) {
    throw new Error("The Hong Kong DCD source no longer contains exactly 18 active districts");
  }
  const centresById = new Map(
    centreRequest.payload.features.map((feature) => [feature.attributes.OBJECTID, feature]),
  );
  const records = {};

  for (const feature of districtRequest.payload.features) {
    const district = feature.attributes;
    const expected = HONG_KONG_DISTRICTS.get(district.AREA_ID);
    const [adminAreaId, areaCode, traditionalName, name, centreObjectId, centreName] =
      expected ?? [];
    if (
      !expected ||
      district.AREA_TYPE !== "DCD" ||
      district.CSDI_ADMIN_AREA_ID !== adminAreaId ||
      district.AREA_CODE !== areaCode ||
      district.NAME_TC !== traditionalName
    ) {
      throw new Error(`Unexpected Hong Kong DCD identity: ${JSON.stringify(district)}`);
    }
    const centre = centresById.get(centreObjectId);
    if (!centre?.geometry || centre.attributes.NAME_TC !== centreName) {
      throw new Error(`Missing selected Hong Kong service centre ${centreObjectId}`);
    }
    const locationId = `CN-HK:DCD:${district.AREA_ID}`;
    records[locationId] = canonicalRecord({
      locationId,
      regionCode: "CN-HK",
      timezone: "Asia/Hong_Kong",
      divisionPath: [
        { code: "CN-HK", name: "香港特别行政区", type: "special_administrative_region" },
        { code: locationId, name, type: "district" },
      ],
      longitude: Number(centre.geometry.x),
      latitude: Number(centre.geometry.y),
      precision: "district_service_point",
      coordinateMatch: "official_had_service_point",
      coordinateSource: `香港特别行政区政府民政事务总署 CSDI@${generatedAt}`,
      source: {
        official_admin_area_id: district.CSDI_ADMIN_AREA_ID,
        official_area_id: district.AREA_ID,
        official_area_code: district.AREA_CODE,
        source_name_traditional: district.NAME_TC,
        source_name_english: district.NAME_EN,
        coordinate_source_objectid: centre.attributes.OBJECTID,
        coordinate_source_name: centre.attributes.NAME_TC,
        coordinate_source_updated_at: centre.attributes.LASTUPDATE,
      },
    });
  }
  return {
    records,
    hashes: {
      district_boundary_sha256: sha256(districtRequest.source),
      service_centres_sha256: sha256(centreRequest.source),
    },
  };
}

async function macauRecords() {
  const sources = await Promise.all(
    MACAU_GEOGRAPHIC_AREAS.map(async (area) => {
      const [locationCode, geonamesId, sourceName, traditionalName, name, type] = area;
      const sourceUrl = `${GEONAMES_RDF_ROOT}/${geonamesId}/about.rdf`;
      const source = await fetchText(sourceUrl, "application/rdf+xml");
      const featureClass = xmlResource(source, "gn:featureClass").split("#").at(-1);
      const featureCode = xmlResource(source, "gn:featureCode").split("#").at(-1);
      const licenseUrl = xmlResource(source, "cc:license");
      if (
        !source.includes(`rdf:about="${GEONAMES_RDF_ROOT}/${geonamesId}/"`) ||
        xmlText(source, "gn:name") !== sourceName ||
        featureClass !== "A" ||
        featureCode !== "A.ADM1" ||
        xmlText(source, "gn:countryCode") !== "MO" ||
        licenseUrl !== GEONAMES_LICENSE_URL ||
        xmlText(source, "cc:attributionName") !== "GeoNames"
      ) {
        throw new Error(`Unexpected GeoNames Macau identity: ${geonamesId}`);
      }
      return {
        locationCode,
        geonamesId,
        sourceName,
        traditionalName,
        name,
        type,
        sourceUrl,
        source,
        featureClass,
        featureCode,
        longitude: Number(xmlText(source, "wgs84_pos:long")),
        latitude: Number(xmlText(source, "wgs84_pos:lat")),
        modifiedAt: xmlText(source, "dcterms:modified"),
      };
    }),
  );

  const records = {};
  for (const area of sources) {
    const locationId = `CN-MO:AREA:${area.locationCode}`;
    records[locationId] = canonicalRecord({
      locationId,
      regionCode: "CN-MO",
      timezone: "Asia/Macau",
      divisionPath: [
        { code: "CN-MO", name: "澳门特别行政区", type: "special_administrative_region" },
        { code: locationId, name: area.name, type: area.type },
      ],
      longitude: area.longitude,
      latitude: area.latitude,
      precision: "geographic_area_representative_point",
      coordinateMatch: "geonames_adm1_direct_id",
      coordinateSource: `GeoNames RDF@${generatedAt}`,
      source: {
        geonames_id: Number(area.geonamesId),
        source_url: area.sourceUrl,
        source_name: area.sourceName,
        display_name_traditional: area.traditionalName,
        source_feature_class: area.featureClass,
        source_feature_code: area.featureCode,
        source_modified_at: area.modifiedAt,
        source_license: "Creative Commons Attribution 4.0",
        coordinate_method: "geonames_feature_representative_point",
      },
    });
  }
  const combinedSource = sources.map(({ geonamesId, source }) => `${geonamesId}\n${source}`).join("\n");
  return { records, sourceSha256: sha256(combinedSource) };
}

async function taiwanRecords() {
  const topologySource = await readFile(taiwanTopologyPath, "utf8");
  const topology = JSON.parse(topologySource);
  const towns = topology.objects?.towns?.geometries;
  if (!Array.isArray(towns) || towns.length !== 368) {
    throw new Error("taiwan-atlas towns-10t.json no longer contains exactly 368 townships");
  }

  const decodeArc = createTopoJsonArcDecoder(topology);
  const records = {};
  const countyCodes = new Set();
  const townCodes = new Set();
  for (const geometry of towns) {
    const town = geometry.properties;
    if (!/^\d{8}$/.test(town.TOWNCODE) || !/^\d{5}$/.test(town.COUNTYCODE)) {
      throw new Error(`Unexpected Taiwan official code: ${JSON.stringify(town)}`);
    }
    if (townCodes.has(town.TOWNCODE)) {
      throw new Error(`Duplicate Taiwan official town code: ${town.TOWNCODE}`);
    }
    countyCodes.add(town.COUNTYCODE);
    townCodes.add(town.TOWNCODE);
    const rings = topoJsonGeometryRings(topology, geometry, decodeArc);
    const [longitude, latitude] = areaCentroid(rings);
    const countyName = traditionalTaiwanToSimplified(town.COUNTYNAME);
    const townName = traditionalTaiwanToSimplified(town.TOWNNAME);
    const countyCode = `CN-TW:COUNTY:${town.COUNTYCODE}`;
    const locationId = `CN-TW:TOWN:${town.TOWNCODE}`;
    records[locationId] = canonicalRecord({
      locationId,
      regionCode: "CN-TW",
      timezone: "Asia/Taipei",
      divisionPath: [
        { code: "CN-TW", name: "台湾地区", type: "region" },
        { code: countyCode, name: countyName, type: "county_or_city" },
        { code: locationId, name: townName, type: "township_level" },
      ],
      longitude,
      latitude,
      precision: "township_area_centroid",
      coordinateMatch: "official_boundary_derived_centroid",
      coordinateSource: "taiwan-atlas@2021.9.20（源自内政部国土测绘中心 dataset 7441）",
      source: {
        official_county_code: town.COUNTYCODE,
        official_town_code: town.TOWNCODE,
        official_county_id: town.COUNTYID,
        official_town_id: town.TOWNID,
        source_county_name_traditional: town.COUNTYNAME,
        source_town_name_traditional: town.TOWNNAME,
        source_county_name_english: town.COUNTYENG,
        source_town_name_english: town.TOWNENG,
        coordinate_method: "planar_area_centroid_from_official_derived_boundary",
      },
    });
  }
  if (countyCodes.size !== 22 || townCodes.size !== 368) {
    throw new Error(
      `Unexpected Taiwan code coverage: ${countyCodes.size} counties, ${townCodes.size} towns`,
    );
  }
  return { records, sha256: sha256(topologySource) };
}

function optionNode(division, locationId = null, children = []) {
  return { code: division.code, name: division.name, location_id: locationId, children };
}

function buildFrontendOptions(records) {
  const roots = new Map();
  for (const record of Object.values(records)) {
    const [rootDivision, ...descendants] = record.division_path;
    if (!roots.has(rootDivision.code)) {
      roots.set(rootDivision.code, optionNode(rootDivision));
    }
    let parent = roots.get(rootDivision.code);
    for (let index = 0; index < descendants.length; index += 1) {
      const division = descendants[index];
      const terminal = index === descendants.length - 1;
      let child = parent.children.find((item) => item.code === division.code);
      if (!child) {
        child = optionNode(division, terminal ? record.location_id : null);
        parent.children.push(child);
      }
      parent = child;
    }
  }

  function sortChildren(node) {
    node.children.sort((left, right) => left.code.localeCompare(right.code, "en"));
    node.children.forEach(sortChildren);
  }
  const provinces = ["CN-HK", "CN-MO", "CN-TW"].map((code) => roots.get(code));
  provinces.forEach(sortChildren);
  return {
    schema_version: "tianxu.special-region-options.v1",
    generated_at: generatedAt,
    provinces,
  };
}

const [hongKong, macau, taiwan] = await Promise.all([
  hongKongRecords(),
  macauRecords(),
  taiwanRecords(),
]);
const records = {
  ...hongKong.records,
  ...macau.records,
  ...taiwan.records,
};
const coverage = {
  total: Object.keys(records).length,
  hong_kong_district: Object.keys(hongKong.records).length,
  macau_geographic_area: Object.keys(macau.records).length,
  taiwan_township: Object.keys(taiwan.records).length,
  fallback: 0,
};
if (coverage.total !== 394) {
  throw new Error(`Expected 394 special-region locations, received ${coverage.total}`);
}

const payload = {
  schema_version: "tianxu.special-region-locations.v1",
  generated_at: generatedAt,
  scope: "Hong Kong districts, Macau traditional parishes plus Cotai, and Taiwan townships",
  lookup_key: "location_id",
  coordinate_crs: "EPSG:4326",
  time_input_basis: "Beijing standard time (UTC+08:00) for every supported location",
  sources: {
    hong_kong: {
      administrative_source: "Hong Kong Home Affairs Department District boundary (DCD)",
      administrative_dataset_id: "had_rcd_1634523272907_75218",
      coordinate_source: "Hong Kong Home Affairs Department Home Affairs Enquiry Centres",
      coordinate_dataset_id: "had_rcd_1629267205214_43393",
      coordinate_method:
        "One official enquiry-centre point per district; Tung Chung selected for Islands District",
      license: "DATA.GOV.HK Terms of Use 1.2",
      retrieved_at: generatedAt,
      ...hongKong.hashes,
    },
    macau: {
      source: "Eight GeoNames RDF records",
      url_template: `${GEONAMES_RDF_ROOT}/{geonamesId}/about.rdf`,
      selection: "Seven traditional parishes plus Cotai",
      coordinate_method: "GeoNames feature representative coordinate",
      license: "Creative Commons Attribution 4.0",
      license_url: GEONAMES_LICENSE_URL,
      attribution: "GeoNames (https://www.geonames.org/)",
      retrieved_at: generatedAt,
      source_sha256: macau.sourceSha256,
    },
    taiwan: {
      source: "taiwan-atlas towns-10t.json",
      version: "2021.9.20",
      upstream: "Ministry of the Interior NLSC township boundary dataset 7441",
      upstream_url: "https://data.gov.tw/dataset/7441",
      coordinate_method: "Planar area centroid of each official-derived EPSG:4326 boundary",
      license: "MIT; upstream Government Data Open License 1.0",
      sha256: taiwan.sha256,
    },
  },
  coverage,
  records,
};

await Promise.all([
  writeFile(backendOutputPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8"),
  writeFile(
    frontendOutputPath,
    `${JSON.stringify(buildFrontendOptions(records), null, 2)}\n`,
    "utf8",
  ),
]);
console.log(`Wrote ${coverage.total} special-region locations to ${backendOutputPath}`);
console.log(`Wrote frontend location options to ${frontendOutputPath}`);
console.log(JSON.stringify(coverage));
