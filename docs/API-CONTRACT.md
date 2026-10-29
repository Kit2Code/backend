# Grader API Contract & React + Vite Frontend Handoff

**Owner:** Backend — MiyaZaki  
**Frontend:** `Kit2Code/website` — React + Vite  
**Backend:** Django + Django REST Framework (planned API layer)  
**Database:** PostgreSQL  
**Last revised:** October 26, 2026  
**Status:** Implementation contract for frontend/backend integration

---

## 1. Purpose: read this first

This document is the shared contract between the frontend and backend repositories. The goal is to let the frontend and backend be developed in parallel without guessing endpoint names, request formats, response fields, authentication behavior, or error handling.

The existing prototype is a working Django website with PostgreSQL, a judge worker, an `isolate` sandbox, and separated deployment roles. The migration replaces the Django-template/htmx public website with a React + Vite frontend and a JSON API. Reuse existing models, judging logic, limits, and worker protocol where practical; improve the architecture when it has a clear benefit.

### Product scope

**This website handles practice:**

- Public problem browsing, search, and archive navigation.
- Source-code submissions and judging results.
- Public submissions list and the signed-in user's submissions.
- Global practice ranking and user profiles.
- Google sign-in, registration, and account settings.

**This website does not handle contests.** Contest workflows use CMS separately. Problem importing, publishing, pausing grading, and backups remain administrator operations rather than frontend pages in the first release.

### Responsibilities

| Area | Responsibility |
|---|---|
| React + Vite frontend | Page design, routing, forms, client-side validation, loading/empty/error states, and calls to the documented API |
| Django API | Authentication, authorization, validation, query behavior, rate limits, data shaping, and the documented response/error formats |
| Existing judge infrastructure | Isolated code execution, worker coordination, and reporting results through the existing internal API |
| CMS | Contest management and contest-specific workflow |

The frontend developer is free to choose the visual design and component structure. This document defines the API contract and required behavior, not the page styling.

### How to read decisions in this document

- **Contract:** behavior the frontend may rely on because it is specified here.
- **Frontend recommendation:** an implementation pattern intended to avoid common integration problems.
- **Backend confirmation required:** the original design does not define this detail sufficiently. Do not silently guess; agree on it and update this document or the generated OpenAPI schema.

---

## 2. Architecture and environments

### Production

Use **one origin** for the frontend and API:

- Caddy serves the built React `dist/` directory at `/`.
- Caddy falls back to `index.html` for frontend routes such as `/main` and `/problems/aplusb`.
- Caddy proxies `/api/*` to the Django public web role.
- The frontend calls relative URLs such as `/api/problems`; it does not hardcode a production API hostname.
- No cross-origin CORS configuration is needed for this same-origin production setup.

The public role and admin role use the same image but are separately exposed. The frontend must never call the Django admin site or the worker's `/internal/*` API.

### Development

Vite proxies `/api` to the **development edge**, not directly to a bare `manage.py runserver`. The public role expects the edge to set `X-Edge-Zone: public`.

Example Vite proxy (adapt to the existing `vite.config.js`; do not replace unrelated plugins):

```js
server: {
  proxy: {
    '/api': 'http://127.0.0.1:8000',
  },
},
```

The backend's development environment must treat the Vite origin as the public site. For the example Vite address `http://localhost:5173`:

- Set `PUBLIC_BASE_URL=http://localhost:5173` in the backend development environment.
- Ensure that origin is trusted for CSRF.
- Add `http://localhost:5173/api/auth/google/callback` to the development `FAKE_OIDC_REDIRECT_URIS` when using the fake OIDC setup.

Do not change the frontend to call a different hostname just to make development requests work. Configure the proxy and backend environment instead.

### Content Security Policy

The existing edge policy includes `default-src 'self'` and `img-src 'self' data:`. Remote fonts, scripts, and remote images embedded in problem statements may therefore be blocked. Prefer self-hosted assets. Any policy relaxation is an intentional deployment/security decision, not something the frontend should work around with arbitrary external resources.

---

## 3. API conventions

| Topic | Contract |
|---|---|
| Base path | `/api` |
| Transport | HTTPS in production; JSON request/response bodies unless an endpoint explicitly says otherwise |
| Character encoding | UTF-8 |
| Authentication | Django session cookie named `g_sess`, set after Google sign-in; intended lifetime 30 days |
| Cookie sending | Axios client uses `withCredentials: true` |
| CSRF | Unsafe methods (`POST`, `PUT`, `PATCH`, `DELETE`) require `X-CSRFToken` matching the `g_csrf` cookie |
| CSRF bootstrap | `GET /api/auth/me` always returns `200` and also sets the CSRF cookie |
| IDs | Problem and group routes use slugs where shown; submissions use integer IDs; users use usernames |
| Time | ISO 8601 UTC strings, e.g. `2026-10-26T09:58:00Z`; display in Asia/Bangkok time in the UI |
| Language | `?lang=th` or `?lang=en`; default is Thai (`th`). Every `GET` endpoint accepts `lang`, ignoring it when it has nothing to localize. Browser locale is not used by the backend |
| Pagination | `?page=N`, where `N` must be a positive integer; otherwise `400 validation_error`. Standard list response is `{ count, page, page_size, results }`. Normal lists use 50 rows; ranking uses 100 |
| Page beyond end | Returns an empty `results` array with the correct `count`. This deliberately differs from the prototype, which returns the last page; that behavior would make a "load more" list repeat rows |
| Error body | `{ "error": { "code", "message", "fields"? } }` |
| Submission caching | Submission results use `Cache-Control: no-store` |
| Photo caching | User photos use `Cache-Control: public, max-age=3600`; the supplied `?v=` query changes when the photo changes |

