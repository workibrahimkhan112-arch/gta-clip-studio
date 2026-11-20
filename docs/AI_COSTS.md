# AI models, usage and credit controls

Pricing snapshot: **2026-11-20**, standard USD API rates. Prices/access can change; provider dashboards and invoices are authoritative. This app records local request history and provider-reported usage. It does **not** know your remaining account balance or free quota.

## Defaults that limit spend

- Detection and captions stay local by default. No API key is needed for those paths.
- Cloud review is manual unless **Auto review after analysis** is explicitly enabled.
- Review uses transcripts/measured features first. Vision is optional and sends a few 320px samples, not a recording.
- Fresh settings review at most **5 candidates**, with **2 frames** if vision is enabled.
- A request contains at most 6,000 transcript characters. API responses default to **512 output tokens**. GPT-6 Luna uses no reasoning; Groq GPT-OSS uses low effort and excludes reasoning text from returned content. Hidden reasoning can still count toward Groq usage. Titles are three short suggestions; they are not applied automatically.
- Default daily limits are **20 cloud requests** and a **$0.10 API-cost guard**, across projects/providers, resetting at UTC midnight. Existing saved candidate/frame preferences are preserved.
- Cached results issue no HTTP request. Force/bypass-cache controls are explicit. There are no automatic inference retries or paid-provider/model fallbacks.
- Unknown-price API models are blocked unless you explicitly allow them. Allowing them removes the enforceable dollar ceiling; request limits remain.

The dollar guard reserves a conservative estimate *before* each API request. Failed/interrupted requests can still be billable, so their reservations remain when actual usage is unknown. This is an application guard, **not a guarantee that provider charges cannot exceed it**. Taxes, currency conversion and other provider-specific charges are not included in the estimates. Also set spending/plan limits with your provider. Lowered app limits and withdrawn upload consent apply to queued/running jobs' remaining requests.

## Model overview

Input/output rates below are **per million tokens**. The example is one hypothetical text-only request with **1,000 uncached input and 200 output tokens**, not a measured clip average. Actual prompts, images, reasoning, caches, billing minimums and account tiers affect cost.

| Path | Model | Input | Output | Example request | Notes |
|---|---|---:|---:|---:|---|
| OpenAI API | `gpt-6-luna` | $0.10 | $0.50 | $0.000200 | Default current economical GPT model; reasoning disabled for these short tasks. Cached input: $0.01/M when reported. |
| Gemini API | `gemini-3.5-flash-lite` | $0.30 | $2.50 | $0.000800 | Limited free tier for eligible projects; otherwise API rates. Cached input snapshot: $0.03/M. |
| Groq API | `openai/gpt-oss-20b` | $0.075 | $0.30 | $0.000135 | Limited free tier; text-only review in this app. |
| Groq API, optional | `openai/gpt-oss-120b` | $0.15 | $0.60 | $0.000270 | Larger optional model, not the default. |
| ChatGPT plan | Account-available model | — | — | No API-USD estimate | Uses your eligible plan/app limits and any credits you authorize in ChatGPT Settings. Not an unlimited API key. |
| Local captions | `tiny.en`, CPU INT8 | $0 API | $0 API | $0 API | Local CPU/RAM/storage/electricity still required; English speech. |
| Groq cloud captions | `whisper-large-v3-turbo` | — | — | $0.04/audio hour if paid | About $0.000667/minute; 10-second minimum billing per request. Actual extracted WAV duration is recorded, not the whole video duration. |

For the text example, ten requests would be roughly **$0.0020 OpenAI**, **$0.0080 Gemini**, or **$0.00135 Groq 20B** at paid API rates. Free-tier usage may cost nothing within its quota; the app cannot verify that quota. These examples exclude charges absent from the usage fields (such as cache writes or provider-specific premiums). The app does not enable search/tools or long-context inputs.

