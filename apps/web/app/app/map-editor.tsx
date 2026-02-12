"use client";

import "leaflet/dist/leaflet.css";
import "leaflet-draw/dist/leaflet.draw.css";

import type { Feature, FeatureCollection, GeoJsonObject } from "geojson";
import L from "leaflet";
import { useEffect, useMemo } from "react";
import { FeatureGroup, GeoJSON, MapContainer, Polyline, TileLayer, useMap } from "react-leaflet";
import { EditControl } from "react-leaflet-draw";

type DrawTarget = "field" | "runway" | "nfz";

type GeomsState = {
  field: GeoJsonObject | null;
  runway_centerline: GeoJsonObject | null;
  nfz: GeoJsonObject[];
};

type MapEditorProps = {
  drawTarget: DrawTarget;
  geoms: GeomsState;
  routeGeo?: Record<string, unknown>;
  mapStyle: "scheme" | "satellite" | "hybrid";
  routePaletteMode: "full_gradient" | "trips_darkness";
  selectedTripIndex: number | null;
  layerVisibility: {
    field: boolean;
    nfz: boolean;
    swaths: boolean;
    transit: boolean;
    trips: boolean;
  };
  onCreateGeometry: (target: DrawTarget, geometry: GeoJsonObject) => void;
};

type TripGeo = {
  start_idx?: number;
  end_idx?: number;
  to_field?: GeoJsonObject;
  back_home?: GeoJsonObject;
};

type TripRange = {
  start: number;
  end: number;
};

type PathSegment = {
  coords: [number, number][];
  color: string;
};

type IndexRange = {
  start: number;
  end: number;
};

function hexToRgb(hex: string): [number, number, number] {
  const v = hex.replace("#", "");
  return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)];
}

function rgbToHex([r, g, b]: [number, number, number]): string {
  const clamp = (v: number) => Math.max(0, Math.min(255, Math.round(v)));
  return `#${clamp(r).toString(16).padStart(2, "0")}${clamp(g).toString(16).padStart(2, "0")}${clamp(b).toString(16).padStart(2, "0")}`;
}

function interpolateColor(start: string, end: string, t: number): string {
  const a = hexToRgb(start);
  const b = hexToRgb(end);
  return rgbToHex([
    a[0] + (b[0] - a[0]) * t,
    a[1] + (b[1] - a[1]) * t,
    a[2] + (b[2] - a[2]) * t,
  ]);
}

function toLatLng([lng, lat]: number[]): [number, number] {
  return [lat, lng];
}

function geoToLines(geo: unknown): [number, number][][] {
  if (!geo || typeof geo !== "object") return [];
  const obj = geo as { type?: string; coordinates?: unknown };
  if (obj.type === "LineString" && Array.isArray(obj.coordinates)) {
    return [(obj.coordinates as number[][]).map(toLatLng)];
  }
  if (obj.type === "MultiLineString" && Array.isArray(obj.coordinates)) {
    return (obj.coordinates as number[][][]).map((ln) => ln.map(toLatLng));
  }
  return [];
}

function swathsFromRouteGeo(routeGeo?: Record<string, unknown>): GeoJsonObject[] {
  if (!routeGeo?.swaths || !Array.isArray(routeGeo.swaths)) return [];
  return routeGeo.swaths as GeoJsonObject[];
}

function tripsFromRouteGeo(routeGeo?: Record<string, unknown>): TripGeo[] {
  if (!routeGeo?.trips || !Array.isArray(routeGeo.trips)) return [];
  return routeGeo.trips as TripGeo[];
}

function normalizeRange(trip: TripGeo, swathCount: number): TripRange | null {
  if (swathCount === 0) return null;
  const rawStart = Number(trip.start_idx ?? 0);
  const rawEnd = Number(trip.end_idx ?? rawStart);
  if (!Number.isFinite(rawStart) || !Number.isFinite(rawEnd)) return null;
  const start = Math.max(0, Math.min(swathCount - 1, Math.floor(rawStart)));
  const end = Math.max(start, Math.min(swathCount - 1, Math.floor(rawEnd)));
  return { start, end };
}

function rangeLines(swaths: GeoJsonObject[], range: TripRange | null): [number, number][][] {
  if (!range) return [];
  const lines: [number, number][][] = [];
  for (let i = range.start; i <= range.end; i += 1) {
    lines.push(...geoToLines(swaths[i]));
  }
  return lines;
}

function linesToGradient(lines: [number, number][][], startColor: string, endColor: string): PathSegment[] {
  const points = lines.flat();
  if (points.length < 2) return [];
  const segments: PathSegment[] = [];
  for (let i = 0; i < points.length - 1; i += 1) {
    const t = i / Math.max(1, points.length - 2);
    segments.push({
      coords: [points[i], points[i + 1]],
      color: interpolateColor(startColor, endColor, t),
    });
  }
  return segments;
}

