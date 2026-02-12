"use client";

import type { GeoJsonObject } from "geojson";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

const MapEditor = dynamic(() => import("./map-editor"), { ssr: false });

type DrawTarget = "field" | "runway" | "nfz";

type MissionListItem = {
  id: number;
  status: string;
  created_at: string;
};

type MissionDetail = {
  id: number;
  status: string;
  created_at: string;
  result_json: {
    route?: {
      geo?: Record<string, unknown>;
      metrics?: Record<string, number>;
    };
    logs?: string[];
    error?: string;
  } | null;
};

type AircraftParams = {
  spray_width_m: number;
  turn_radius_m: number;
  total_capacity_l: number;
  fuel_reserve_l: number;
  mix_rate_l_per_ha: number;
  fuel_burn_l_per_km: number;
  headland_factor: number;
  route_order: "snake" | "boustro" | "spiral" | "straight_loops";
  objective: "n_swath" | "swath_length" | "field_coverage" | "overlap";
  use_cc: boolean;
};

type GeomsState = {
  field: GeoJsonObject | null;
  runway_centerline: GeoJsonObject | null;
  nfz: GeoJsonObject[];
};

const METRIC_LABELS: Record<string, string> = {
  length_total_m: "Length total, m",
  length_transit_m: "Transit, m",
  length_spray_m: "Spray, m",
  time_total_min: "Time total, min",
  fuel_l: "Fuel, l",
  fert_l: "Mix, l",
  field_area_ha: "Field area, ha",
  sprayed_area_ha: "Sprayed, ha",
};

