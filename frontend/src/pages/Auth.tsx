import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Link, Navigate, Route, Routes, useSearchParams } from 'react-router-dom';
import { ArrowRight, CircleCheck, KeyRound, LoaderCircle, MailCheck, ShieldCheck, Store } from 'lucide-react';
import { problem, useAuth } from '../auth';
import { ErrorNotice } from '../components/ui';
import '../auth.css';

declare global {
  interface Window {
    google?: { accounts: { id: {
      initialize: (options: { client_id: string; callback: (response: { credential: string }) => void; ux_mode?: string }) => void;
      renderButton: (element: HTMLElement, options: Record<string, unknown>) => void;
    } } };
  }
}

const GOOGLE_SCRIPT = 'https://accounts.google.com/gsi/client';
let googleScript: Promise<void> | null = null;
function loadGoogle() {
  googleScript ??= new Promise<void>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = GOOGLE_SCRIPT; script.async = true; script.defer = true;
    script.onload = () => resolve(); script.onerror = () => { googleScript = null; reject(new Error('Could not load Google sign-in. Check your internet connection.')); };
    document.head.appendChild(script);
  });
  return googleScript;
}

/** Google's own "Continue with Google" button. Shown only when the backend has a Google client id. */
function GoogleButton({ onError }: { onError: (message: string) => void }) {
  const auth = useAuth(); const holder = useRef<HTMLDivElement>(null); const [busy, setBusy] = useState(false);
  const signIn = useRef(auth.signInWithGoogle); signIn.current = auth.signInWithGoogle;
  useEffect(() => {
    if (!auth.googleClientId) return;
    let active = true;
    loadGoogle().then(() => {
      if (!active || !holder.current || !window.google) return;
      window.google.accounts.id.initialize({ client_id: auth.googleClientId!, ux_mode: 'popup', callback: ({ credential }) => {
        setBusy(true); onError('');
        signIn.current(credential).catch(e => onError(problem(e))).finally(() => setBusy(false));
      } });
      window.google.accounts.id.renderButton(holder.current, { type: 'standard', theme: 'outline', size: 'large', text: 'continue_with', shape: 'pill', width: 352, logo_alignment: 'center' });
    }).catch(e => onError((e as Error).message));
    return () => { active = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.googleClientId]);
  if (!auth.googleClientId) return null;
  return <><div className="auth-google">{busy ? <span className="auth-google-busy"><LoaderCircle className="spin" size={17} />Signing in with Google…</span> : <div ref={holder} />}</div>
    <div className="auth-divider"><span>or use your email</span></div></>;
}

function AuthLayout({ title, subtitle, children, footer }: { title: string; subtitle: string; children?: ReactNode; footer?: ReactNode }) {
  return <main className="auth-page"><section className="auth-card panel">
    <div className="auth-brand"><span className="brand-mark">c<span /></span>customerly<span className="brand-dot">.</span></div>
    <h1>{title}</h1><p>{subtitle}</p>{children}{footer && <div className="auth-footer">{footer}</div>}
  </section><p className="auth-note"><ShieldCheck size={14} />Your data stays private to your store.</p></main>;
}

function useSubmit() {
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const run = (action: () => Promise<void>) => async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError('');
    try { await action(); } catch (e) { setError(problem(e)); } finally { setBusy(false); }
  };
  return { busy, error, run, setError };
}

function Submit({ busy, label, busyLabel }: { busy: boolean; label: string; busyLabel: string }) {
  return <button className="button primary full" disabled={busy}>{busy ? <LoaderCircle className="spin" size={17} /> : null}{busy ? busyLabel : label}<ArrowRight size={17} /></button>;
}

function SignIn({ onDeveloperToken }: { onDeveloperToken: () => void }) {
  const auth = useAuth(); const { busy, error, run, setError } = useSubmit();
  const [email, setEmail] = useState(''); const [password, setPassword] = useState('');
  return <AuthLayout title="Welcome back" subtitle="Sign in to see your customers, metrics and audiences."
    footer={<>New to Customerly? <Link to="/signup">Create an account</Link></>}>
    <GoogleButton onError={setError} />
    <form className="auth-form" onSubmit={run(() => auth.signIn(email, password))}>
      <label className="field">Email<input type="email" autoComplete="email" required value={email} onChange={e => setEmail(e.target.value)} placeholder="you@store.com" /></label>
      <label className="field">Password<input type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} /></label>
      <Link className="auth-forgot" to="/forgot-password">Forgot password?</Link>
      <ErrorNotice error={error || auth.error} />
      <Submit busy={busy} label="Sign in" busyLabel="Signing in…" />
    </form>
    <button className="text-button auth-dev" onClick={onDeveloperToken}>Use a developer workspace token instead</button>
  </AuthLayout>;
}

