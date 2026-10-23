# Installing the grader on ZimaOS

This guide installs the grader as one ZimaOS app, using only the ZimaOS dashboard, the ZimaOS Files
app, the Cloudflare dashboard and the Google Cloud console. Two optional steps at the end (the
restore drill and pinning other apps' CPUs) need the ZimaOS terminal; they say why.

What you end up with:

- the **public site** at `https://<your public hostname>`, reached through a Cloudflare tunnel (no
  port is opened on your router);
- the **admin site** at `http://<the NAS's LAN address>:8081`, on your home network (and over
  Tailscale, if you use it);
- all data in `/DATA/AppData/grader` on the NAS.

## 0. Before you start

You need:

- ZimaOS on an x86-64 machine with at least 6 CPUs online and about 3 GB of free memory;
- a domain whose DNS is on Cloudflare (for example `example.org`, giving `grader.example.org`);
- a Google account (for the sign-in client, step 5);
- the grader's images published to GHCR, with each package set to **public** in the GitHub
  organisation's *Packages* settings (once, after the first release), so ZimaOS can download them
  without logging in.

**CPUs.** The app is set up for a machine whose CPUs 0–5 are online: CPU 3 runs the grader's
services and CPUs 4 and 5 run the judge's sandboxes, leaving CPUs 0–2 to other apps (for example a
CMS installation's workers). To see your CPUs: ZimaOS dashboard → *Settings* → *About* (or the
System page in the grader's admin site after installing, which also warns about shared cores).
To change the split, see [Changing which CPUs the grader uses](#changing-which-cpus-the-grader-uses).

**Memory.** Together the grader's containers are limited to about 2 GB, and the judge's sandboxes
to 1 GB more (`JUDGE_MEM_TOTAL`).

## 1. Make a Cloudflare tunnel for the grader

1. In the Cloudflare dashboard, open **Zero Trust** → **Networks** → **Tunnels** → **Create a
   tunnel**. Choose **Cloudflared**, name it `grader`, and save.
2. On the next page Cloudflare shows install commands containing a long **token** (the text after
   `--token`). Copy only the token; you will paste it into ZimaOS in step 2. Don't run the commands.
3. Still in the tunnel, open **Public Hostname** → **Add a public hostname**:
   - *Subdomain* and *Domain*: your public hostname (for example `grader` + `example.org`);
   - *Service*: type **HTTP**, URL **`edge:8080`**.
   Save.

The tunnel container (part of the app) connects out to Cloudflare; nothing is opened on your router,
and Cloudflare sends visitors to the grader's edge on port 8080, which is not reachable any other way.

## 2. Install the app

1. ZimaOS dashboard → **App Store** → **Custom Install** (the **+** icon) → **Import** (the
   Docker Compose tab) → paste the whole of
   [`deploy/zimaos/grader.compose.yml`](../deploy/zimaos/grader.compose.yml) → **Submit**.
2. Before pressing *Install*, change these values in the app's settings (they are placeholders):

   | Container | Setting | Change it to |
   |---|---|---|
   | `web` | `PUBLIC_BASE_URL` | `https://<your public hostname>` |
   | `web-admin` | `ADMIN_BASE_URL` | `http://<the NAS's LAN address>:8081` |
   | `web-admin` | `PUBLIC_BASE_URL` | `https://<your public hostname>` (the same as above) |
   | `tunnel` | `TUNNEL_TOKEN` | the token from step 1 |

   The LAN address is shown on the ZimaOS dashboard (for example `192.168.1.20`).
3. Press **Install**. The first start downloads about 1.5 GB of images and takes a few minutes.
   `secrets-init` and `db-init` run once and stop; that is expected (they create the passwords and
   keys, and the database account the site uses, and show *Exited*). The other containers should
   become *healthy*; the worker takes up to a minute.

## 3. Create the first admin account

While no admin account exists, the grader makes a one-time setup link, valid for 24 hours:

1. Open the ZimaOS **Files** app → `AppData` → `grader` → `uploads` → **`SETUP-LINK.txt`**. (The
   same link is in the logs of the `web-admin` container.)
2. Open that link in a browser on your home network. Choose a username and a password (at least 8
   characters), and press *Create account*.
3. You are sent to the admin login page: sign in. The link stops working, and the file disappears.

If the link expired, restart the app (a new link is made at each start while there is no admin).

## 4. The admin site over Tailscale (optional)

If Tailscale runs on the NAS (ZimaOS has a Tailscale app), you can open the admin site from anywhere
on your tailnet at `http://<the NAS's Tailscale address>:8081`. Tell the grader that address too, or
it will refuse the requests: in the app's settings, set `web-admin` → `GRADER_ADMIN_EXTRA_URLS` to
`http://<the NAS's Tailscale address>:8081`. Several addresses are separated by commas, for example
the Tailscale IP and the MagicDNS name: `http://100.x.y.z:8081,http://<name>.<tailnet>.ts.net:8081`.

## 4b. The admin site from anywhere, through Cloudflare (optional)

Instead of (or as well as) Tailscale, you can open the admin site at `https://admin.<your domain>`
through the same Cloudflare tunnel. Cloudflare Access asks who you are first (your email gets a
one-time code), and the grader checks Cloudflare's signed answer itself: if anything is missing or
wrong, the address only says *Not Found*. Your admin username and password are still needed after
that. Everything here is done in the Cloudflare dashboard and the ZimaOS app settings.

1. **Protect the address first.** Cloudflare dashboard → **Zero Trust** → **Access** →
   **Applications** → **Add an application** → **Self-hosted**.
   - *Application name*: `grader admin`. *Session duration*: for example 24 hours.
   - *Application domain*: subdomain `admin`, your domain (for example `example.org`).
   - Next, add a policy: *Action* **Allow**, *Include* → **Emails** → your email (and anyone else
     who should reach the admin). Save the application.
   - Open the application again → **Overview** and copy the **Application Audience (AUD) Tag** (a
     long hex string).
   - Note your **team domain**: Zero Trust → **Settings** → **Custom Pages** (or *General*) shows
     it as `<your team>.cloudflareaccess.com`.
2. **Point the address at the grader.** Zero Trust → **Networks** → **Tunnels** → `grader` →
   **Public Hostname** → **Add a public hostname**: subdomain `admin`, your domain, *Service* type
   **HTTP**, URL **`edge:8082`**. Save. (Do step 1 first, so the address is never open without
   Cloudflare's login; until step 3 the grader answers *Not Found* anyway.)
3. **Tell the grader.** ZimaOS → the grader app → settings:

   | Container | Setting | Value |
   |---|---|---|
   | `web-admin` | `GRADER_CF_ACCESS_TEAM_DOMAIN` | `<your team>.cloudflareaccess.com` (no `https://`) |
   | `web-admin` | `GRADER_CF_ACCESS_AUD` | the AUD tag from step 1 |
   | `web-admin` | `GRADER_ADMIN_EXTRA_URLS` | add `https://admin.<your domain>` (comma-separated with any Tailscale addresses) |
   | `edge` | `GRADER_CF_ACCESS_UPSTREAM` | `https://<your team>.cloudflareaccess.com` |

   Save; the app restarts.
4. **Check it.** In a private browser window open `https://admin.<your domain>/admin/`: Cloudflare's
   login page comes first, then the grader's admin login. Actions you take this way are in the
   audit log with your Cloudflare email.

To switch it off again, empty the three `GRADER_CF_ACCESS_…` settings (or delete the public
hostname in the tunnel).

## 5. Google sign-in

Students sign in with Google. The grader asks Google only for an account number (no email, name or
photo).

1. Open the admin site → **Sign-in**. Copy the **redirect URI** shown there
   (`https://<your public hostname>/auth/google/callback`).
2. In the [Google Cloud console](https://console.cloud.google.com/): create a project (or pick one),
   then **APIs & Services** → **OAuth consent screen**: user type *External*, an app name, your
   email as support contact, and no extra scopes. Publish the app (*In production*) so anyone can
   sign in.
3. **APIs & Services** → **Credentials** → **Create credentials** → **OAuth client ID** →
   *Web application*. Under *Authorized redirect URIs*, add the URI from step 1. Create.
4. Copy the **Client ID** and **Client secret** into the grader's **Sign-in** page and press *Save*.
   The secret is stored encrypted and never shown again (the page only says "set").
5. Press **Test sign-in config**. It should say that keys were received.

## 6. Check the judge

Admin site → **System**:

- the worker `grader-worker` is *online*;
- read the environment report's warnings (for example judge CPUs sharing a physical core);
- press **Run self-test**. After a minute it should say **all passed**: every hostile test program
  was stopped by the sandbox on this machine.

Then import problems (**Import**), let their reference solutions be checked, and **Publish** them.
**Calibrate** times the reference solutions on this machine and suggests a time scale for the
**Grading** page.

## Updating to a new version

Releases are tagged `vX.Y.Z`. Each release run on GitHub (Actions → Release → the run) ends with an
**Image digests** box: four lines like `ghcr.io/kit2code/grader-web:0.1.2@sha256:…`. In the ZimaOS
dashboard → the grader app → *Settings*, replace the image of every `ghcr.io/kit2code/grader-…`
service with the matching line: `grader-web` for `web` and `web-admin`, `grader-worker` for
`worker`, `grader-edge` for `edge`, `grader-ops` for `secrets-init`, `db-init` and `backup`. Save.
The app restarts; `web-admin` updates the database by itself. Your data, settings and secrets stay.

The digest (`@sha256:…`) names the exact images that release built and tested; a version tag alone
could be pushed again later.

## Backups

- Every night at 03:30 (`backup` → `BACKUP_TIME`) the database is saved into
  `/DATA/AppData/grader/backups`; the newest 14 are kept (`BACKUP_KEEP`).
- Admin site → **Backups** → **Back up now** makes one at once; the page lists the files, and a
  superuser can download them.
- Add `/DATA/AppData/grader` to the **ZimaOS Backup** app, with an external disk or cloud storage as
  the target, so the backups (and the problems and secrets) also exist outside the NAS.

A backup holds every account, session and password hash: keep copies as private as the NAS itself.
The Google client secret in it is encrypted with the app's own key; after restoring onto a fresh
install, paste the secret into the Sign-in page again.

### Restore drill (needs the terminal)

ZimaOS has no button for restoring a database, so this uses the ZimaOS terminal (Settings →
Terminal). Practise it once into a scratch database; it doesn't touch the real data. Replace
`<file>` with a name from the Backups page.

```sh
docker exec grader-db-1 createdb -U grader restore_check
docker exec grader-backup-1 sh -c 'PGPASSWORD=$(cat /run/secrets/backup/db_password) pg_restore -h db -U grader -d restore_check /backups/<file>'
docker exec grader-db-1 psql -U grader -d restore_check -c 'select count(*) from judging_submission'
docker exec grader-db-1 dropdb -U grader restore_check
```

A real restore is the same, into the real database, with the app's other services stopped:

```sh
docker stop grader-web-1 grader-web-admin-1 grader-worker-1
docker exec grader-db-1 dropdb -U grader grader
docker exec grader-db-1 createdb -U grader -O grader_app grader
docker exec grader-backup-1 sh -c 'PGPASSWORD=$(cat /run/secrets/backup/db_password) pg_restore -h db -U grader -d grader /backups/<file>'
docker start grader-web-admin-1 grader-web-1 grader-worker-1
```

(The `backup` container keeps running: the restore goes through it, because it has the database
password and the backups folder. `-O grader_app` gives the new database to the role the web site
logs in as; the app's `db-init` step also hands it every restored table at the next start.)

## Contest day

Admin site → **Grading** → **Add a pause window** from the contest's start until its final results
(include any rejudges). During the window the grader judges nothing, so the contest gets the CPUs
and memory; practice submissions wait in the queue and are judged afterwards. **Pause grading now**
does the same until you press *Resume*.

## Sharing the NAS with CMS and other apps

The grader keeps to CPUs 3–5. Other apps that are not pinned to CPUs can still run on 3–5 and make
the judge's timings noisy. For steady timings, pin them to the other CPUs (for example CMS to
`0-2`): in ZimaOS, open that app's *Settings* and set its CPU set, if your ZimaOS version shows the
field; otherwise this needs the terminal (`docker update --cpuset-cpus 0-2 <container>`), and is
lost when that app is recreated.

For CMS itself: keep its workers on their own cores (for example 0 and 2) and keep
`core_services.Worker` in `cms.conf` at 2 entries.

## Changing which CPUs the grader uses

In the app's settings: every container's **CPU set** (`cpuset`, `3` by default) and `worker` →
`JUDGE_BOX_CPUS` (`4,5`: one CPU per judging slot; `JUDGE_SLOTS` must not be larger than the number
of CPUs listed). Use CPUs that exist (a CPU set naming a missing CPU stops that container from
starting) and keep the judge's CPUs apart from the services' and from CMS's.

## Troubleshooting

- **Each container's log:** ZimaOS dashboard → the grader app → the container → *Logs*.
- **The public site shows a Cloudflare error.** Check the `tunnel` container is healthy and the
  tunnel's public hostname points to `HTTP` `edge:8080`.
- **`web` keeps saying "waiting for the database and migrations".** The reason is in brackets.
  "web-admin has not migrated yet": wait for `web-admin`. "Cannot read secret file … Permission
  denied": restart the app (so `secrets-init` fixes the owners).
- **"Bad Request (400)" on the admin site.** You opened it at an address the grader doesn't know:
  use `ADMIN_BASE_URL`, or add the address to `GRADER_ADMIN_EXTRA_URLS` (step 4).
- **Google says "redirect_uri_mismatch".** The redirect URI in the Google console must be exactly
  the one on the Sign-in page.
- **Submissions stay "waiting".** Admin site → System: is the worker online, is grading paused, is
  there a pause window now? Recent judge errors are listed there too.
- **Time limits feel too tight or too loose.** Admin site → Calibrate, then set the time scale on
  the Grading page.
- **The worker stays *unhealthy*.** Its log says why: "the judge loop has not started" (wait a
  minute after a start), "every judge slot is broken" (restart the app), or a start-up error about
  cgroups or CPUs (check `JUDGE_BOX_CPUS` and the CPU sets name CPUs that exist).
- **`web` or `web-admin` say "password authentication failed for user grader_app".** `db-init` sets
  that password at every start: restart the app, and check `db-init` ends with *Exited (0)* and
  its log says "role grader_app created" or "updated".
- **The edge stops at once with "must be an https:// address".** A Google upstream setting on
  `edge` was changed to something that isn't `https://`; remove it (the defaults are Google's own).
  For `GRADER_CF_ACCESS_UPSTREAM`, write it with `https://` in front (step 4b).
- **`https://admin.<your domain>` says *Not Found* after Cloudflare's login.** The grader didn't
  accept Cloudflare's answer: check the three `GRADER_CF_ACCESS_…` settings (team domain without
  `https://` on `web-admin`, with it on `edge`; the AUD tag exactly as shown). The `web-admin` log
  says "Cloudflare Access keys: …" if it can't fetch the keys. *Bad Request (400)* instead: add the
  address to `GRADER_ADMIN_EXTRA_URLS`.

## First-install checklist

Tick each line as you go; the step numbers are the sections above. Keep the screenshots of the last
two lines: they show the install works on this machine.

- [ ] **0.** The four `ghcr.io/kit2code/grader-…` packages are *Public*; the NAS has CPUs 0–5 online
  and about 3 GB of memory free; other apps (CMS) are stopped or pinned to CPUs 0–2.
- [ ] **1.** The Cloudflare tunnel `grader` exists, its token is copied, and its public hostname
  points to `HTTP` `edge:8080`. The tunnel shows *Inactive* until the app starts; that's fine.
- [ ] **2.** The compose file is imported, the four placeholders are replaced, and *Install*
  finished. In the app's containers: `secrets-init` and `db-init` *Exited*; `db`, `web`,
  `web-admin`, `worker`, `edge`, `tunnel` and `backup` *healthy* (allow a few minutes). The
  Cloudflare tunnel now shows *Healthy*.
- [ ] **3.** `SETUP-LINK.txt` opened, the admin account made, and you can sign in to
  `http://<the NAS's LAN address>:8081/admin/`.
- [ ] **4.** (Optional) the admin site opens over Tailscale too.
- [ ] **4b.** (Optional) `https://admin.<your domain>/admin/` shows Cloudflare's login first, then
  the grader's; in a window without the Cloudflare login it never shows the grader's page.
- [ ] **5.** The Google client is saved on the Sign-in page and *Test sign-in config* says keys were
  received. `https://<your public hostname>` opens the public site and *Sign in with Google*
  works.
- [ ] **6.** Admin site → System: the worker is *online*; the environment report has no warnings
  you can't explain (save a screenshot); *Run self-test* says **all passed** (save a screenshot).
- [ ] **6.** Import a small problem: zip the folder `fixtures/problems/aplusb` of this repository
  (on Windows: right-click → *Send to* → *Compressed (zipped) folder*) and upload it on the
  **Import** page; publish it. Sign in on the public site and submit
  `fixtures/submissions/aplusb/ac.cpp`: it is judged **AC**.

