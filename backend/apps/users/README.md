# apps/users: accounts and sign-in

People sign in with **Google** or with an **email + password**. Django owns
the accounts; store membership and roles stay in the canonical `store_users`
table. After sign-in the API returns the **same signed workspace token** that
the ingestion, KPI, Customer 360 and segment APIs already accept, so none of
those APIs changed. The only email is the forgot-password link.

## Flow

1. **Continue with Google** (one click), or **Create account** with email +
   password (signed in immediately).
2. A new account names its store (`plan = free`, the account becomes `owner`).
   Several stores → choose one.
3. An HttpOnly "stay signed in" cookie lasts 30 days. The frontend keeps only
   the short-lived workspace token (1 hour) and renews it automatically.

## Which stores an account can open

Anyone can type any email into a password form, so a password-only email is
**not proven**. Google has verified the email of a Google sign-in.

| Account | Opens |
|---------|-------|
| Password only (email not proven) | Only the stores it created itself |
| Signed in with Google at least once (email proven) | Its own stores **and** every `store_users` row with that email, e.g. a store a teammate added it to |

Signing in with Google using the same email as a password account **links**
them (same account, same stores).

## Forgot password

**Forgot password?** on the sign-in page → enter email → a reset link is
emailed (same answer whether or not the email has an account) → **Choose a new
password**. The link expires in 1 hour and works once. Resetting signs the
account out everywhere, unlocks it, and counts as proof of the email (the
person received it), so the account then also opens stores a teammate added it
to. A Google-only account can use it to add a password.

Without `EMAIL_HOST`, emails are printed in the Django terminal (copy the link
from there). For real emails use any existing Gmail with 2-Step Verification
and an **app password** (https://myaccount.google.com/apppasswords):

```
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=your.gmail@gmail.com
EMAIL_HOST_PASSWORD=<16-character app password, no spaces>
DEFAULT_FROM_EMAIL=Customerly <your.gmail@gmail.com>
FRONTEND_URL=http://127.0.0.1:5173
```

Test it with `python manage.py sendtestemail you@example.com`.

## Endpoints (`/api/auth/`)

| Method | Path | Body | Result |
|--------|------|------|--------|
| GET | `config/` | – | `{"google_client_id": ... or null, "password": true}` |
| POST | `signup/` | `email, password` | `201`, signed in: `needs_store`; `400` invalid email/weak password; `409` email already registered |
| POST | `signin/` | `email, password` | `ok` (workspace token), `needs_store` or `choose_store`; `401` wrong email/password; `429` locked |
| POST | `google/` | `credential` (Google ID token) | same results as `signin/`; `401` token not valid; `503` Google not configured/unreachable |
| POST | `session/` | `store_id?` | fresh workspace token from the cookie; `401` when signed out |
| POST | `store/` | `name` | `201` new store with the account as owner; `409` if it already has one |
| POST | `forgot-password/` | `email` | always `200`; `503` if the email could not be sent |
| POST | `reset-password/` | `token, password` | `200`; `400` weak password or invalid/used/expired link |
| POST | `signout/` | – | clears the cookie |

## Security

- Google ID tokens are verified with Google's public keys (signature, audience
  = our client id, issuer, expiry) and must carry a verified email.
- Passwords are stored only as Django password hashes, with strength rules
  (8+ characters, not common, not only numbers, not similar to the email).
- 5 wrong passwords lock the account for 15 minutes (Google sign-in or a
  password reset unlocks it); requests are rate-limited per IP (`AUTH_THROTTLE_RATE`, default
  30/minute). Wrong password and unknown email get the same answer.
- Reset links are signed with `DJANGO_SECRET_KEY`, expire after 1 hour and
  stop working once the password changes (so they work once).
- The stay-signed-in cookie is signed, `HttpOnly`, `SameSite=Strict`, limited
  to `/api/auth/`, and HTTPS-only outside local development.
- `user_accounts` and `user_account_stores` have row-level security enabled,
  so Supabase's Data API roles cannot read them.

## Setup

1. Apply the migration once to the shared database (from `backend/`):
   `python manage.py migrate users`
2. Google sign-in (optional; without it only email + password is offered):
   - [Google Cloud Console](https://console.cloud.google.com/) → create a
     project → **APIs & Services → OAuth consent screen**: External, app name,
     support email; add your team as **test users** while in testing.
   - **APIs & Services → Credentials → Create credentials → OAuth client ID →
     Web application**. Authorized JavaScript origins:
     `http://127.0.0.1:5173` and `http://localhost:5173` (add the production
     URL later). No redirect URI is needed.
   - Put the client id (it ends in `.apps.googleusercontent.com`; it is a
     public value) in `backend/.env`: `GOOGLE_CLIENT_ID=...` and restart Django.
     The frontend reads it from `/api/auth/config/`.

## Tests

```bash
python -m pytest apps/users      # from backend/; Google is stubbed
```
