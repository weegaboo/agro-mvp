"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useMemo, useState } from "react";

export default function LoginPage() {
  const router = useRouter();
  const apiBaseUrl = useMemo(
    () => process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
    [],
  );
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const response = await fetch(`${apiBaseUrl}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login, password }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "Не удалось выполнить вход");
      }
      localStorage.setItem("agro_access_token", payload.access_token);
      router.push("/app");
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Неизвестная ошибка");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main>
      <section className="card">
        <h1>Вход</h1>
        <form onSubmit={handleSubmit}>
          <label>
            Логин
            <input
              name="login"
              type="text"
              autoComplete="username"
              value={login}
              onChange={(event) => setLogin(event.target.value)}
              required
            />
          </label>
          <label>
            Пароль
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {error && <p>{error}</p>}
          <div className="actions">
            <button type="submit" disabled={loading}>
              {loading ? "Входим..." : "Войти"}
            </button>
            <Link className="button-link secondary" href="/register">
              Создать аккаунт
            </Link>
          </div>
        </form>
      </section>
    </main>
  );
}