`gpt-4.1-nano` is marked shut down and `gpt-5-nano` deprecated in the checked OpenAI catalog. Although the older GPT-5 nano page lists lower nominal rates, it is not a reliable new-app default. The app does not silently upgrade from an unavailable model to a costly one.

## OpenAI API vs. ChatGPT plan

**API key:** select OpenAI and API key mode in Settings; save a write-only key there, or set `OPENAI_API_KEY` on the backend. Requests use the public Responses API and your Platform billing account.

**ChatGPT plan:** select ChatGPT plan mode and choose **Continue with ChatGPT**. The official public-client flow opens the system browser, binds registration to a stable local host ID, uses PKCE/state/nonce, and verifies the returned signed identity. Credentials stay in protected server storage (Windows DPAPI or owner-only files), not browser storage. You can select/reconnect saved registrations, cancel pending sign-in without signing out the active account, and sign out with a bounded remote-revocation attempt.

Model choices come from the active account's catalog. If the low-cost default is unavailable, select an eligible model; there is no automatic upgrade or API-key fallback. Plan requests stream with `store:false` until `response.completed`; incomplete/failed/interrupted streams are errors, not successful results. The preview does not support audio/video input or transcription; keep local/Groq captions.

The plan route currently forbids an API output-token cap. The app uses short prompts/schema, a short-response byte bound, local request limits, and the account's own plan controls instead. Its USD budget does not apply to plan allowance. Manage actual plan/credit permissions at [ChatGPT Settings → Usage](https://chatgpt.com/settings/usage). Account eligibility and provider preview restrictions still apply. A live sign-in/inference was not performed during implementation.

## Reading the Usage panel

Settings → **AI usage** reports requests, local cache hits, input/cached/output tokens, audio seconds, estimated API cost, and pending/uncertain reservations by provider/model/operation. Missing provider usage stays **Unknown**, not zero. Requests made before this ledger or outside this app cannot be reconstructed here. ChatGPT rows show **plan usage**, not a made-up dollar balance. Auth/model-list requests are not counted as inference requests. Test fixtures use mocked usage and never write charges to your provider account.

## Free options and starting a channel

- **Gemini:** eligible active projects have a rate-limited free tier for selected models. Check your project verification, region, model limits and data-use terms; free-tier submissions may be used to improve products. The Google Cloud $300 welcome credit is not the same as Gemini API free quota and is excluded for new Gemini API accounts under the current billing docs.
- **Groq** (with a **q**) has a limited free tier. It is a practical starting point for transcript-based titles/review and cloud Whisper, with request/token/audio quotas.
- **Grok/xAI** (with a **k**) is a different service. Published API model pricing is paid; no dependable ongoing free API allowance was verified here. Treat promotional credits as temporary/account-specific, and check the xAI console rather than assuming free web-app access provides API quota. It is not added as a separate provider in this app.

These tools are sufficient to assist a new channel's workflow, not to guarantee interesting clips, views or monetization. Review the footage and captions, make titles accurate, use authorized material and add your own editing/commentary. An AI editorial score is not a probability of going viral.

## Sources checked

- [GPT-6 Luna model/pricing](https://developers.openai.com/api/docs/models/gpt-6-luna.md)
- [GPT-5 nano status](https://developers.openai.com/api/docs/models/gpt-5-nano.md) and [GPT-4.1 nano status](https://developers.openai.com/api/docs/models/gpt-4.1-nano.md)
- [Official ChatGPT plan usage](https://developers.openai.com/siwc/token-sharing-open-source.md), [registration](https://developers.openai.com/siwc/token-sharing-open-source/sign-in.md), [inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference.md), and [limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations.md)
- [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) and [billing/free tier](https://ai.google.dev/gemini-api/docs/billing)
- [Groq models/pricing](https://console.groq.com/docs/models) and [free/developer tiers](https://console.groq.com/docs/billing-faqs)
- [xAI model pricing](https://docs.x.ai/docs/models)
