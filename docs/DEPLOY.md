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
   `secrets-init` runs once and stops; that is expected (it creates the passwords and keys, and
   shows *Exited*). The other containers should become *healthy*.

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

Releases are tagged `vX.Y.Z`. In the ZimaOS dashboard → the grader app → *Settings*, change the
image tag `0.1.0` to the new version for every `ghcr.io/kit2code/grader-…` image (`web`,
`web-admin`, `worker`, `edge`, `secrets-init`, `backup`) and save. The app restarts; `web-admin`
updates the database by itself. Your data, settings and secrets stay.

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
docker exec grader-db-1 createdb -U grader grader
docker exec grader-backup-1 sh -c 'PGPASSWORD=$(cat /run/secrets/backup/db_password) pg_restore -h db -U grader -d grader /backups/<file>'
docker start grader-web-admin-1 grader-web-1 grader-worker-1
```

(The `backup` container keeps running: the restore goes through it, because it has the database
password and the backups folder.)

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
