# Publishing Setup Guide — YouTube & Instagram

This is a step-by-step guide for setting up the accounts, apps, and credentials the native publishing integration needs. It assumes no prior experience with Google Cloud or Meta for Developers. Do these steps yourself — nothing here can be automated on your behalf, since both platforms require you to log in as the account owner and click through consent/verification screens.

Companion document: `docs/PUBLISHING_PLAN.md` explains *why* each of these steps exists. This document just tells you *what to click*.

Do all of Part 1 and Part 2 before sending anything back — the final checklist tells you exactly what to hand over once you're done.

---

## Part 1 — YouTube / Google Cloud

### 1.1 Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and sign in with the Google account that owns (or manages) your YouTube channel.
2. Top-left project dropdown → **New Project**. Name it something like `aura-video-publishing`. Leave "Organization" as whatever your account defaults to.
3. Wait for it to finish creating, then make sure it's selected in the top project dropdown before continuing.

### 1.2 Enable the YouTube Data API v3

1. In the left sidebar (or search bar at top): **APIs & Services → Library**.
2. Search "YouTube Data API v3" → open it → click **Enable**.

### 1.3 Configure the OAuth consent screen

This is the screen you'll see (and approve) when the app asks for permission to upload to your channel.

1. **APIs & Services → OAuth consent screen**.
2. User type: **External** (Internal is only available for Google Workspace organizations — a personal Gmail account must use External).
3. Fill in the required fields: app name (e.g. "Aura-Video Publisher"), your email as support contact, your email again as developer contact. A logo/homepage isn't required for this use case.
4. **Scopes** step: add these two, and only these two —
   - `https://www.googleapis.com/auth/youtube.upload` — lets the app upload videos to your channel. This is the core permission; without it nothing works.
   - `https://www.googleapis.com/auth/youtube.force-ssl` — lets the app manage the video after upload (set the custom thumbnail, post the after-upload comment, read status). Without it, uploads would work but thumbnails/comments would silently fail.
   - Do **not** add broader scopes (e.g. full `youtube` scope, channel management, analytics) — the app doesn't need them and requesting less means less to explain if you ever go through Google's verification process.
