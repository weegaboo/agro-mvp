import Link from 'next/link';

export default function RegisterPage() {
  return (
    <main>
      <section className="card">
        <h1>Create account</h1>
        <form>
          <label>
            Login
            <input name="login" type="text" autoComplete="username" />
          </label>
          <label>
            Password
            <input name="password" type="password" autoComplete="new-password" />
          </label>
          <label>
            Confirm password
            <input name="confirm" type="password" autoComplete="new-password" />
          </label>
          <div className="actions">
            <button type="button">Create</button>
            <Link className="button-link secondary" href="/login">
              Back
            </Link>
          </div>
        </form>
      </section>
    </main>
  );
}