function SignUp() {
  const auth = useAuth(); const { busy, error, run, setError } = useSubmit();
  const [email, setEmail] = useState(''); const [password, setPassword] = useState(''); const [confirm, setConfirm] = useState('');
  return <AuthLayout title="Create your account" subtitle="Bring your store's data together in one clear customer view."
    footer={<>Already have an account? <Link to="/login">Sign in</Link></>}>
    <GoogleButton onError={setError} />
    <form className="auth-form" onSubmit={run(async () => {
      if (password.length < 8) throw new Error('Use at least 8 characters for your password.');
      if (password !== confirm) throw new Error('The passwords do not match.');
      await auth.signUp(email, password);
    })}>
      <label className="field">Email<input type="email" autoComplete="email" required value={email} onChange={e => setEmail(e.target.value)} placeholder="you@store.com" /></label>
      <label className="field">Password<input type="password" autoComplete="new-password" required minLength={8} value={password} onChange={e => setPassword(e.target.value)} />
        <small>At least 8 characters. Avoid common words and only-number passwords.</small></label>
      <label className="field">Confirm password<input type="password" autoComplete="new-password" required value={confirm} onChange={e => setConfirm(e.target.value)} /></label>
      <ErrorNotice error={error} />
      <Submit busy={busy} label="Create account" busyLabel="Creating account…" />
    </form>
  </AuthLayout>;
}

function PasswordFields({ password, setPassword, confirm, setConfirm }: { password: string; setPassword: (v: string) => void; confirm: string; setConfirm: (v: string) => void }) {
  return <>
    <label className="field">New password<input type="password" autoComplete="new-password" required minLength={8} value={password} onChange={e => setPassword(e.target.value)} />
      <small>At least 8 characters. Avoid common words and only-number passwords.</small></label>
    <label className="field">Confirm new password<input type="password" autoComplete="new-password" required value={confirm} onChange={e => setConfirm(e.target.value)} /></label>
  </>;
}

function ForgotPassword() {
  const auth = useAuth(); const { busy, error, run } = useSubmit(); const [email, setEmail] = useState(''); const [sent, setSent] = useState(false);
  if (sent) return <AuthLayout title="Check your email" subtitle={`If an account exists for ${email}, we sent it a link to reset the password. The link works once and expires in 1 hour.`}
    footer={<Link to="/login">Back to sign in</Link>}><div className="auth-icon"><MailCheck size={30} /></div></AuthLayout>;
  return <AuthLayout title="Forgot your password?" subtitle="Enter your account email and we'll send you a link to choose a new password."
    footer={<Link to="/login">Back to sign in</Link>}>
    <form className="auth-form" onSubmit={run(async () => { await auth.forgotPassword(email); setSent(true); })}>
      <label className="field">Email<input type="email" autoComplete="email" required value={email} onChange={e => setEmail(e.target.value)} placeholder="you@store.com" /></label>
      <ErrorNotice error={error} />
      <Submit busy={busy} label="Send reset link" busyLabel="Sending…" />
    </form>
    {auth.googleClientId && <p className="auth-hint">Signed up with Google? You can also just use <b>Continue with Google</b>.</p>}
  </AuthLayout>;
}