export default function AppPage() {
  const router = useRouter();
  const apiBaseUrl = useMemo(() => process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000", []);
  const [token, setToken] = useState<string | null>(null);
  const [drawTarget, setDrawTarget] = useState<DrawTarget>("field");
  const [geoms, setGeoms] = useState<GeomsState>({ field: null, runway_centerline: null, nfz: [] });
  const [aircraft, setAircraft] = useState<AircraftParams>({
    spray_width_m: 20,
    turn_radius_m: 40,
    total_capacity_l: 200,
    fuel_reserve_l: 5,
    mix_rate_l_per_ha: 10,
    fuel_burn_l_per_km: 0.35,
    headland_factor: 3,
    route_order: "snake",
    objective: "n_swath",
    use_cc: true,
  });
  const [missions, setMissions] = useState<MissionListItem[]>([]);
  const [selectedMission, setSelectedMission] = useState<MissionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const savedToken = localStorage.getItem("agro_access_token");
    if (!savedToken) {
      router.replace("/login");
      return;
    }
    setToken(savedToken);
  }, [router]);

  const loadMissions = useCallback(async (authToken: string) => {
    const response = await fetch(`${apiBaseUrl}/missions`, { headers: { Authorization: `Bearer ${authToken}` } });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail ?? "Failed to fetch missions");
    setMissions(payload as MissionListItem[]);
  }, [apiBaseUrl]);

  const loadMissionById = useCallback(async (missionId: number) => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${apiBaseUrl}/missions/${missionId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "Failed to fetch mission");
      setSelectedMission(payload as MissionDetail);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [apiBaseUrl, token]);

  useEffect(() => {
    if (!token) return;
    void loadMissions(token).catch((loadError: unknown) => {
      setError(loadError instanceof Error ? loadError.message : "Failed to load missions");
    });
  }, [token, loadMissions]);

  const handleCreateGeometry = (target: DrawTarget, geometry: GeoJsonObject) => {
    setGeoms((prev) => {
      if (target === "field") return { ...prev, field: geometry };
      if (target === "runway") return { ...prev, runway_centerline: geometry };
      return { ...prev, nfz: [...prev.nfz, geometry] };
    });
  };

  const clearGeometry = (target: DrawTarget) => {
    setGeoms((prev) => {
      if (target === "field") return { ...prev, field: null };
      if (target === "runway") return { ...prev, runway_centerline: null };
      return { ...prev, nfz: [] };
    });
  };

  const buildMission = async () => {
    if (!token) return;
    if (!geoms.field || !geoms.runway_centerline) {
      const missing = [
        !geoms.field ? "field polygon" : null,
        !geoms.runway_centerline ? "runway line" : null,
      ]
        .filter(Boolean)
        .join(" and ");
      setError(`Missing required geometry: ${missing}`);
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${apiBaseUrl}/missions/from-geo`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ geoms, aircraft }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "Mission build failed");
      await loadMissions(token);
      await loadMissionById((payload as MissionDetail).id);
    } catch (buildError) {
      setError(buildError instanceof Error ? buildError.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem("agro_access_token");
    router.push("/login");
  };

  const routeGeo = selectedMission?.result_json?.route?.geo;
  const metrics = selectedMission?.result_json?.route?.metrics ?? {};
  const logs = selectedMission?.result_json?.logs ?? [];
  const trips = (routeGeo?.trips as Array<Record<string, unknown>> | undefined) ?? [];

  return (
    <main className="workspace-v2">
      <section className="workspace-panel left-panel">
        <h2>Aircraft & Route Params</h2>
        <label>
          Spray width, m
          <input min={1} max={200} step={1} type="number" value={aircraft.spray_width_m} onChange={(e) => setAircraft({ ...aircraft, spray_width_m: Number(e.target.value) })} />
        </label>
        <label>
          Turn radius, m
          <input min={1} max={500} step={1} type="number" value={aircraft.turn_radius_m} onChange={(e) => setAircraft({ ...aircraft, turn_radius_m: Number(e.target.value) })} />
        </label>
        <label>
          Total capacity, l
          <input min={1} max={10000} step={1} type="number" value={aircraft.total_capacity_l} onChange={(e) => setAircraft({ ...aircraft, total_capacity_l: Number(e.target.value) })} />
        </label>
        <label>
          Fuel reserve, l
          <input min={0} max={500} step={0.5} type="number" value={aircraft.fuel_reserve_l} onChange={(e) => setAircraft({ ...aircraft, fuel_reserve_l: Number(e.target.value) })} />
        </label>
        <label>
          Mix rate, l/ha
          <input min={0} max={200} type="number" step={0.5} value={aircraft.mix_rate_l_per_ha} onChange={(e) => setAircraft({ ...aircraft, mix_rate_l_per_ha: Number(e.target.value) })} />
        </label>
        <label>
          Fuel burn, l/km
          <input min={0} max={10} type="number" step={0.01} value={aircraft.fuel_burn_l_per_km} onChange={(e) => setAircraft({ ...aircraft, fuel_burn_l_per_km: Number(e.target.value) })} />
        </label>
        <label>
          Headland factor (x width)
          <input min={0} max={8} step={0.5} type="number" value={aircraft.headland_factor} onChange={(e) => setAircraft({ ...aircraft, headland_factor: Number(e.target.value) })} />
        </label>
        <label>
          Route order
          <select value={aircraft.route_order} onChange={(e) => setAircraft({ ...aircraft, route_order: e.target.value as AircraftParams["route_order"] })}>
            <option value="snake">snake</option>
            <option value="boustro">boustro</option>
            <option value="spiral">spiral</option>
            <option value="straight_loops">straight_loops</option>
          </select>
        </label>
        <label>
          Objective
          <select value={aircraft.objective} onChange={(e) => setAircraft({ ...aircraft, objective: e.target.value as AircraftParams["objective"] })}>
            <option value="n_swath">n_swath</option>
            <option value="swath_length">swath_length</option>
            <option value="field_coverage">field_coverage</option>
            <option value="overlap">overlap</option>
          </select>
        </label>
        <label className="checkbox-row">
          <input type="checkbox" checked={aircraft.use_cc} onChange={(e) => setAircraft({ ...aircraft, use_cc: e.target.checked })} />
          Use continuous curvature
        </label>

        <h3>Geometry Editor</h3>
        <p>Use map draw tools: polygon for {drawTarget === "nfz" ? "NFZ" : "Field"}, polyline for Runway.</p>
        <div className="mode-row">
          <button type="button" className={drawTarget === "field" ? "" : "secondary"} onClick={() => setDrawTarget("field")}>Polygon -> Field</button>
          <button type="button" className={drawTarget === "nfz" ? "" : "secondary"} onClick={() => setDrawTarget("nfz")}>Polygon -> NFZ</button>
        </div>
        <p>Field: {geoms.field ? "set" : "missing"} | Runway: {geoms.runway_centerline ? "set" : "missing"} | NFZ: {geoms.nfz.length}</p>
        <div className="mode-row">
          <button type="button" className="secondary" onClick={() => clearGeometry("field")}>Clear Field</button>
        </div>
        <div className="mode-row">
          <button type="button" className="secondary" onClick={() => clearGeometry("runway")}>Clear Runway</button>
          <button type="button" className="secondary" onClick={() => clearGeometry("nfz")}>Clear NFZ</button>
        </div>

        <button type="button" onClick={() => void buildMission()} disabled={loading}>
          {loading ? "Building..." : "Build Mission"}
        </button>
        <button type="button" className="secondary" onClick={handleLogout}>Logout</button>
        {error && <p>{error}</p>}
      </section>

      <section className="workspace-panel center-panel">
        <MapEditor drawTarget={drawTarget} geoms={geoms} routeGeo={routeGeo} onCreateGeometry={handleCreateGeometry} />
      </section>

      <section className="workspace-panel right-panel">
        <h2>Missions & Stats</h2>
        <div className="mission-list">
          {missions.map((mission) => (
            <button
              key={mission.id}
              type="button"
              className={`mission-item ${selectedMission?.id === mission.id ? "active" : ""}`}
              onClick={() => void loadMissionById(mission.id)}
            >
              #{mission.id} · {mission.status}
            </button>
          ))}
        </div>

        {Object.keys(metrics).length > 0 && (
          <>
            <h3>Mission Metrics</h3>
            <div className="metrics-grid">
              {Object.entries(metrics).map(([key, value]) => (
                <div key={key} className="metric-card">
                  <span>{METRIC_LABELS[key] ?? key}</span>
                  <strong>{Number(value).toFixed(2)}</strong>
                </div>
              ))}
            </div>
          </>
        )}

        {trips.length > 0 && (
          <>
            <h3>Trips</h3>
            <div className="trip-list">
              {trips.map((trip, index) => (
                <div key={index} className="trip-card">
                  <strong>Trip {index + 1}</strong>
                  <div>Swaths: {String(trip.start_idx)} - {String(trip.end_idx)}</div>
                  <div>Fuel used: {Number(trip.fuel_used_l ?? 0).toFixed(2)} l</div>
                  <div>Mix used: {Number(trip.mix_used_l ?? 0).toFixed(2)} l</div>
                </div>
              ))}
            </div>
          </>
        )}

        <h3>Logs</h3>
        <pre>{logs.join("\n") || "-"}</pre>
      </section>
    </main>
  );
}