5. **Test users** step: add the Google account email you'll actually use to connect the app.
6. Save. Your app is now in **Testing** status.
7. **Important — read this before moving on:** while your app is in Testing status, the refresh token Google issues expires after **7 days**, meaning the app would ask you to reconnect roughly weekly. To avoid that:
   - Go back to **OAuth consent screen** and click **Publish App**.
   - For an app requesting only `youtube.upload` + `youtube.force-ssl` (both are "sensitive" but not "restricted" scopes) used by a single person, Google's automated review is typically fast and doesn't require the more involved verification process reserved for public-facing apps with many users. You may see a warning that your app "hasn't completed verification" — for personal/single-user use, you can generally proceed past this (Google shows an "unverified app" warning on the consent screen itself, which you'll just click through as the owner) rather than needing full verification. If Google does prompt for full verification (it can happen), it will ask for a short demo video showing the OAuth flow and how the scopes are used — this is a known, sometimes-required step for the `youtube.upload` scope specifically; budget a day or two if you hit it.
   - Publishing the app removes the 7-day refresh-token ceiling regardless of whether it's "verified" — that's the main thing you're doing here.

### 1.4 Create the OAuth Client ID

1. **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
2. Application type: **Web application**.
3. Name: anything, e.g. `aura-video-web`.
4. **Authorized redirect URIs** — add exactly:
   ```
   http://localhost:PORT/api/v1/oauth/google/callback
   ```
   Replace `PORT` with whatever port the app runs on locally (check your `config.toml`/how you start the server — commonly `8000`). If you ever run this on a different host/port, add that as an additional redirect URI rather than replacing this one.
5. Create → you'll be shown a **Client ID** and **Client Secret**. Copy both somewhere safe now — the secret is only shown once (you can always generate a new one later if you lose it).

### 1.5 Request the API audit (for public video visibility)

Without this, every video the app uploads is locked to **Private** and can never be switched to Public/Unlisted by anyone — this is a YouTube-wide anti-abuse policy for any unaudited API client, not a bug in the app.

1. Go to Google's **YouTube API Services - Audit and Quota Extension Form** (search "YouTube API audit form" from your Google account, or find it linked from `developers.google.com/youtube/v3/guides/quota_and_compliance_audits`).
2. Fill it out describing the app: a personal tool that uploads your own video content to your own channel, requesting an audit so uploaded videos can be made public. Be honest and specific — vague submissions get bounced back for more detail.
3. This can take some time to process (historically anywhere from a couple of weeks to longer) — submit it now, even before finishing the rest of this guide, so the clock starts. Until it's approved, the app will upload videos as Private with a clear on-screen notice — this is expected, not an error.

### 1.6 Quota increase (optional, not required to start)

Default daily allocation is 10,000 units shared across most endpoints, **plus a separate dedicated allowance of 100 calls/day specifically for `videos.insert`** (uploads) — so you can upload up to ~100 videos/day by default without needing this step at all. Only request a quota increase later if you outgrow that, via the same Audit and Quota Extension Form as above (a passed audit is generally a prerequisite for a quota increase request).

### 1.7 Enable custom thumbnails (phone verification)

Separate from everything above — this is a YouTube Studio setting, not a Cloud Console one.

1. Go to [studio.youtube.com](https://studio.youtube.com) → **Settings → Channel → Feature eligibility**.
2. Find **"Features that require phone verification"** → **Verify phone number**.
3. Enter your phone number, receive a code by text or call, enter it. This unlocks custom thumbnails immediately (note: a given phone number can only verify up to 2 channels per year).
4. If you skip this, the app will still upload and publish videos fine — it just won't be able to set a custom thumbnail, and will tell you why in the UI rather than failing silently.

---

## Part 2 — Instagram / Meta

### 2.1 Convert your Instagram account to Professional (Business)

1. In the Instagram app: **Profile → Edit profile → switch to professional account** (wording may vary slightly by app version) → choose **Business** (not Creator — Business accounts are the ones third-party apps are built to publish through).
2. You do **not** need to link a Facebook Page for the path this guide uses (see 2.3) — if Instagram prompts you to link one during setup, you can skip it.

### 2.2 Create a Meta app

1. Go to [developers.facebook.com/apps](https://developers.facebook.com/apps) and log in with the Facebook account tied to your Instagram (Meta requires a Facebook account to own the developer app, even though the publishing flow itself won't touch your Facebook Page).
2. **Create App** → choose the "Other" / business-type use case if prompted, then give it a name (e.g. "Aura Video Publisher").
3. Once created, you land on the app dashboard, in **Development mode** by default — leave it there. Development mode means only accounts with a role on this app (i.e., you) can use it, which is exactly what lets you skip Meta's App Review process entirely for a single-owner tool.

### 2.3 Add the Instagram product

1. On the app dashboard, **Add Product** → find **Instagram** → add the **Instagram API with Instagram Login** product (sometimes labeled "Business Login for Instagram"). This is the recommended path — it does not require linking a Facebook Page, unlike the older Facebook-Login-based Instagram integration.
2. Follow the product's setup steps to configure it for your app.

### 2.4 Add your account with a role

1. **App settings → Roles → Roles** (or similar, Meta's UI shifts around) — add your own Instagram/Facebook account as an **Administrator** or **Developer** on the app, if it isn't already there as the app creator.
2. This is the step that lets Development mode work for you without App Review — only accounts with a role on the app can authorize it while it's in Development mode.

### 2.5 Get your App ID and App Secret

1. **App settings → Basic**. Copy the **App ID** and **App Secret** (click "Show" — it may ask you to re-enter your Facebook password).

### 2.6 Configure redirect URI

1. Within the Instagram product's settings, find the OAuth redirect URI configuration and add:
   ```
   http://localhost:PORT/api/v1/oauth/instagram/callback
   ```
   Same `PORT` substitution as the YouTube step (1.4).

### 2.7 Scopes

The app will request exactly these two — nothing broader:
- `instagram_business_basic` — read basic account info (needed to show your account name/avatar in Settings, and confirm the connection).
- `instagram_business_content_publish` — the actual publish permission (create containers, publish Reels).

### 2.8 Token specifics (informational — the app handles this automatically once connected)

- The OAuth callback returns a **short-lived token** (valid ~1 hour).
- The app immediately exchanges it for a **long-lived token** (valid ~60 days).
- The app refreshes the long-lived token automatically before it expires (as long as you reconnect if it ever lapses — Settings will show "Reconnect Instagram" if a refresh ever fails, e.g. because you revoked access from the Instagram app itself).

---

## Part 3 — Media hosting (Cloudflare R2)

Instagram's publishing API downloads your video from a public URL — it cannot reach a file sitting only on your laptop. The app stages each video to temporary cloud storage right before publishing to Instagram, and deletes it immediately after Instagram confirms it downloaded the file (plus an automatic 48-hour cleanup rule as a safety net). Cloudflare R2 is recommended: free tier is generous for this volume, and unlike AWS S3 it has **no charge for downloading data out** — which matters here since every single Instagram publish is exactly that (Meta's servers downloading your video once).

1. Sign up at [dash.cloudflare.com](https://dash.cloudflare.com) (free account is fine).
2. Left sidebar → **R2 Object Storage** → **Create bucket**. Name it e.g. `aura-video-publish-staging`. Location: Automatic.
3. **Bucket → Settings → CORS Policy** — add a permissive read rule so presigned URLs work:
   ```json
   [
     {
       "AllowedOrigins": ["*"],
       "AllowedMethods": ["GET"],
       "AllowedHeaders": ["*"]
     }
   ]
   ```
4. **Bucket → Settings → Lifecycle rules → Add rule** — delete objects older than **2 days (48 hours)**. This is the safety net that catches anything the app doesn't clean up itself after a crash.
5. **R2 → Manage API tokens → Create API token** — permissions: Object Read & Write, scoped to this one bucket if the UI lets you narrow it. Copy the **Access Key ID**, **Secret Access Key**, and note your **Account ID** and the bucket's **S3 API endpoint** (shown on the bucket's "Settings" page, looks like `https://<account-id>.r2.cloudflarestorage.com`).

---

## WHAT YOU MUST GIVE ME

Everything below, once you've completed Parts 1–3. I'll wire these into `.env.example`/`.env` (secrets) and `config.example.toml`/`config.toml` (non-secret IDs and settings) exactly as named here — nothing else needs to change on your end.

| # | Credential | From step | Env var name |
|---|---|---|---|
| 1 | Google OAuth Client ID | 1.4 | `GOOGLE_OAUTH_CLIENT_ID` |
| 2 | Google OAuth Client Secret | 1.4 | `GOOGLE_OAUTH_CLIENT_SECRET` |
| 3 | Confirmation you submitted the YouTube API audit form (or its reference/ticket number if given one) | 1.5 | *(not a credential — just tell me)* |
| 4 | Meta App ID | 2.5 | `META_APP_ID` |
| 5 | Meta App Secret | 2.5 | `META_APP_SECRET` |
| 6 | Cloudflare R2 Access Key ID | Part 3, step 5 | `R2_ACCESS_KEY_ID` |
| 7 | Cloudflare R2 Secret Access Key | Part 3, step 5 | `R2_SECRET_ACCESS_KEY` |
| 8 | Cloudflare Account ID | Part 3, step 5 | `R2_ACCOUNT_ID` |
| 9 | R2 bucket name | Part 3, step 2 | `R2_BUCKET` |
| 10 | R2 S3 API endpoint URL | Part 3, step 5 | `R2_ENDPOINT` |
| 11 | `TOKEN_ENCRYPTION_KEY` — **you generate this one yourself**, run: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and paste the output | — | `TOKEN_ENCRYPTION_KEY` |
| 12 | The local port your server runs on (so I confirm the redirect URIs you registered in 1.4/2.6 match) | — | *(not a credential — just confirm the number)* |

Nothing else is required to start. I will not begin Part B (implementation) until you've sent these and confirmed you're happy with `docs/PUBLISHING_PLAN.md`.
