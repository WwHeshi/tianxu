import { LoaderCircle } from "lucide-react";

interface AuthenticatedPageStateProps {
  error: string;
}

export function AuthenticatedPageState({ error }: AuthenticatedPageStateProps) {
  if (error) {
    return (
      <main className="auth-state-page">
        <section><h1>暂时无法进入系统</h1><p>{error}</p></section>
      </main>
    );
  }
  return (
    <main className="auth-state-page" aria-live="polite">
      <LoaderCircle className="spin" size={24} aria-hidden="true" />
      <p>正在验证登录状态</p>
    </main>
  );
}
