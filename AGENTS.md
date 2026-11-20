# AGENTS.md — `food_truck_app`

## 1. Project Role & Boundary
- **Peran:** UI Portal (Mahasiswa & Petugas), Auth/Session Gatekeeper, Master Data, dan **Cold Storage** (riwayat *war* tiket).
- **Yang BOLEH di repo ini:** DocType, Role/Permission bawaan Frappe, Whitelisted API proxy ke Durable Object, dan halaman web (`www/`).
- **Yang TIDAK BOLEH di repo ini:** Menangani *high-concurrency* rebutan kuota/antrean tiket langsung di MariaDB Frappe. Seluruh operasi `claim` dan `redeem` *real-time* wajib diteruskan ke `durable-object-service`.

## 2. Tech Stack & Project Structure
- **Stack:** Frappe Framework (`~=15.0.0`), Python `>=3.10`, Ruff (`indent-style = "tab"`, `quote-style = "double"`).
- **Struktur Inti (`food_truck_app/`):**
  - `food_truck_app/food_truck_app/doctype/` — DocType inti (`Food Truck Unit`, `Food Truck Session`, `Food Truck Ticket History`, `Food Truck Settings`).
  - `food_truck_app/api.py` — Whitelisted API proxy & sync ke Durable Object.
  - `food_truck_app/www/` — Halaman portal (`war-tiket`, `validasi-tiket`).
  - `food_truck_app/hooks.py` — Konfigurasi app & fixtures (Role/Permission).

## 3. Coding Conventions & Guardrails
- **Auth & Role:** Gunakan sistem `User` & `Role` bawaan Frappe (`Mahasiswa`, `Petugas Food Truck`). Validasi login wajib dilakukan di backend (`frappe.session.user`) sebelum memanggil Durable Object.
- **War Window & Reload:** Hanya izinkan klaim untuk `Food Truck Session` hari ini yang berstatus `Open`. Saat `/war-tiket` dimuat ulang, cek tiket aktif mahasiswa (`get_my_ticket` -> `GET /my-claim`) agar QR Code tetap tampil jika sudah pernah klaim.
- **Integrasi DO:** Selalu kirim header `X-Workspace-ID: <YYYY-MM-DD>` dan shared secret dari `Food Truck Settings`.
- **Idempotency Cold Storage:** `close_session_and_sync` wajib bersifat *idempotent* agar pemanggilan ulang tidak menduplikasi record di `Food Truck Ticket History`.
- **Aturan Bisnis:** Tiket `CLAIMED` hanya hangus (`EXPIRED`) saat petugas menutup sesi, bukan *auto-expire* di tengah sesi.

## 4. Local Dev & Verification
- Jalankan migrasi setelah mengubah DocType: `bench --site <site> migrate`
- Jalankan unit test: `bench --site <site> run-tests --app food_truck_app`

## 5. GitHub Issue & Workflow Rules
- **Repo:** `BektiHandoyo/food-truck-app` (Master Issue: `#1`).
- Sebelum mulai ngoding, cek detail & Acceptance Criteria sub-issue terkait dengan `gh issue view <nomor> --repo BektiHandoyo/food-truck-app`.
- **Wajib konfirmasi user** sebelum melakukan mutasi GitHub (`gh issue create/edit/close`, `git push`) atau menjalankan perintah destruktif.
