# Interim ticket sink — Google Sheet setup

Ten minutes, no deployment, no database. This is the thing that makes tickets **exist**, which
is what has been missing: until a ticket is real, acknowledging the person who raised it is a
promise the system cannot keep.

It is deliberately temporary. The swap to a real backend later is one class in `sinks.py` —
nothing upstream of it changes.

---

## 1. Create the sheet

1. Go to <https://sheets.new>. Name it something like `Valmo intake — tickets`.
2. **Extensions → Apps Script**. A script editor opens, bound to this sheet.
3. Delete the `function myFunction() {}` stub.
4. Paste the entire contents of [Code.gs](Code.gs).
5. Save (⌘S). Name the project `valmo-intake-sink`.

You do not need to create the `tickets` tab or type the headers — the script creates them on the
first post and freezes the header row.

## 2. Deploy it as a Web App

1. **Deploy → New deployment**.
2. Click the gear next to "Select type" → **Web app**.
3. Fill in:

   | Field | Value |
   |---|---|
   | Description | `intake sink v1` |
   | Execute as | **Me** (`pranav.akella@meesho.com`) |
   | Who has access | **Anyone within Meesho** |

   **"Who has access" is the one that matters.** This URL is a write endpoint — anyone holding
   it can append rows. `Anyone within Meesho` requires a Meesho Google login. Do not pick
   `Anyone`, which makes it writable by the open internet.

4. **Deploy**. Google will ask you to authorise the script the first time — it wants permission
   to edit this spreadsheet, which is exactly what it does.
5. Copy the **Web app URL**. It looks like
   `https://script.google.com/a/macros/meesho.com/s/AKfy…/exec`.

## 2b. Set the shared secret

The deployment URL has to be **shareable** — the status page a partner opens lives at the same
address. So the URL cannot also be the thing that authorises writes.

1. In the Apps Script editor: **Project Settings → Script Properties → Add script property**
2. Name `INTAKE_SECRET`, value a long random string. Generate one:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

3. Save the same value locally:

```bash
cd ~/PSP/backend
printf '%s' 'THE_SAME_STRING' > data/appscript_secret.txt
chmod 600 data/appscript_secret.txt
```

Without this, every POST is refused with `server_not_configured`.

## 3. Give the pipeline the URL

The URL is a credential. It goes in a file, at mode 600, never in a commit or a chat:

```bash
cd ~/PSP/backend
printf '%s' 'PASTE_THE_URL_HERE' > data/appscript_url.txt
chmod 600 data/appscript_url.txt
```

`backend/data/*.txt` is already gitignored, so this cannot be committed by accident.

## 4. Check it is live before sending anything

```bash
curl -sL "$(cat ~/PSP/backend/data/appscript_url.txt)"
# {"ok":true,"sheet":"tickets","rows":0}
```

`doGet` writes nothing, so this is safe to run any time. `-L` matters: Apps Script redirects to
`googleusercontent.com` to serve the response.

If you get HTML back instead of JSON, the deployment is set to require a login the `curl` does
not have — re-check step 2's access setting.

## 5. Run the pipeline into it

```bash
cd ~/PSP/backend
.venv/bin/python -m app.intake.cli run-all --run-id today --sink sheet
```

`--sink` defaults to `dry`, which creates nothing. Naming a real sink is an explicit act and
prints a warning to stderr before it posts.

To test the wiring without touching Google at all:

```bash
.venv/bin/python -m app.intake.cli run-all --run-id today --sink file
```

---

## The status page — what a partner actually opens

Every created ticket gets an unguessable random token, returned by the sheet and stored by the
sink. The acknowledgement carries a link built from it:

```
https://script.google.com/a/macros/meesho.com/s/AKfy…/exec?t=9f3c1a…
```

That page shows **one** ticket: reference, title, state (Received / Being worked on / Resolved),
who raised it, when, how many times it has been raised, DC, and category. It lists nothing else
and links nowhere else.

Two things make it safe to send outside the team:

- **A row number would be enumerable.** `?ref=VAL-48` would read somebody else's ticket. A
  random token cannot be incremented into a neighbour's.
- **The same URL accepts POSTs**, so without the shared secret above, handing a partner the
  status link would hand them the ability to create tickets.

To move a ticket along, edit the `state` column in the sheet — `open`, `in_progress`,
`resolved` — and optionally write a line in `status_note`. The page reflects it on next load.
That is the whole workflow until a real backend exists.

## The guarantee, and how to see it for yourself

Run the same command twice. The second run creates **zero** tickets and the sheet does not grow:

```
=== RUN 1 ===  Sink=sheet: 20 ticket(s) CREATED.
=== RUN 2 ===  Sink=sheet: 0 ticket(s) CREATED.   (20 already existed)
```

This holds because the key is derived from the **source message** —
`sha256(source_system, channel_id, message_id)` — not from the run. The same Slack message
produces the same key on every run, from every machine, forever. `Code.gs` looks it up before
appending and returns the original `VAL-<row>` reference if it is already there.

A sink that trusts the caller not to retry is not idempotent, it is lucky. Retries are normal:
timeouts, re-runs, two people running the pipeline. The contract is that doing so is harmless.

## What each column means

| Column | Notes |
|---|---|
| `idempotency_key` | The key above. Column A because the lookup scans only this column. |
| `source_id` | `channel_id/message_id` — pairs with `permalink` to get back to the message. |
| `disposition` | From the BM25 matcher, or `NOVEL` when nothing scored above the floor. |
| `occurrence_count` | **Recurrence, not dedupe.** The same issue raised seven times is one row with a 7, not seven rows and not six suppressed. |
| `first_raised_at` / `last_raised_at` | The span that count covers. |
| `flags` | Failed validation checks, `; `-separated. A ticket missing a required field says so rather than looking complete. |
| `entities` | JSON. Typed identifiers with `valid` / `in_registry` per value. |

## When to leave this behind

Two limits, both fine at interim volume and neither fine at scale:

- **`findKey_` reads all of column A on every post.** Linear per request. At a few thousand rows
  it is imperceptible; at a hundred thousand it is not.
- **Apps Script quotas.** Roughly 20,000 URL-fetch-free executions/day on Workspace, and a
  6-minute execution ceiling.

The stated target is 100k–1M messages/month across surfaces. This sink does not go there — but
the *interface* does. Replacing it means writing one class with a `create(draft) -> ref` method
and pointing `--sink` at it. Nothing else in the pipeline knows the difference.
