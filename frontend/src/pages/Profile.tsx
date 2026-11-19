import { useEffect, useState, type FormEvent } from 'react';
import { CircleCheck, KeyRound, LoaderCircle, Mail, UserRound } from 'lucide-react';
import { ApiClient } from '../api';
import { problem, useAuth } from '../auth';
import { Badge, ErrorNotice, Loading, PageHeader } from '../components/ui';
import { date } from '../format';

type Profile = { email: string; full_name: string; has_password: boolean; google_linked: boolean; email_verified: boolean; created_at: string };

// Account endpoints use the HttpOnly stay-signed-in cookie, not the workspace token.
const accounts = new ApiClient('');

/** The signed-in person's own profile: name, sign-in methods and password. */
export function ProfilePage() {
  const auth = useAuth();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { accounts.get<Profile>('/api/auth/profile/').then(setProfile).catch(e => setError(problem(e))); }, []);

  if (!auth.enabled) return <><PageHeader eyebrow="YOUR ACCOUNT" title="Profile" /><section className="panel"><p>Profiles are available when you sign in with an account. This workspace uses a developer token.</p></section></>;
  return <>
    <PageHeader eyebrow="YOUR ACCOUNT" title="Your profile" description="Your details and how you sign in to Customerly." />
    <ErrorNotice error={error} />
    {!profile && !error && <Loading text="Loading your profile…" />}
    {profile && <div className="profile-grid">
      <Details profile={profile} onSaved={setProfile} />
      <SignInMethods profile={profile} />
      <PasswordForm profile={profile} onSaved={setProfile} />
    </div>}
  </>;
}

function useSave() {
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [saved, setSaved] = useState('');
  const run = (action: () => Promise<string>) => async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(''); setSaved('');
    try { setSaved(await action()); } catch (e) { setError(problem(e)); } finally { setBusy(false); }
  };
  return { busy, error, saved, run };
}

function Saved({ text }: { text: string }) {
  return text ? <p className="profile-saved"><CircleCheck size={15} />{text}</p> : null;
}

function Details({ profile, onSaved }: { profile: Profile; onSaved: (p: Profile) => void }) {
  const [name, setName] = useState(profile.full_name); const { busy, error, saved, run } = useSave();
  return <section className="panel">
    <div className="section-heading"><div><h2><UserRound size={18} /> Personal details</h2><p>Member since {date(profile.created_at)}</p></div></div>
    <form className="profile-form" onSubmit={run(async () => {
      onSaved(await accounts.request<Profile>('/api/auth/profile/', { method: 'PATCH', body: JSON.stringify({ full_name: name }) }));
      return 'Your name was saved.';
    })}>
      <label className="field">Full name<input required minLength={2} maxLength={150} value={name} onChange={e => setName(e.target.value)} placeholder="Your full name" /></label>
      <label className="field">Email<input value={profile.email} readOnly disabled /><small>Your email is how you sign in, so it can't be changed here.</small></label>
      <ErrorNotice error={error} /><Saved text={saved} />
      <div className="button-row"><button className="button primary" disabled={busy || name.trim() === profile.full_name}>{busy ? <LoaderCircle className="spin" size={16} /> : null}Save changes</button></div>
    </form>
  </section>;
}

function SignInMethods({ profile }: { profile: Profile }) {
  return <section className="panel">
    <div className="section-heading"><div><h2><Mail size={18} /> Sign-in methods</h2><p>Ways you can sign in to this account.</p></div></div>
    <div className="profile-methods">
      <div><b>Google</b><span>{profile.google_linked ? 'Continue with Google uses this account.' : 'Use Continue with Google with this email to link it.'}</span>
        <Badge tone={profile.google_linked ? 'green' : ''}>{profile.google_linked ? 'Connected' : 'Not connected'}</Badge></div>
      <div><b>Email and password</b><span>{profile.has_password ? 'You can sign in with your email and password.' : 'Set a password below to also sign in without Google.'}</span>
        <Badge tone={profile.has_password ? 'green' : ''}>{profile.has_password ? 'Password set' : 'No password'}</Badge></div>
      <div><b>Email</b><span>{profile.email_verified ? 'Confirmed through Google or a password reset link.' : 'Not confirmed yet. Continue with Google or reset your password to confirm it.'}</span>
        <Badge tone={profile.email_verified ? 'green' : ''}>{profile.email_verified ? 'Confirmed' : 'Not confirmed'}</Badge></div>
    </div>
  </section>;
}

function PasswordForm({ profile, onSaved }: { profile: Profile; onSaved: (p: Profile) => void }) {
  const [current, setCurrent] = useState(''); const [next, setNext] = useState(''); const [confirm, setConfirm] = useState('');
  const { busy, error, saved, run } = useSave();
  return <section className="panel">
    <div className="section-heading"><div><h2><KeyRound size={18} /> {profile.has_password ? 'Change password' : 'Set a password'}</h2>
      <p>{profile.has_password ? 'Changing it signs you out on every other device.' : 'Add a password so you can also sign in with your email.'}</p></div></div>
    <form className="profile-form" onSubmit={run(async () => {
      if (next.length < 8) throw new Error('Use at least 8 characters for your new password.');
      if (next !== confirm) throw new Error('The new passwords do not match.');
      const result = await accounts.post<Profile>('/api/auth/change-password/', { current_password: current, new_password: next });
      onSaved(result); setCurrent(''); setNext(''); setConfirm('');
      return profile.has_password ? 'Password changed. Other devices were signed out.' : 'Password set.';
    })}>
      {profile.has_password && <label className="field">Current password<input type="password" autoComplete="current-password" required value={current} onChange={e => setCurrent(e.target.value)} /></label>}
      <label className="field">New password<input type="password" autoComplete="new-password" required minLength={8} value={next} onChange={e => setNext(e.target.value)} />
        <small>At least 8 characters. Avoid common words and only-number passwords.</small></label>
      <label className="field">Confirm new password<input type="password" autoComplete="new-password" required value={confirm} onChange={e => setConfirm(e.target.value)} /></label>
      <ErrorNotice error={error} /><Saved text={saved} />
      <div className="button-row"><button className="button primary" disabled={busy}>{busy ? <LoaderCircle className="spin" size={16} /> : null}{profile.has_password ? 'Change password' : 'Set password'}</button></div>
    </form>
  </section>;
}
