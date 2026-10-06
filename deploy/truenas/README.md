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

Open `http://<truenas-ip>:8000` in a browser.

## Updating

Every push to `main` publishes a new `:latest` image. To update, open the app in **Apps** and use **Update** (or **Edit** → **Save** to redeploy), which pulls the newest image. Your data in the dataset is kept.

To pin a specific build instead of `:latest`, use a commit tag such as `ghcr.io/zaydons/mylabvault:<commit-sha>`.

## Backups

- Set up a **Periodic Snapshot Task** for the dataset (**Data Protection** → **Periodic Snapshot Tasks**). SQLite is a single file, so snapshots are a simple way to roll back.
- You can also use **Settings** → export in the MyLabVault UI for a portable JSON export.

## Moving existing data

To move data from another install, stop the app, copy the old `data/` folder contents (`mylabvault.db` and `uploads/`) into the dataset, then start the app again. Or use the export/import feature on the MyLabVault **Settings** page.