### Language behavior

Every `GET` endpoint accepts `lang`; it is ignored where there is nothing to localize. Localized values appear in:

- `GET /api/site`: `recent_problems[].title`.
- Problem lists and problem details: `title` and the selected statement.
- Archive group pages: group and folder names, and problem titles in the rows.
- Submission `problem.title`.
- Public profiles: `title.name`, folder names, and problem titles.

If the requested language is unavailable for a problem or name, the API falls back to the other language and then to the slug where specified. The response's `statement.language` tells the frontend which statement language was actually selected.

**Frontend recommendation:** keep one selected language in the frontend and let the shared Axios client attach it to every `GET` request (see section 4), instead of passing `lang` through each helper. The API defaults to Thai if `lang` is omitted. Do not infer the API language from the browser language.

### Query parameters and URL encoding

Use Axios `params` for query strings rather than building them manually. Use route params as values and encode them as required when composing URLs. Never use a problem title as its identifier; use the documented slug.

---

## 4. Recommended frontend API client

Remove the existing placeholder helpers that call `GET /`, `POST /createData`, `PUT /:id`, and `DELETE /:id`; those routes are not part of this API.

A small shared Axios client is recommended. The example below is integration guidance; fit the file names to the existing React project instead of reorganizing the whole frontend solely to match this sample.

```js
// src/api/client.js
import axios from 'axios';

export const api = axios.create({
  baseURL: '/api',
  timeout: 10000,
  withCredentials: true,
  xsrfCookieName: 'g_csrf',
  xsrfHeaderName: 'X-CSRFToken',
});

let currentLang = 'th';

export function setApiLanguage(lang) {
  currentLang = lang === 'en' ? 'en' : 'th';
}

// Attach the selected language to every GET request. An explicit `lang` in params wins.
api.interceptors.request.use((config) => {
  if (config.method === 'get') {
    config.params = { lang: currentLang, ...config.params };
  }
  return config;
});

export async function get(url, params, config) {
  const response = await api.get(url, { ...config, params });
  return response.data;
}
```

Suggested API functions (you can split these into modules by feature):

```js
// src/api/index.js
import { api, get } from './client';

export const getMe = () => get('/auth/me');
export const getSite = () => get('/site');
export const listProblems = (params) => get('/problems', params);
export const getProblem = (slug) =>
  get(`/problems/${encodeURIComponent(slug)}`);
export const listGroups = () => get('/groups');
export const getGroup = (slug, subSlug, params) => {
  const path = subSlug
    ? `/groups/${encodeURIComponent(slug)}/${encodeURIComponent(subSlug)}`
    : `/groups/${encodeURIComponent(slug)}`;
  return get(path, params);
};
export const getSubmission = (id, config) =>
  get(`/submissions/${id}`, undefined, config);
export const listSubmissions = (params) => get('/submissions', params);
export const listMySubmissions = (params) => get('/submissions/mine', params);
export const getRanking = (params) => get('/ranking', params);
export const getUserProfile = (username) =>
  get(`/users/${encodeURIComponent(username)}`);
// The source endpoint returns text/plain. Axios would JSON-parse a source such as `42` or `{}`
// into a number or object, so ask for the raw text.
export const getSubmissionSource = async (id, config) =>
  (await api.get(`/submissions/${id}/source`, { ...config, responseType: 'text' })).data;
export const updateMe = async (patch) =>
  (await api.patch('/me', patch)).data;
export const logout = () => api.post('/auth/logout');
export const logoutEverywhere = () => api.post('/auth/logout-everywhere');

export const submitSource = async (slug, language, source) =>
  (await api.post(
    `/problems/${encodeURIComponent(slug)}/submissions`,
    { language, source }
  )).data;

export const submitFile = async (slug, language, file) => {
  const form = new FormData();
  form.append('language', language);
  form.append('file', file);
  // Do not manually set Content-Type: the browser/Axios must add the multipart boundary.
  return (await api.post(
    `/problems/${encodeURIComponent(slug)}/submissions`,
    form
  )).data;
};

export const register = async (payload) =>
  (await api.post('/auth/register', payload)).data;
```

When the user changes language, call `setApiLanguage('th' | 'en')` and refetch the data on screen, since titles and statements come back localized.

A single app-level authentication provider or equivalent can call `getMe()` on startup and share the result with the header and protected pages. Avoid making every component independently fetch the current user on the same render. Call `getMe()` again after sign-in return or when a page needs to refresh the authentication state.

### Handling API errors

