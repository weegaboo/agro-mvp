"use client";

import "leaflet/dist/leaflet.css";
import "leaflet-draw/dist/leaflet.draw.css";

import type { GeoJsonObject } from "geojson";
import { FeatureGroup, GeoJSON, MapContainer, TileLayer } from "react-leaflet";
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
  onCreateGeometry: (target: DrawTarget, geometry: GeoJsonObject) => void;
};

export default function MapEditor({ drawTarget, geoms, routeGeo, onCreateGeometry }: MapEditorProps) {
  const drawConfig = {
    rectangle: false,
    circle: false,
    marker: false,
    circlemarker: false,
    polygon: drawTarget === "field" || drawTarget === "nfz",
    polyline: drawTarget === "runway",
  };

  return (
    <div className="map-editor">
      <MapContainer center={[55.75, 37.61]} zoom={11} style={{ width: "100%", height: "100%" }}>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />

        <FeatureGroup>
          <EditControl
            position="topleft"
            onCreated={(event) => {
              const geometry = event.layer.toGeoJSON().geometry as GeoJsonObject;
              onCreateGeometry(drawTarget, geometry);
            }}
            draw={drawConfig}
            edit={{ edit: false, remove: false }}
          />
        </FeatureGroup>

        {geoms.field && <GeoJSON data={geoms.field} style={{ color: "#166534", weight: 2, fillOpacity: 0.12 }} />}
        {geoms.runway_centerline && <GeoJSON data={geoms.runway_centerline} style={{ color: "#111827", weight: 4 }} />}
        {geoms.nfz.map((shape, index) => (
          <GeoJSON key={`nfz-${index}`} data={shape} style={{ color: "#b91c1c", weight: 2, fillOpacity: 0.2 }} />
        ))}

        {routeGeo?.cover_path && (
          <GeoJSON data={routeGeo.cover_path as GeoJsonObject} style={{ color: "#2563eb", weight: 2 }} />
        )}
        {routeGeo?.swaths &&
          Array.isArray(routeGeo.swaths) &&
          routeGeo.swaths.map((swath, index) => (
            <GeoJSON key={`swath-${index}`} data={swath as GeoJsonObject} style={{ color: "#d97706", weight: 2 }} />
          ))}
        {routeGeo?.to_field && (
          <GeoJSON data={routeGeo.to_field as GeoJsonObject} style={{ color: "#334155", weight: 2, dashArray: "4 6" }} />
        )}
        {routeGeo?.back_home && (
          <GeoJSON data={routeGeo.back_home as GeoJsonObject} style={{ color: "#64748b", weight: 2, dashArray: "4 6" }} />
        )}
      </MapContainer>
    </div>
  );
}