function nearestIndex(points: [number, number][], target: [number, number]): number {
  let bestIdx = 0;
  let bestDist = Number.POSITIVE_INFINITY;
  for (let i = 0; i < points.length; i += 1) {
    const dx = points[i][0] - target[0];
    const dy = points[i][1] - target[1];
    const d2 = dx * dx + dy * dy;
    if (d2 < bestDist) {
      bestDist = d2;
      bestIdx = i;
    }
  }
  return bestIdx;
}

function coverRangeForTrip(
  coverPoints: [number, number][],
  swaths: GeoJsonObject[],
  trip: TripRange | null,
): IndexRange | null {
  if (!trip || coverPoints.length < 2) return null;
  const startSwath = swaths[trip.start];
  const endSwath = swaths[trip.end];
  if (!startSwath || !endSwath) return null;

  const startPts = geoToLines(startSwath).flat();
  const endPts = geoToLines(endSwath).flat();
  if (startPts.length === 0 || endPts.length === 0) return null;

  const idxs = [...startPts, ...endPts].map((pt) => nearestIndex(coverPoints, pt));
  const start = Math.min(...idxs);
  const end = Math.max(...idxs);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
  return { start, end };
}

function FitToLayers({ geoms, routeGeo }: { geoms: GeomsState; routeGeo?: Record<string, unknown> }) {
  const map = useMap();
  useEffect(() => {
    const pieces: GeoJsonObject[] = [];
    if (geoms.field) pieces.push(geoms.field);
    if (geoms.runway_centerline) pieces.push(geoms.runway_centerline);
    pieces.push(...geoms.nfz);
    if (routeGeo?.cover_path) pieces.push(routeGeo.cover_path as GeoJsonObject);
    if (pieces.length === 0) return;
    const fc: FeatureCollection = {
      type: "FeatureCollection",
      features: pieces.map((g) => ({ type: "Feature", geometry: g, properties: {} }) as Feature),
    };
    const bounds = L.geoJSON(fc).getBounds();
    if (bounds.isValid()) map.fitBounds(bounds.pad(0.15));
  }, [map, geoms, routeGeo]);
  return null;
}