Axios errors usually expose the server response under `error.response`. The API error body is intended to be:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Please type or upload your code.",
    "fields": {
      "source": ["Please type or upload your code."]
    }
  }
}
```

A shared error helper can read:

```js
const status = error.response?.status;
const code = error.response?.data?.error?.code;
const message = error.response?.data?.error?.message;
const fields = error.response?.data?.error?.fields;
```

Frontend behavior:

- `validation_error`: show the general message and field-specific messages when present.
- `not_authenticated` (`401`): refresh auth state or send the user to sign in if the action requires authentication.
- `csrf_failed` (`403`): refresh `getMe()` to obtain a CSRF cookie, then let the user retry. Do not create an infinite retry loop.
- `registration_closed`: show that registration is currently closed.
- `not_found` (`404`): show the normal not-found state; do not reveal whether hidden problems or another user's source exists.
- `payload_too_large` (`413`): tell the user the submission/request is too large.
- Submission-specific `429` codes: explain the applicable cooldown/queue/hourly limit and honor `Retry-After` when provided.
- `server_error` (`500`) or network failure: show a generic failure state and allow a deliberate retry where appropriate; do not display raw server tracebacks.

The backend is the authority on validation. Client-side validation is for fast feedback only.

---

## 5. Frontend routes and endpoint map

These are the intended frontend routes and API calls. Adapt route syntax to the router already installed in the Vite project; React Router is not mandated by this document.

| Frontend route | API calls | Notes |
|---|---|---|
| `/main` | `GET /api/problems`, `GET /api/site` | Main problem list and site summary |
| `/problems/:slug` | `GET /api/problems/{slug}`, `POST /api/problems/{slug}/submissions` | Problem statement, samples, submission form |
| `/submissions/:id` | `GET /api/submissions/{id}` every 2 seconds while not done | Live judging result page |
| `/submissions` | `GET /api/submissions` | Public submission list and filters |
| `/submissions/mine` (suggested route) | `GET /api/submissions/mine` | Signed-in user's submission list; route name is a frontend choice |
| `/archive` | `GET /api/groups` | Top-level archive |
| `/archive/:group` | `GET /api/groups/{slug}` | Group page; special group slug `other` means ungrouped problems |
| `/archive/:group/:sub` | `GET /api/groups/{slug}/{sub_slug}` | Subgroup page |
| `/ranking` | `GET /api/ranking` | Global practice ranking |
| `/users/:username` | `GET /api/users/{username}` | Public user profile |
| `/settings` | `GET /api/auth/me`, `PATCH /api/me`, `POST /api/auth/logout-everywhere` | Account settings |
| `/welcome` | `POST /api/auth/register` | Complete registration after first Google sign-in |
| `/signin-error` | No API call required to display error; reads `?code=` | Error code is provided by the sign-in redirect |
| Any page/header | `GET /api/auth/me`; link to `/api/auth/google/start`; `POST /api/auth/logout` | Prefer one shared auth state in the app |

The current prototype frontend has `/main` and `/about`. The remaining routes are part of the planned frontend. The exact route for the public submissions list versus the “mine” list is a frontend routing choice; the API endpoints are fixed as shown.

### Page state requirements

For data-driven pages, design these states explicitly:

1. **Loading:** show a stable loading state while the request is in progress.
2. **Success with data:** render the returned data.
3. **Empty success:** show a useful empty state; do not treat an empty list as an API error.
4. **Request failure:** show a recoverable error state where retry makes sense.
5. **Not found:** show a not-found state for API `404` responses.
6. **Authentication required:** for protected actions/pages, explain that the user must sign in.

These are frontend responsibilities; they do not require extra API endpoints.

---

## 6. Sign-in and account

Google sign-in is a **browser redirect**, not an Axios call. The server owns the OAuth flow and session cookie.

### Sign-in flow

1. Sign-in control navigates to `/api/auth/google/start?next=/main` (or another approved same-site path). Do not call this URL with Axios.
2. Django redirects the browser to Google and handles the callback at `/api/auth/google/callback`.
3. A known Google account receives a session and is redirected to `next`.
4. A new Google account is redirected to `/welcome`. Its pending identity is held in the session for 10 minutes; that page submits `POST /api/auth/register`.
5. If sign-in fails, Django redirects to `/signin-error?code=<code>`.

The `next` value must be a path on the site. The backend must validate it to prevent open redirects.

### Sign-in error codes

| Code | Meaning | Suggested UI message |
|---|---|---|
| `failed` | Sign-in failed | Sign-in could not be completed. Try again. |
| `registration_closed` | New registrations are disabled | Registration is currently closed. |
| `account_disabled` | Account is disabled | This account cannot sign in. |
| `unavailable` | Google sign-in is not configured/available | Sign-in is temporarily unavailable. |
| `rate_limited` | Sign-in attempts are rate-limited | Too many attempts. Please try again later. |

The suggested text is UI guidance, not a change to the server error codes.

### Authentication endpoints

| Method and endpoint | Purpose | Success |
|---|---|---|
| `GET /api/auth/me` | Current user; always returns `200`; also sets CSRF cookie | `{ "user": null }` or `{ "user": { ... } }` |
| `POST /api/auth/register` | Complete first-time registration | `201` and `{ "user": { ... } }` |
| `POST /api/auth/logout` | End this browser's session | `204 No Content` |
| `POST /api/auth/logout-everywhere` | End all sessions for this user | `204 No Content` |
| `PATCH /api/me` | Update own settings | Updated user object |

### Registration request

```json
{
  "username": "kit_01",
  "display_name": "Kit",
  "accept_privacy": true
}
```

Rules:

- `username`: 3–20 characters, lowercase `a-z`, digits `0-9`, and `_` only.
- `display_name`: required, at most 100 characters, no control characters, and cannot be blank (the prototype's first-sign-in form requires it).
- `accept_privacy`: required, and must be `true`.

Registration can return `400 validation_error` (including `fields.username`), `400 no_pending_signin`, or `403 registration_closed`.

### User object

Returned by `GET /api/auth/me`, `POST /api/auth/register`, and `PATCH /api/me`:

```json
{
  "username": "kit_01",
  "display_name": "Kit",
  "show_photo": true,
  "photo_url": "/api/users/kit_01/photo?v=1761456000"
}
```

`photo_url` is `null` when the user has no stored photo or has disabled photo display.

Staff do not sign in through these endpoints; staff use the separate Django admin role.

---

## 7. Site information and problem list

Problem browsing is public. Signing in adds the current user's progress. Hidden problems and problems without a published version behave as if they do not exist: they are omitted from lists and return `404` when requested directly.

### Site summary

`GET /api/site`

```json
{
  "announcement": "Contest on Saturday",
  "problem_count": 42,
  "registration_open": true,
  "grading_paused": false,
  "recent_problems": [
    { "slug": "aplusb", "title": "A plus B" }
  ]
}
```

`recent_problems` contains the five newest problems. `grading_paused` is true while an admin has paused grading or a scheduled pause window is active. Submissions may still be queued during a pause; judging waits until the pause ends.

### Problem list

`GET /api/problems`

Optional query parameters:

| Parameter | Meaning |
|---|---|
| `q` | Search, up to 100 characters. Matches slug and both localized titles; numeric search text can also match problem ID |
| `group` | Group ID; includes problems in that group's subgroups |
| `status` | `solved`, `tried`, or `untried`; meaningful only while signed in and ignored while signed out |
| `sort` | `id` (default), `title`, or `passes`; prefix `-` for descending. Unknown values fall back to `id` |
| `page` | Page number; default is 1 |
| `lang` | `th` or `en`; default is `th` |

Example response:

```json
{
  "count": 42,
  "page": 1,
  "page_size": 50,
  "counts": {
    "all": 42,
    "solved": 5,
    "tried": 3,
    "untried": 34,
    "today": 1
  },
  "results": [
    {
      "id": 1,
      "slug": "aplusb",
      "title": "A plus B",
      "max_score": 100,
      "time_limit_ms": 1000,
      "memory_limit_mb": 256,
      "passes": 17,
      "my_best": 100,
      "status": "solved"
    }
  ]
}
```

Behavior:

- `count` is the number of rows after the list filters; `counts` covers all visible problems, not only the filtered set.
- `counts.today` counts problems solved since midnight in Asia/Bangkok.
- While signed out, `counts.all` is populated and the other count values are `null`.
- `passes` is the number of active users with full marks.
- `my_best` and `status` are included only when signed in. `my_best` can be `null`.
- `status` is `solved` when the best score equals `max_score`, `tried` when the user has attempted but not solved it, and `untried` otherwise.

### Problem detail

`GET /api/problems/{slug}?lang=th`

```json
{
  "id": 1,
  "slug": "aplusb",
  "title": "A plus B",
  "tags": ["math"],
  "max_score": 100,
  "time_limit_ms": 1000,
  "memory_limit_mb": 256,
  "languages": [
    { "code": "cpp17", "name": "C++17" },
    { "code": "python3", "name": "Python 3" }
  ],
  "subtasks": [
    { "name": "1", "points": 30 },
    { "name": "2", "points": 70 }
  ],
  "statement": {
    "language": "th",
    "html": "<p>Add two numbers.</p>",
    "pdfs": { "th": "/api/problems/aplusb/files/statement/th.pdf" }
  },
  "samples": [
    { "name": "1-01", "input": "1 2\n", "output": "3\n", "cut_short": false }
  ],
  "attachments": [
    { "name": "attachments/template.cpp", "url": "/api/problems/aplusb/files/attachments/template.cpp" }
  ]
}
```

Field notes:

- `subtasks` is empty for problems using plain `sum` scoring.
- `statement.language` is the actual language of the returned statement, which may differ from the requested `lang` when only the other language exists.
- `statement.html` is empty when the statement is available only as PDFs.
- The backend renders the statement from Markdown with raw HTML disabled, then sanitizes it with an allow-list. The frontend may render this returned HTML using `dangerouslySetInnerHTML` **only because the backend contract promises sanitized HTML**. Do not render arbitrary user-provided HTML this way.
- Each sample is limited to 16 KB; `cut_short` indicates truncation.
- `attachments` lists downloadable files under `attachments/` and grader headers marked downloadable. PDFs and samples are represented in their relevant response fields.

### Problem files

`GET /api/problems/{slug}/files/{name}`

Only allow-listed files are served: statement PDFs, files under `attachments/`, sample tests, and grader files marked downloadable. Tests, solutions, checkers, and other unapproved files return `404`. PDFs open in the browser; other files download.

Use the full `url` or PDF URL returned by the API as supplied. Do not construct filesystem paths on the client.

---

## 8. Problem archive

### Top-level archive

`GET /api/groups`

```json
{
  "folders": [
    { "id": 1, "slug": "toi", "name": "TOI", "total": 30, "solved": 4 },
    { "id": null, "slug": "other", "name": "Other", "total": 6, "solved": 0 }
  ]
}
```

- `total` includes problems in subgroups.
- `solved` is `null` when the visitor is signed out.
- Folders with no visible problems are omitted.
- `other` represents problems without a group and appears only when such problems exist.

### Group or subgroup detail

- `GET /api/groups/{slug}`
- `GET /api/groups/{slug}/{sub_slug}`
- `GET /api/groups/other` for ungrouped problems

```json
{
  "group": { "id": 3, "slug": "toi20", "name": "TOI20" },
  "crumbs": [ { "slug": "toi", "name": "TOI" } ],
  "folders": [],
  "problems": {
    "count": 12,
    "page": 1,
    "page_size": 50,
    "results": []
  }
}
```

- `folders` contains subgroups using the same shape as the top-level archive.
- `problems` contains only problems assigned directly to the current group. It supports the problem list's `status`, `sort`, `page`, and `lang` parameters, and uses the same problem-row shape as `GET /api/problems`.
- `problems` is `null` when there are no directly assigned problems.
- A group that does not exist at that location, or has no visible problems counting its subgroups, returns `404`.
- **Contract:** a top-level group whose visible problems are all in subgroups returns `200` with `folders` filled and `problems: null`.

---

## 9. Submissions and judging results

The browser creates a submission and then polls its detail endpoint. The worker claims the queued job through the internal web-admin API and writes judging results to the database. The frontend does not call the worker's internal API.

### Create a submission

`POST /api/problems/{slug}/submissions` requires a signed-in user.

**Option A — submit source text as JSON:**

```json
{
  "language": "cpp17",
  "source": "#include <iostream>\nint main() { }"
}
```

**Option B — upload a file as `multipart/form-data`:**

| Field | Value |
|---|---|
| `language` | Language code from `languages[].code` on the problem detail response |
| `file` | Selected source file |

If both `file` and `source` are present, the file wins (prototype behavior).

Validation and limits:

- Language must be allowed for that problem.
- Source cannot be blank and cannot contain a NUL character.
- Source is limited to 64 KB in UTF-8.
- The complete request body is limited to 256 KB; oversized requests return `413 payload_too_large` before body parsing.
- Rate limits are checked in this order: 10 seconds since the user's last submission (`cooldown`), at most 3 waiting submissions (`too_many_waiting`), and 120 submissions per hour (`hourly_limit`). Each returns `429`.
- Submitting while grading is paused is allowed; the submission remains `queued` until grading resumes.

Success is `201 Created`, with `Location: /api/submissions/123` and:

```json
{ "id": 123, "status": "queued" }
```

After a successful POST, navigate to the submission detail route using the returned `id`. Do not guess or locally allocate submission IDs.

### Submission statuses and verdicts

`status` values:

| Status | Meaning | Frontend behavior |
|---|---|---|
| `queued` | Waiting to be judged | Show queued/waiting state; continue polling |
| `judging` | Worker is processing it | Show current progress and any test results already returned; continue polling |
| `done` | Judging has finished | Stop polling and show the final result |

Verdict codes:

| Code | Meaning |
|---|---|
| `AC` | Accepted |
| `PA` | Partially correct |
| `WA` | Wrong answer |
| `TLE` | Time limit exceeded |
| `MLE` | Memory limit exceeded |
| `RE` | Runtime error |
| `OLE` | Output limit exceeded |
| `CE` | Compilation error |
| `JE` | Judge error |

Until `status` is `done`, `verdict` is `""` and `score` is `null`. Test results may appear incrementally while `judging`. `time_ms` is rounded to 10 ms and `memory_mb` to 0.1 MB to avoid exposing overly precise measurements from hidden tests.

### Submission detail response

`GET /api/submissions/{id}` returns `404` if the problem is not visible.

Completed example:

```json
{
  "id": 123,
  "status": "done",
  "problem": { "slug": "aplusb", "title": "A plus B" },
  "user": { "username": "kit_01", "display_name": "Kit" },
  "language": "cpp17",
  "created_at": "2026-10-26T10:02:11Z",
  "judged_at": "2026-10-26T10:02:14Z",
  "verdict": "PA",
  "score": 30,
  "max_score": 100,
  "max_time_ms": 480,
  "max_memory_mb": 12.4,
  "subtasks": [
    { "name": "1", "points": 30, "score": 30 },
    { "name": "2", "points": 70, "score": 0 }
  ],
  "tests": [
    { "name": "1-01", "verdict": "AC", "outcome": 1, "time_ms": 10, "memory_mb": 3.1, "message": "" },
    { "name": "2-01", "verdict": "WA", "outcome": 0, "time_ms": 20, "memory_mb": 3.2, "message": "" }
  ],
  "compile_log": null,
  "is_author": true
}
```

Field and privacy rules:

- `status` follows the three values above. Until `done`, `verdict` is `""` and `score` is `null`.
- `compile_log` is populated only for the author and only when the verdict is `CE`; it may quote source lines.
- `is_author` indicates whether the current viewer is the submission author and whether the frontend should offer the source-code link.
- Verdict, score, subtasks, and per-test results are visible to everyone for visible problems.
- Compiler output is visible only to the author and only for `CE`.
- Source code is visible only to the author.

**Contract: field values by state.** Every field is always present in the response; only its value changes.

| Field | `queued` | `judging` | `done` |
|---|---|---|---|
| `verdict` | `""` | `""` | verdict code |
| `score` | `null` | `null` | number |
| `judged_at` | `null` | `null` | timestamp |
| `max_time_ms`, `max_memory_mb` | `null` | `null` | number, or `null` when no test ran (for example `CE`) |
| `tests` | `[]` | grows as tests finish | all tests |
| `subtasks` | `[]` | `[]` | scores per subtask, or `[]` for problems without subtasks |
| `compile_log` | `null` | `null` | author-only text for `CE`, otherwise `null` |

`max_score` and `is_author` are always set. Most of this matches the prototype's model defaults. The frontend should render only data that is non-null and non-empty rather than assuming a field is filled.

### Polling implementation

Poll every 2 seconds while the response's `status` is not `done`. Stop when it becomes `done`, the user leaves the page, or the component unmounts. Pass an `AbortSignal` through to Axios and make the delay cancellable.

Example helper (adapt names to the existing API module):

```js
import { getSubmission } from './api';

