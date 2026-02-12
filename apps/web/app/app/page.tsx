"use client";

import { FormEvent, useMemo, useState } from "react";

type PlannerResponse = {
  route: Record<string, unknown>;
  logs: string[];
};

export default function AppPage() {
  const apiBaseUrl = useMemo(
    () => process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
    [],
  );
  const [projectPath, setProjectPath] = useState("");
  const [projectFile, setProjectFile] = useState<File | null>(null);
  const [result, setResult] = useState<PlannerResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleBuildRouteByPath = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`${apiBaseUrl}/planner/build-from-project`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_path: projectPath }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Planner request failed");
      }
      setResult(payload as PlannerResponse);
    } catch (buildError) {
      setResult(null);
      setError(buildError instanceof Error ? buildError.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  const handleBuildRouteByUpload = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!projectFile) {
      setError("Select a project JSON file first");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const formData = new FormData();
      formData.append("file", projectFile);
      const response = await fetch(`${apiBaseUrl}/planner/build-from-upload`, {
        method: "POST",
        body: formData,
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Planner request failed");
      }
      setResult(payload as PlannerResponse);
    } catch (buildError) {
      setResult(null);
      setError(buildError instanceof Error ? buildError.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main>
      <section className="card">
        <h1>Map workspace placeholder</h1>
        <p>Planner API smoke flow.</p>
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
        <form onSubmit={handleBuildRouteByPath}>
          <label htmlFor="projectPath">Project path</label>
          <input
            id="projectPath"
            type="text"
            value={projectPath}
            onChange={(event) => setProjectPath(event.target.value)}
            placeholder="/app/path/to/project.json"
            required
          />
          <button type="submit" disabled={loading}>
            {loading ? "Building..." : "Build from path"}
          </button>
        </form>
        {error && <p>{error}</p>}
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
