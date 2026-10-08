# Deploying MyLabVault on TrueNAS SCALE

These steps install MyLabVault as a custom app on **TrueNAS SCALE 24.10 (Electric Eel) or newer**, where apps run on Docker (written for 25.10 Goldeye). The app uses the published image `ghcr.io/zaydons/mylabvault:latest`, which is public, so no registry login is needed.

Check your version on the TrueNAS **Dashboard** (System Information card). TrueNAS CORE and SCALE 24.04 or older don't support installing apps from YAML.

> ⚠️ **MyLabVault has no login.** Anyone who can reach the port can read and change all stored health data. Keep it on your LAN (or behind a VPN such as Tailscale/WireGuard) and do not forward the port or put it behind a public reverse proxy.

## 1. Create a dataset for the data

1. Go to **Datasets**, select your pool, and click **Add Dataset**.
2. Name it, for example `apps/mylabvault` (create the `apps` parent first if you don't have one). The **Apps** dataset preset is fine.
3. Note the full path, for example `/mnt/tank/apps/mylabvault`.

This dataset holds everything the app stores: the SQLite database (`mylabvault.db`) and uploaded PDFs (`uploads/pdfs/`).

## 2. Install the app

1. Go to **Apps** → **Discover Apps**, open the **⋮** menu (top right), and choose **Install via YAML**.
2. Name the app `mylabvault`.
3. Paste the contents of [`compose.yaml`](compose.yaml) and replace `/mnt/POOL/apps/mylabvault` with your dataset path from step 1.
4. If port `8000` is already used on your server, change the **left** side of `"8000:8000"` (for example `"30080:8000"`).
5. Click **Save**. TrueNAS pulls the image and starts the container. It shows as **Running** once the health check passes, which takes up to about 40 seconds.

Open `http://<truenas-ip>:8000` in a browser. On first launch the app asks whose results these are; enter your name, or choose *Skip for now* to keep "Default Patient".

## Optional: AI parsing with Amazon Bedrock

MyLabVault can use Claude through Amazon Bedrock to read lab reports the built-in parser can't handle, such as scanned PDFs, unfamiliar layouts, or reports where it picks the wrong date. It's **off unless you configure it**. When it's off, nothing leaves your server.

When it's on:
- Uploads that the built-in parser can't read, or where it finds no tests, are retried with AI automatically.
- Each uploaded file gets a **Re-scan with AI** button on the import screen. Afterwards a comparison shows what the AI found that the built-in reader didn't (and vice versa), and you can switch back to the built-in results.
- Results parsed by AI are marked with an **AI** badge. You still review and confirm them before anything is saved.
- **Settings → Clean up** gets an **Ask AI to review** button that finds lab tests, panels, units and providers saved under different names. It sends only those names, not your results, and nothing is merged until you confirm.

The PDF, including your name, date of birth and results, is sent to Claude in Amazon Bedrock in your AWS account. Data handling is covered by Amazon Bedrock's data-protection terms, and Anthropic has no access to the Bedrock inference infrastructure.

### AWS setup

1. **Model access:** in the AWS console, open **Amazon Bedrock** in your region (for example `us-east-1`) and confirm **Claude Haiku 4.5** answers in the **Chat / Text playground**. If it asks for Anthropic use-case details, submit them first. If you switch to another model later, check that one too.
2. **IAM policy:** go to **IAM → Policies → Create policy**, open the JSON tab, and create a policy named `MyLabVaultBedrock`:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": "bedrock:InvokeModel",
       "Resource": "*"
     }]
   }
   ```
3. **IAM user:** create a user named `mylabvault` with no console access and attach the policy.
4. **Access key:** create an access key for the user (use case: *Application running outside AWS*). Save both values in a password manager; the secret is shown only once.
5. **Budget alert:** go to **Billing → Budgets** and create a monthly budget, for example $5, with an email alert. Normal use costs cents per report.

### Turn it on in TrueNAS

Go to **Apps → mylabvault → Edit**, add these environment variables (the commented-out block in [`compose.yaml`](compose.yaml)), and save:

| Variable | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | Access key ID from step 4 |
| `AWS_SECRET_ACCESS_KEY` | Secret access key from step 4 |
| `AWS_REGION` | Your Bedrock region, e.g. `us-east-1` |
| `MYLABVAULT_AI_MODEL` | Optional. Bedrock model or inference profile ID. Defaults to `us.anthropic.claude-haiku-4-5-20251001-v1:0` (Claude Haiku 4.5, US regions). For other models or regions, copy the ID from **Bedrock → Cross-region inference** |

AI parsing turns on when the two keys and the region are all set. Remove them to turn it off.

### Troubleshooting

The import screen shows the reason when an AI re-scan fails:

| Message | Fix |
|---|---|
| *AWS credentials were rejected* | Check the access key ID and secret. |
| *not allowed to use this model* | Grant model access in Bedrock, and attach the policy to the user. |
| *not available in this AWS region* / *not a valid Bedrock model* | Use a region where the model is offered, or set `MYLABVAULT_AI_MODEL` to an inference profile ID listed under **Bedrock → Cross-region inference**. |
| *Could not reach the AI service* | The app needs outbound internet access. |

## Updating

Every push to `main` publishes a new `:latest` image. The running version (for example `Version 2026.10.07.32 (5f86139)`) is shown at the bottom right of every page. TrueNAS always shows *Version 1.0.0* for custom apps, so use the app's footer instead. When a newer image has been published, an **Update available** badge appears next to it. The app checks GitHub every few hours; set `MYLABVAULT_UPDATE_CHECK: "false"` to turn the check off.

To update, pull the new image in a TrueNAS shell, then restart the app. **Update** in the Apps screen does not always pull a new `:latest`.

```bash
docker pull ghcr.io/zaydons/mylabvault:latest
```

Then go to **Apps → mylabvault** and click **Stop**, then **Start**. Your data in the dataset is kept.

To pin a specific build instead of `:latest`, use its version or commit tag, such as `ghcr.io/zaydons/mylabvault:2026.10.07.32` or `ghcr.io/zaydons/mylabvault:<commit-sha>`.

## Logs

Open **Apps → mylabvault → Logs** (the container log). Each line is one event: `request` lines for every page and API call, `audit` lines for every change (imports, merges, deletions, data import/export/reset), and errors with their cause. When the app shows an error ID, search the log for it. To see more detail, add `MYLABVAULT_LOG_LEVEL: "DEBUG"` to the app's environment; `MYLABVAULT_LOG_FORMAT: "json"` writes JSON lines for a log collector. Results, health values and names are never written to the log. See [Logs](../../README.md#logs) for the format.

## Backups

- Set up a **Periodic Snapshot Task** for the dataset (**Data Protection** → **Periodic Snapshot Tasks**). SQLite is a single file, so snapshots are a simple way to roll back.
- You can also use **Settings → Backup & restore** in the MyLabVault UI to download a portable backup (ZIP with PDFs, or JSON).

## Moving existing data

To move data from another install, stop the app, copy the old `data/` folder contents (`mylabvault.db` and `uploads/`) into the dataset, then start the app again. Or download a backup from **Settings → Backup & restore** on the old install and restore it on the new one.