function delay(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }

    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, ms);

    function onAbort() {
      clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    }

    signal.addEventListener('abort', onAbort, { once: true });
  });
}

export async function waitForResult(id, onUpdate, signal) {
  while (!signal.aborted) {
    const submission = await getSubmission(id, { signal });
    if (signal.aborted) return;

    onUpdate(submission);
    if (submission.status === 'done') return submission;

    await delay(2000, signal);
  }
}
```

The `getSubmission` API helper must pass the Axios config through to the underlying request. In the React effect, create an `AbortController` and abort it during cleanup. Treat `AbortError` as normal cancellation, not as a user-visible failure.

**Backend confirmation required:** the retry policy for temporary network errors during polling is not defined here. The frontend should not retry forever without limits or show an abort caused by page navigation as an error. Agree on a bounded retry/error-display behavior before launch.

### Submission lists

| Endpoint | Behavior |
|---|---|
| `GET /api/submissions` | Public list of submissions on visible problems, newest first, 50 per page. Filters: `problem` (slug), `user` (username), `verdict` (code) |
| `GET /api/submissions/mine` | Signed-in user's own submissions, newest first; returns `401` when signed out |
| `GET /api/submissions/{id}/source` | Returns source as `text/plain; charset=utf-8`; only the author can access it. Other viewers receive `404` |

A bad list filter value returns `400 validation_error` (prototype behavior differs from an older empty-list response; this API contract uses the `400` behavior).

Submission list row (does not contain source code or compiler output):

```json
{
  "id": 123,
  "problem": { "slug": "aplusb", "title": "A plus B" },
  "user": { "username": "kit_01", "display_name": "Kit" },
  "language": "cpp17",
  "status": "done",
  "verdict": "PA",
  "score": 30,
  "max_time_ms": 480,
  "max_memory_mb": 12.4,
  "created_at": "2026-10-26T10:02:11Z"
}
```

Do not show a source-code action unless `is_author` is true on the detail response. The source endpoint itself remains the authority and may still return `404`.

---

## 10. Ranking and user profiles

### Global ranking

`GET /api/ranking?page=N&lang=th`

Users are ranked by the sum of their best scores on visible problems. Ties are ordered by the time the total was first reached, then by username, so ranks are unique. Disabled users and users with zero totals are omitted. Page size is 100.

```json
{
  "count": 250,
  "page": 1,
  "page_size": 100,
  "results": [
    {
      "rank": 1,
      "username": "kit_01",
      "display_name": "Kit",
      "photo_url": null,
      "total": 640.5
    }
  ]
}
```

`rank` is the position in the entire ranking, not the position within the current page.

### Public user profile

`GET /api/users/{username}?lang=th`

An unknown or disabled user returns `404`.

```json
{
  "username": "kit_01",
  "display_name": "Kit",
  "photo_url": null,
  "title": { "key": "pupil", "name": "Pupil" },
  "standing": { "total": 640.5, "rank": 1 },
  "solved": [
    { "slug": "aplusb", "title": "A plus B", "best_score": 100, "reached_at": "2026-10-20T08:00:00Z" }
  ],
  "attempted": [
    { "slug": "sum2", "title": "Sum 2", "best_score": 30, "reached_at": "2026-10-21T09:30:00Z" }
  ],
  "folders": [
    { "id": 1, "slug": "toi", "name": "TOI", "total": 30, "solved": 4 }
  ],
  "is_self": false
}
```

- `title.key` is the highest title earned by problems solved: `newbie` (0), `pupil` (10), `specialist` (30), `expert` (75), and `master` (150). `title.name` follows `lang`.
- `standing.rank` is `null` if the user has no score.
- `solved` means full marks on the current problem version; `attempted` contains other attempted visible problems. Both are sorted by slug.
- The frontend can draw a solved-over-time chart from the `reached_at` dates.
- `folders` uses the archive folder shape and includes this user's solved counts.
- `is_self` says whether the profile belongs to the signed-in viewer.

### Photos

`GET /api/users/{username}/photo` returns a stored Google photo as JPEG or PNG. It returns `404` if the user has no photo, has disabled photos, or is disabled. Use `photo_url` exactly as supplied; its `?v=` query changes when the image changes. Do not assume a photo exists for every user.

### Settings

`PATCH /api/me`

```json
{ "display_name": "Kit K.", "show_photo": false }
```

Both fields are optional:

- `display_name` cannot be blank, is limited to 100 characters, and cannot contain control characters.
- Setting `show_photo` to `false` deletes the stored photo. Setting it back to `true` fetches a photo again at the next sign-in.
- The endpoint requires authentication. Signed-out requests return `401`; rate-limited requests return `429 rate_limited`.
- The `school` field exists in the user model but is intentionally not exposed in this API contract.

---

## 11. Pagination, filtering, validation, and limits

### Standard pagination

Lists use this shape:

```json
{
  "count": 42,
  "page": 1,
  "page_size": 50,
  "results": []
}
```

- Default page is `1`.
- Normal list page size is `50`; ranking page size is `100`.
- `page` must be a positive integer; anything else returns `400 validation_error`.
- A page beyond the end returns an empty `results` array with the correct `count` (the prototype returned the last page; this API deliberately does not).
- Some endpoints add fields such as `counts` or `folders`; retain those endpoint-specific fields.

### Validation and request limits

| Limit | Value | Purpose/configuration name |
|---|---:|---|
| Source code | 64 KB UTF-8 | `MAX_SOURCE_BYTES` |
| Whole submit request body | 256 KB | `MAX_SUBMIT_BODY_BYTES` |
| Gap between submissions | 10 seconds | `GRADER_SUBMIT_COOLDOWN_SECONDS` |
| Waiting submissions per user | 3 | `GRADER_SUBMIT_MAX_WAITING` |
| Submissions per hour | 120 | `GRADER_SUBMIT_PER_HOUR` |
| Username | 3–20 lowercase letters, digits, `_` | `USERNAME_RULE` |
| Display name | Up to 100 characters | User model `max_length` |
| Search text | Up to 100 characters | `MAX_SEARCH_LENGTH` |
| Sample text displayed | 16 KB per sample | `MAX_SAMPLE_BYTES` |
| Page size | 50 / ranking 100 | `TASKS_PER_PAGE` / `RANKING_PAGE_SIZE` |

Mirror useful rules in client-side validation to help the user, but do not rely on it for security or correctness; the backend must validate every request.

---

## 12. Error contract

All API errors are intended to use one shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Please type or upload your code.",
    "fields": {
      "source": ["Please type or upload your code."]
    }
  }
}
```