export default function MapEditor({
  drawTarget,
  geoms,
  routeGeo,
  mapStyle,
  routePaletteMode,
  selectedTripIndex,
  layerVisibility,
  onCreateGeometry,
}: MapEditorProps) {
  const drawConfig = {
    rectangle: false,
    circle: false,
    marker: false,
    circlemarker: false,
    polygon: true,
    polyline: true,
  };

  const swaths = useMemo(() => swathsFromRouteGeo(routeGeo), [routeGeo]);
  const trips = useMemo(() => tripsFromRouteGeo(routeGeo), [routeGeo]);
  const tripRanges = useMemo(
    () => trips.map((trip) => normalizeRange(trip, swaths.length)),
    [trips, swaths.length],
  );
  const coverPoints = useMemo(() => geoToLines(routeGeo?.cover_path).flat(), [routeGeo]);
  const coverTripRanges = useMemo(
    () => tripRanges.map((trip) => coverRangeForTrip(coverPoints, swaths, trip)),
    [tripRanges, coverPoints, swaths],
  );

  const selectedTrip = selectedTripIndex !== null ? trips[selectedTripIndex] : null;
  const selectedRange = selectedTripIndex !== null ? (tripRanges[selectedTripIndex] ?? null) : null;

  const fieldGeo = (geoms.field ?? (routeGeo?.field as GeoJsonObject | undefined)) ?? null;
  const nfzGeo = geoms.nfz.length > 0 ? geoms.nfz : ((routeGeo?.nfz as GeoJsonObject[] | undefined) ?? []);
  const runwayGeo = (geoms.runway_centerline ?? (routeGeo?.runway_centerline as GeoJsonObject | undefined)) ?? null;

  const fullRouteSegments = useMemo(
    () => linesToGradient([coverPoints], "#22c55e", "#111827"),
    [coverPoints],
  );

  const selectedTripWorkSegments = useMemo(() => {
    if (selectedTripIndex === null) return [] as PathSegment[];
    const coverRange = coverTripRanges[selectedTripIndex];
    if (!coverRange) return [] as PathSegment[];
    const tripPoints = coverPoints.slice(coverRange.start, coverRange.end + 1);
    return linesToGradient([tripPoints], "#67e8f9", "#1d4ed8");
  }, [selectedTripIndex, coverTripRanges, coverPoints]);

  const tripOrderSegments = useMemo(() => {
    const segments: PathSegment[] = [];
    coverTripRanges.forEach((range, idx) => {
      if (!range) return;
      const tripPoints = coverPoints.slice(range.start, range.end + 1);
      if (tripPoints.length < 2) return;
      const k = tripRanges.length <= 1 ? 0 : idx / (tripRanges.length - 1);
      const color = interpolateColor("#cbd5e1", "#0f172a", k);
      for (let i = 0; i < tripPoints.length - 1; i += 1) {
        segments.push({ coords: [tripPoints[i], tripPoints[i + 1]], color });
      }
    });
    return segments;
  }, [coverTripRanges, coverPoints, tripRanges.length]);

  const workSegmentsToRender = useMemo(() => {
    if (selectedTripIndex !== null) return selectedTripWorkSegments;
    if (routePaletteMode === "trips_darkness") return tripOrderSegments;
    return fullRouteSegments;
  }, [selectedTripIndex, selectedTripWorkSegments, routePaletteMode, tripOrderSegments, fullRouteSegments]);

  const transitSet = useMemo(() => {
    if (selectedTripIndex !== null) {
      return selectedTrip ? [selectedTrip] : [];
    }
    return trips;
  }, [selectedTripIndex, selectedTrip, trips]);
  const swathsToRender = useMemo(() => {
    if (selectedTripIndex !== null && selectedRange) {
      return swaths.slice(selectedRange.start, selectedRange.end + 1);
    }
    return swaths;
  }, [selectedTripIndex, selectedRange, swaths]);

  return (
    <div className="map-editor">
      <MapContainer center={[55.75, 37.61]} zoom={11} style={{ width: "100%", height: "100%" }}>
        {mapStyle === "scheme" && (
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
        )}
        {(mapStyle === "satellite" || mapStyle === "hybrid") && (
          <TileLayer
            attribution="Tiles &copy; Esri"
            url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
          />
        )}
        {mapStyle === "hybrid" && (
          <TileLayer
            attribution="Labels &copy; Esri"
            url="https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
          />
        )}

        <FeatureGroup>
          <EditControl
            key={`draw-${drawTarget}`}
            position="topleft"
            onCreated={(event) => {
              const geometry = event.layer.toGeoJSON().geometry as GeoJsonObject;
              const target: DrawTarget =
                event.layerType === "polyline" ? "runway" : drawTarget === "nfz" ? "nfz" : "field";
              onCreateGeometry(target, geometry);
            }}
            draw={drawConfig}
            edit={{ edit: false, remove: false }}
          />
        </FeatureGroup>

        {layerVisibility.field && fieldGeo && (
          <GeoJSON data={fieldGeo} style={{ color: "#14532d", weight: 2, fillOpacity: 0.1 }} />
        )}
        {layerVisibility.nfz &&
          nfzGeo.map((shape, index) => (
            <GeoJSON
              key={`nfz-${index}`}
              data={shape}
              style={{ color: "#b91c1c", weight: 2, fillOpacity: 0.25, dashArray: "8 6" }}
            />
          ))}
        {runwayGeo && <GeoJSON data={runwayGeo} style={{ color: "#0f172a", weight: 5, opacity: 0.9 }} />}

        {layerVisibility.swaths &&
          swathsToRender.map((swath, index) => (
            <GeoJSON
              key={`swath-${selectedTripIndex ?? "all"}-${index}`}
              data={swath}
              style={{ color: "#2563eb", weight: 2.2, opacity: 0.85 }}
            />
          ))}

        {layerVisibility.transit &&
          workSegmentsToRender.map((seg, index) => (
            <Polyline key={`work-seg-${index}`} positions={seg.coords} pathOptions={{ color: seg.color, weight: 3 }} />
          ))}

        {layerVisibility.trips &&
          transitSet.map((trip, idx) => {
            const dashColor =
              selectedTripIndex !== null
                ? interpolateColor("#67e8f9", "#1d4ed8", 0.7)
                : interpolateColor("#94a3b8", "#1f2937", transitSet.length <= 1 ? 0 : idx / (transitSet.length - 1));
            const tripKey = `${selectedTripIndex ?? "all"}-${trip.start_idx ?? "s"}-${trip.end_idx ?? "e"}-${idx}`;
            const toLines = geoToLines(trip.to_field);
            const backLines = geoToLines(trip.back_home);
            return (
              <FeatureGroup key={`transit-${tripKey}`}>
                {toLines.map((line, lineIdx) => (
                  <Polyline
                    key={`to-${tripKey}-${lineIdx}`}
                    positions={line}
                    pathOptions={{ color: dashColor, weight: 2.5, dashArray: "6 6", opacity: 0.95 }}
                  />
                ))}
                {backLines.map((line, lineIdx) => (
                  <Polyline
                    key={`back-${tripKey}-${lineIdx}`}
                    positions={line}
                    pathOptions={{ color: dashColor, weight: 2.5, dashArray: "6 6", opacity: 0.85 }}
                  />
                ))}
              </FeatureGroup>
            );
          })}

        <FitToLayers geoms={geoms} routeGeo={routeGeo} />
      </MapContainer>
      <div className="map-legend">
        <div><span className="legend-dot field" />Field</div>
        <div><span className="legend-dot runway" />Runway</div>
        <div><span className="legend-dot nfz" />NFZ</div>
        <div><span className="legend-dot swath" />Swaths</div>
        <div><span className="legend-dot route" />Work path / Trip transit</div>
      </div>
    </div>
  );
}
