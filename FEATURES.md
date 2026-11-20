# Feature API contract

These additions extend the original endpoints in `CONTRACTS.md`. Settings validation fills defaults for older saved JSON. Keys/tokens never belong in Settings, job payloads or browser storage.

## Settings

- `caption_provider`: local (default) or groq. `caption_style` remains standard (default), animated, or none. None opts out of automatic transcription/export captions.
- `default_layout`: split (default) or blurred, applied only to new imports. Legacy projects without layout keep blurred output.
- `ai_scoring_provider`: none (default), gemini, groq, openai.
- `ai_scoring_model`: empty selects the provider default. OpenAI defaults to gpt-6-luna; Gemini to gemini-3.5-flash-lite; Groq to openai/gpt-oss-20b.
- `openai_auth_mode`: api_key (default) or chatgpt. Never fall back between billing modes.
- `ai_review_mode`: text (default) or vision. Groq remains text-only.
- `auto_ai_review`: false by default. Enabling it requires provider credentials and cloud consent before any upload.
- `ai_scoring_max_candidates`: default5, range1–20; `ai_scoring_frames`: default2, range1–8 (vision needs at least2).
- `ai_daily_request_limit`: default20, range1–500; UTC reset, shared across projects/providers.
- `ai_daily_budget_usd`: default0.10, range0–100. Zero blocks API requests, not ChatGPT-plan allowance. Conservative app guard, not an invoice guarantee.
- `ai_max_output_tokens`: default512, range128–2048. ChatGPT plan requests omit this unsupported field and instead use request/schema/byte bounds plus provider limits.
- `ai_allow_unpriced_models`: false by default. Explicit opt-in retains request caps but cannot enforce a dollar ceiling.
- `cloud_consent`: false by default; checked live even for queued/running requests.

See `docs/AI_COSTS.md` for checked model rates, limits and billing caveats. Saved existing candidate/frame preferences are preserved.

## Project layout

`PATCH /api/projects/{id}/layout` accepts a complete layout and returns Project:

```json
{
  "mode": "split",
  "facecam": null,
  "gameplay": {"x": 0, "y": 0, "width": 1, "height": 1},
  "split_ratio": 0.5,
  "facecam_fit": "fill",
  "gameplay_fit": "fit",
  "caption_position": "lower_middle"
}
```

Crop coordinates are finite normalized source values, with positive dimensions contained in0–1. Split requires a saved facecam before export. Ratio is top-panel fraction0.2–0.7. Fit/fill and lower_middle/center/bottom are supported. Queued exports hold a project snapshot; later edits cannot change them. UI resize handles cover all four edges and corners, with keyboard/numeric alternatives.

## Visual subtitle style

Highlight can hold `subtitle_style` or null. `PATCH /api/highlights/{id}` saves a validated partial-style object (defaults filled); explicit null resets to legacy/project placement. Style-only edits do not invalidate transcript or AI reviews. Fields:

- font_family: Arial, Impact, Verdana; font_size:18–96, default48 at720px output width; bold:true.
- text_color:#FFFFFF; highlight_color:#00FF00; outline_color:#141414; outline_width:0–8, default3 at720px.
- background_color:#000000; background_opacity:0–0.85, default0.
- x:default0.5, center normalized0–1; y:default0.73, range0.05–0.95; width:default0.84, range0.2–1. Horizontal bounds must fit around x.

Colors are #RRGGBB only. Size/outline scale with output width. Standard and animated ASS use saved font/color/width/position/background. Animated still requires actual word timestamps. Absent style preserves legacy ASS behavior exactly. Preview wrapping is approximate; export uses libass font shaping.

## Caption, review and title jobs

One heavy queue serializes analyze/export/youtube/captions/score/titles jobs. Analysis saves results first, then queues missing selected-clip captions when captions are enabled. Optional auto review queues only when explicitly enabled, consented and configured. Caption/AI failures never remove detection results.

- `POST /api/highlights/{id}/captions {force?:boolean}` →202 Job.
- `POST /api/projects/{id}/captions {force?:boolean}` →202 Job for all final highlights.
- `POST /api/projects/{id}/score {force?:boolean}` →202 Job for bounded top candidates.
- `POST /api/highlights/{id}/titles {force?:boolean}` →202 Job. Requires transcript; returns up to three suggestions on Highlight.ai_titles. Actual title changes only through explicit PATCHtitle.
- Analyze additionally accepts `auto_enhance:boolean=true`; benchmark CLI sets false to isolate discovery/repeat runs without changing preferences.

Caption jobs persist each successful clip before the next begins. Existing manual transcripts remain unless force is explicit. Cache keys include source stat, clip bounds and relevant provider/model/transcript/prompt settings. Cached cloud work records a zero-request event. Force is the only cache-bypass action. Provider/auth/quota failures do not trigger model/provider upgrades or inference retries.

Highlight additions: ai_review (separate provider/model/score/rubric/explanation/evidence/created_at/cache_hit), ai_error, ai_titles, title_error, caption_status, caption_error, subtitle_style. Transcript changes invalidate content-dependent AI output; styling/renaming do not.

## Credentials and official ChatGPT connection

- `GET /api/integrations` returns gemini/groq/openai configured/source statuses only.
- `PUT /api/integrations/{provider} {api_key:string}` is write-only. Empty removes local key; environment variable still wins.
- `GET /api/chatgpt` returns public connection state, pending/error, active account ID and account labels; never access/refresh tokens.
- `POST /api/chatgpt/connect {account_id?:string}` returns official authorization URL/expiry for a user-clicked browser flow.
- `POST /api/chatgpt/select {account_id:string}` changes active registration.
- `POST /api/chatgpt/disconnect {account_id?:string}` attempts revocation then clears local tokens, retaining registration/host identity.
- `POST /api/chatgpt/cancel` closes pending authorization without signing out the active account.
- `GET /api/chatgpt/models` returns active-account visible `{id,name}` catalog, not a hardcoded permission claim.

OAuth uses official OpenAI endpoints, an ephemeral127.0.0.1/auth/callback listener, stable opaque host ID, fresh state/nonce/PKCE, issued-client binding and signed-ID verification. Refresh rotation is serialized. Protected files use Windows DPAPI or Unix0600. Authorization URLs can contain OIDC hints; do not log or share them. No scraping or reading ChatGPT/Codex browser credentials. Plan eligibility/live access requires the user's account and is not guaranteed by a successful local test.

## Metering

`GET /api/usage` returns today's UTC requests/cache events, per-provider/model/operation rows, provider-reported tokens/audio seconds, estimated USD, conservative reservations, unknown-use counts, recent sanitized entries and a pricing snapshot. It is not an account balance or invoice. Groq captions record the submitted WAV duration with provider billing minimums; local captions incur no API request.

Ledger reserves before each inference POST and atomically checks daily request/API-cost guards. Unknown/failed/interrupted billing retains reservations. Plan usage has no fabricated USD price. Supported providers use fixed HTTPS endpoints, bounded request/response bodies, no redirects/proxy inheritance, cancellable requests and sanitized errors. Gemini/OpenAI vision uploads only sparse samples, never the original recording; title generation sends text only.