`fields` appears only for `validation_error`.

| HTTP status | Code | Meaning / expected frontend behavior |
|---:|---|---|
| `400` | `validation_error` | Invalid request body or query parameter; may include field errors |
| `400` | `no_pending_signin` | No pending Google identity to register |
| `401` | `not_authenticated` | Endpoint needs a signed-in user |
| `403` | `csrf_failed` | CSRF header is missing or wrong |
| `403` | `registration_closed` | New account registration is closed |
| `404` | `not_found` | Missing, hidden, or inaccessible resource; do not distinguish hidden resources in the UI |
| `413` | `payload_too_large` | Request body exceeds 256 KB |
| `429` | `cooldown` | User submitted too recently |
| `429` | `too_many_waiting` | User already has 3 waiting submissions |
| `429` | `hourly_limit` | User exceeded the hourly submission limit |
| `429` | `rate_limited` | Another rate limit, such as sign-in or settings |
| `500` | `server_error` | Unexpected server error; display a generic message |

Every `429` includes `Retry-After` in seconds when the server knows the wait time. The frontend should use this value where possible rather than inventing its own countdown.

**Contract requirement:** API exceptions, including permission and CSRF errors, should use the JSON error shape consistently. If the reverse proxy can generate errors outside Django, ensure its behavior is handled or documented separately.