function ResetPassword() {
  const auth = useAuth(); const [params] = useSearchParams(); const token = params.get('token') || '';
  const { busy, error, run } = useSubmit(); const [password, setPassword] = useState(''); const [confirm, setConfirm] = useState(''); const [done, setDone] = useState(false);
  if (done) return <AuthLayout title="Password updated" subtitle="You were signed out everywhere. Sign in with your new password."
    footer={<Link to="/login">Continue to sign in</Link>}><div className="auth-icon"><CircleCheck size={30} /></div></AuthLayout>;
  return <AuthLayout title="Choose a new password" subtitle="Pick something you haven't used here before."
    footer={<Link to="/login">Back to sign in</Link>}>
    <div className="auth-icon"><KeyRound size={28} /></div>
    <form className="auth-form" onSubmit={run(async () => {
      if (!token) throw new Error('This reset link is incomplete. Request a new one.');
      if (password.length < 8) throw new Error('Use at least 8 characters for your password.');
      if (password !== confirm) throw new Error('The passwords do not match.');
      await auth.resetPassword(token, password); setDone(true);
    })}>
      <PasswordFields password={password} setPassword={setPassword} confirm={confirm} setConfirm={setConfirm} />
      <ErrorNotice error={error} />
      {error && /invalid|expired|used/i.test(error) && <Link className="auth-forgot" to="/forgot-password">Request a new reset link</Link>}
      <Submit busy={busy} label="Update password" busyLabel="Updating…" />
    </form>
  </AuthLayout>;
}

/** Opened from the reset email, whether or not someone is signed in on this browser. */
export const LINK_PATHS = ['/reset-password'];

function CreateStore() {
  const auth = useAuth(); const { busy, error, run } = useSubmit(); const [name, setName] = useState('');
  return <AuthLayout title="Name your store" subtitle={`Signed in as ${auth.email}. One last step: create the workspace for your store.`}
    footer={<button className="text-button" onClick={() => void auth.signOut()}>Use a different account</button>}>
    <div className="auth-icon"><Store size={28} /></div>
    <form className="auth-form" onSubmit={run(() => auth.createStore(name))}>
      <label className="field">Store name<input required minLength={2} maxLength={120} value={name} onChange={e => setName(e.target.value)} placeholder="My Store" /></label>
      <ErrorNotice error={error} />
      <Submit busy={busy} label="Create store" busyLabel="Creating your store…" />
    </form>
    {auth.googleClientId && <p className="auth-hint">Joining a teammate's store instead? Sign out and use <b>Continue with Google</b> with the email they added.</p>}
  </AuthLayout>;
}

function ChooseStore() {
  const auth = useAuth(); const [busy, setBusy] = useState('');
  return <AuthLayout title="Choose a store" subtitle={`${auth.email} belongs to more than one store.`}
    footer={<button className="text-button" onClick={() => void auth.signOut()}>Sign out</button>}>
    <div className="auth-stores">{auth.stores.map(store => <button key={store.id} className="auth-store" disabled={!!busy}
      onClick={() => { setBusy(store.id); void auth.chooseStore(store.id).finally(() => setBusy('')); }}>
      <Store size={18} /><span><b>{store.name}</b><small>{store.role}</small></span>{busy === store.id ? <LoaderCircle className="spin" size={16} /> : <ArrowRight size={16} />}
    </button>)}</div>
  </AuthLayout>;
}

function AuthProblem() {
  const auth = useAuth();
  return <AuthLayout title="We couldn't open your workspace" subtitle="Your account is fine. The workspace service did not respond as expected."
    footer={<button className="text-button" onClick={() => void auth.signOut()}>Sign out</button>}>
    <ErrorNotice error={auth.error} />
    <button className="button primary full" onClick={() => window.location.reload()}>Try again<ArrowRight size={17} /></button>
  </AuthLayout>;
}

/** Everything shown before a store workspace is connected. */
export function AuthScreens({ onDeveloperToken }: { onDeveloperToken: () => void }) {
  const auth = useAuth();
  if (auth.status === 'loading') return <main className="auth-page"><LoaderCircle className="spin" size={28} aria-label="Loading" /></main>;
  if (LINK_PATHS.includes(window.location.pathname)) return <ResetPassword />;
  if (auth.status === 'needs_store') return <CreateStore />;
  if (auth.status === 'choose_store') return <ChooseStore />;
  if (auth.status === 'error') return <AuthProblem />;
  return <Routes>
    <Route path="/signup" element={<SignUp />} />
    <Route path="/forgot-password" element={<ForgotPassword />} />
    <Route path="/reset-password" element={<ResetPassword />} />
    <Route path="/login" element={<SignIn onDeveloperToken={onDeveloperToken} />} />
    <Route path="*" element={<Navigate to="/login" replace />} />
  </Routes>;
}
