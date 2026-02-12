"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";

type PlannerResponse = {
  route: Record<string, unknown>;
  logs: string[];
};

type MissionListItem = {
  id: number;
  status: string;
  created_at: string;
};

export default function AppPage() {
  const router = useRouter();
  const apiBaseUrl = useMemo(
    () => process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
    [],
  );
  const [projectFile, setProjectFile] = useState<File | null>(null);
  const [result, setResult] = useState<PlannerResponse | null>(null);
  const [missions, setMissions] = useState<MissionListItem[]>([]);
  const [token, setToken] = useState<string | null>(null);
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

  const loadMissions = async (authToken: string) => {
    const response = await fetch(`${apiBaseUrl}/missions`, {
      headers: { Authorization: `Bearer ${authToken}` },
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail ?? "Failed to fetch missions");
    }
    setMissions(payload as MissionListItem[]);
  };

  const handleBuildRouteByUpload = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!projectFile) {
      setError("Select a project JSON file first");
      return;
    }
    if (!token) {
      setError("You are not authorized");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const formData = new FormData();
      formData.append("file", projectFile);
      const response = await fetch(`${apiBaseUrl}/missions`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Planner request failed");
      }
      setResult(payload.result_json as PlannerResponse);
      await loadMissions(token);
    } catch (buildError) {
      setResult(null);
      setError(buildError instanceof Error ? buildError.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem("agro_access_token");
    router.push("/login");
  };

  return (
    <main>
      <section className="card">
        <h1>Map workspace placeholder</h1>
        <p>Authenticated mission workspace.</p>
        <div className="actions">
          <button type="button" className="secondary" onClick={handleLogout}>
            Logout
          </button>
          <button
            type="button"
            onClick={() => token && loadMissions(token).catch((e: unknown) => setError(String(e)))}
          >
            Refresh missions
          </button>
        </div>
        <form onSubmit={handleBuildRouteByUpload}>
          <label htmlFor="projectFile">Upload project JSON</label>
          <input
            id="projectFile"
            type="file"
            accept="application/json,.json"
            onChange={(event) => setProjectFile(event.target.files?.[0] ?? null)}
          />
          <button type="submit" disabled={loading || !projectFile}>
            {loading ? "Building..." : "Build from upload"}
          </button>
        </form>
        {error && <p>{error}</p>}
        {missions.length > 0 && (
          <div>
            <h2>Missions</h2>
            <pre>{JSON.stringify(missions, null, 2)}</pre>
          </div>
        )}
        {result && (
          <div>
            <h2>Planner response</h2>
            <pre>{JSON.stringify(result.route, null, 2)}</pre>
            <h3>Logs</h3>
            <pre>{result.logs.join("\n")}</pre>
          </div>
        )}
      </section>
    </main>
  );
}