---

## 13. Backend implementation notes

The API is a new layer over the existing prototype. These notes are primarily for the backend owner; the frontend should not call internal implementation details.

### Django changes

- Keep existing `accounts`, `problems`, `judging`, and `core` apps, including their models/migrations, store, importer, rate limits, admin, and middleware where appropriate.
- Keep `common/grader_common`, the judge worker, and the existing deployment layout unless an implementation review identifies a concrete reason to change them.
- Add Django REST Framework and a new `api` package mounted in the public URL configuration with `path("api/", include("api.urls"))`.
- Do not expose frontend API endpoints through the separate admin URL configuration.
- Replace public template/htmx views with JSON API views as the migration proceeds.
- Update `GOOGLE_REDIRECT_URI` to `PUBLIC_BASE_URL + "/api/auth/google/callback"` in settings, Google configuration, and the development fake OIDC configuration.
- Redirect sign-in failures and first-sign-in users to frontend routes rather than rendering the old message page.

Suggested DRF settings sketch:

```python
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "EXCEPTION_HANDLER": "api.errors.handler",
    "DEFAULT_PAGINATION_CLASS": "api.pagination.Page",
}
```

Endpoints requiring an authenticated user must use an authenticated permission class. Session authentication must enforce CSRF for unsafe methods. `api.errors.handler` and `api.pagination.Page` implement the documented error and pagination shapes.

