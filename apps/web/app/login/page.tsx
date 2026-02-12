import Link from 'next/link';

export default function LoginPage() {
  return (
    <main>
      <section className="card">
        <h1>Sign in</h1>
        <form>
          <label>
            Login
            <input name="login" type="text" autoComplete="username" />
          </label>
          <label>
            Password
            <input name="password" type="password" autoComplete="current-password" />
          </label>
          <div className="actions">
            <button type="button">Sign in</button>
            <Link className="button-link secondary" href="/register">
              Create account
            </Link>
          </div>
        </form>
      </section>
    </main>
  );
}