### Prototype logic to reuse

| API area | Existing logic to inspect/reuse |
|---|---|
| `GET /api/site` | `core/views.home`, `SiteSetting`, pause windows |
| Problems and groups | `problems/views.problem_list`, `archive`, `archive_group`, `problems/tasks.py`, `problems/archive.py` |
| Problem detail and files | `problem_detail`, `problem_file`, `statements.py`, `files.py`, `store.py` |
| Create submission | `judging/views.submit`, `_limit_reached`, `SubmitForm` |
| Submission reads | `submission_detail`, `submission_source`, `all_submissions`, `my_submissions`, `SubmissionFilterForm` |
| Ranking and profiles | `judging/views.ranking`, `profile`, `judging/profile.py` |
| Authentication, user settings, photos | `accounts/views.py`, `google.py`, `avatars.py`, `forms.py` |

Preserve the existing fixed-query-count behavior for `tasks.rows()` and archive counts. Serializers should not introduce a query per result row.

### API schema and tests

Generate an OpenAPI schema from the DRF serializers with `drf-spectacular` and serve Swagger UI in development.

Until that schema exists, this document is the contract. Once it exists, the generated schema is the source of truth for exact request/response fields, and this document keeps the conventions, behavior, privacy rules, and decisions. Do not hand-maintain a second copy of every field list: if code and an example here disagree, fix the code or serializer, regenerate the schema, and update the example.

At minimum, add backend tests for:

- Endpoint response schemas and pagination.
- Signed-out versus signed-in responses.
- CSRF/auth requirements and logout behavior.
- Hidden problem and file allow-list behavior.
- Source-code privacy and compiler-output privacy.
- Submission validation, body-size limits, and rate limits.
- Concurrent submission requests and worker job claiming.
- Worker crash/abandoned-job behavior and stale result reports.
- Ranking and profile behavior after a problem version changes.

---

## 14. Decisions still required before launch

These points are not fully settled by the existing prototype or this contract. Resolve them explicitly rather than letting the frontend and backend make different assumptions.

| Decision | Recommended direction | Owner / status |
|---|---|---|
| Crash policy change | Product choice: an abandoned submission becomes `JE` without a retry. The prototype retries up to 3 times, so that limit must change. Keep the existing lease, heartbeat, and stale-write rejection | Backend — change required |
| Polling after temporary network errors | Agree a bounded retry policy (limited attempts, then a visible error with a manual retry) and do not show page-navigation aborts as errors | Frontend + backend — decision required |
| Problem version updates | Define whether previous scores remain eligible for ranking after a new published version. Also decide where `max_score` comes from for old submissions; recommended: store the max score on the submission when it is judged | Product + backend — policy required |
| Frontend design details | Page styling, component choices, and layout are owned by the frontend developer | Frontend — design freedom |

### Already implemented in the prototype (keep these)

These were listed as open in an earlier draft, but the prototype already handles them. Keep the behavior when building the API.

| Topic | What the prototype does |
|---|---|
| Worker authentication | The worker sends a bearer token. The token lives in a secrets directory readable only by web-admin and the worker. `/internal/*` exists only on the admin role, rejects any request carrying an edge zone header, and is reachable only over `judge_net` |
| Atomic job claiming | One transaction requeues expired leases and claims a job with `select_for_update(skip_locked=True)`, so two workers never get the same job |
| Lost jobs and stale writes | 60-second lease with a 10-second heartbeat. Progress, complete, and fail reports are accepted only from the current lease holder; anyone else gets `409` and the worker drops the job. Results from a lost lease are discarded, and a dead worker's job returns to the queue after its lease expires |

### Decisions already selected

- Same-origin production deployment through Caddy.
- Cookie-based Django sessions rather than tokens.
- API localization via `?lang=th|en`, default `th`.
- Server-returned sanitized statement HTML.
- Polling every 2 seconds while judging is in progress.
- Contest management handled by CMS, not this frontend.
- Multiple judge workers, isolated execution with `isolate`, and a private internal API path to web-admin.
- Worker failure policy: abandoned submissions become `JE` rather than being retried. Detection already exists (lease and heartbeat); only the retry limit changes.

---

## 15. Frontend/backend integration checklist

Use this checklist before calling the migration complete.

### Contract and development setup

- [ ] Frontend calls only relative `/api/...` endpoints.
- [ ] Vite proxies `/api` to the development edge, not directly to bare Django `runserver`.
- [ ] Google callback and trusted-origin development settings match the Vite origin.
- [ ] Frontend and backend agree on every unresolved item in Section 14.
- [ ] OpenAPI schema matches the implemented API responses.

### Authentication and permissions

- [ ] `GET /api/auth/me` establishes the initial auth state and CSRF cookie.
- [ ] Google sign-in is a browser navigation, not an Axios request.
- [ ] Registration, normal logout, and logout-everywhere work.
- [ ] CSRF failures and signed-out responses produce understandable UI.
- [ ] Source code and compile logs are shown only when authorized.

### Problems, archive, and profile pages

- [ ] Search, sorting, filters, localization, and pagination use the documented query parameters.
- [ ] The shared Axios client sends the selected `lang` on every `GET` request.
- [ ] Signed-out pages correctly handle nullable progress counts.
- [ ] Problem statements render only backend-sanitized HTML.
- [ ] PDF and attachment links use the API-provided URLs.
- [ ] Empty lists, hidden/missing problems, disabled users, and network errors have distinct appropriate UI states.

### Submissions and judging

- [ ] Text submissions send JSON; file submissions use `FormData`.
- [ ] UI performs helpful client-side validation but displays backend validation/rate-limit errors too.
- [ ] Successful submission navigates using the returned submission ID.
- [ ] Polling updates results while judging and stops at `done`.
- [ ] The submission detail renders every state from the field table in section 9 without errors.
- [ ] Polling is cancelled on navigation/unmount and does not treat cancellation as an error.
- [ ] User can distinguish queued, judging, and completed states, including `JE`.
- [ ] Frontend handles `Retry-After` where supplied.

### Production

- [ ] Caddy serves `dist/` and falls back to `index.html` for frontend routes.
- [ ] `/api/*` is proxied to the public web role.
- [ ] Production cookies and CSRF settings are secure under HTTPS.
- [ ] Content Security Policy behavior is understood for fonts, images, and problem statement content.
- [ ] Backend tests cover concurrent claims, limits, authorization, and worker failure handling.

---

## 16. Implementation principle

The goal is not to reproduce Codeforces feature-for-feature. The goal is to deliver a reliable practice platform around the working grader: React + Vite for the user interface, Django + DRF for the API, PostgreSQL for application and submission state, and the existing isolated judge infrastructure for execution. Keep the implementation straightforward, make the API contract explicit, and add complexity only when a real requirement justifies it.
