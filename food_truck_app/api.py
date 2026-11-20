# Copyright (c) 2026, BektiHandoyo and contributors
# For license information, please see license.txt

from datetime import datetime
import re

import frappe
import requests
from frappe.utils import now_datetime, today

from food_truck_app.auth import (
	ROLE_MAHASISWA,
	ROLE_PETUGAS,
	get_student_nim,
	require_login,
	require_role,
)

DO_TIMEOUT_SECONDS = 10
UUID_REGEX = re.compile(
	r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _ms_to_datetime_str(ms_val: int | float | None) -> str | None:
	if not ms_val:
		return None
	try:
		dt = datetime.fromtimestamp(float(ms_val) / 1000.0)
		return dt.strftime("%Y-%m-%d %H:%M:%S")
	except (ValueError, TypeError, OSError):
		return None


def _is_uuid(val: str) -> bool:
	return bool(UUID_REGEX.match((val or "").strip()))


def _get_do_settings() -> tuple[str, str]:
	settings = frappe.get_single("Food Truck Settings")
	base_url = (settings.do_base_url or "").strip().rstrip("/")
	secret = (settings.get_password("do_api_secret", raise_exception=False) or "").strip()

	if not base_url or not secret:
		frappe.throw(
			"Konfigurasi Durable Object (do_base_url / do_api_secret) belum lengkap di Food Truck Settings."
		)
	return base_url, secret


def _request_do(
	method: str,
	path: str,
	workspace_date: str,
	json_body: dict | list | None = None,
	params: dict | None = None,
) -> tuple[dict, int]:
	base_url, secret = _get_do_settings()
	url = f"{base_url}/{path.lstrip('/')}"
	headers = {
		"X-Workspace-ID": str(workspace_date),
		"X-API-Secret": secret,
		"Authorization": f"Bearer {secret}",
		"Content-Type": "application/json",
	}

	try:
		resp = requests.request(
			method=method.upper(),
			url=url,
			headers=headers,
			json=json_body,
			params=params,
			timeout=DO_TIMEOUT_SECONDS,
		)
	except requests.RequestException as exc:
		frappe.throw(f"Gagal menghubungi Durable Object service: {exc}")

	try:
		data = resp.json()
	except ValueError:
		data = {"ok": False, "message": resp.text or f"HTTP {resp.status_code}"}

	return data, resp.status_code


def _get_open_sessions(target_date: str) -> list[dict]:
	sessions = frappe.get_all(
		"Food Truck Session",
		filters={"session_date": target_date, "status": "Open"},
		fields=[
			"name",
			"session_date",
			"food_truck_unit",
			"slot_time",
			"slot_id",
			"quota",
			"status",
			"synced_to_do",
		],
		order_by="slot_time asc, food_truck_unit asc",
	)
	if not sessions:
		return []

	unit_codes = list({s.food_truck_unit for s in sessions if s.food_truck_unit})
	units = {
		u.name: u
		for u in frappe.get_all(
			"Food Truck Unit",
			filters={"name": ["in", unit_codes]},
			fields=["name", "unit_name", "location", "is_active"],
		)
	}

	active_sessions = []
	for s in sessions:
		unit = units.get(s.food_truck_unit)
		if unit and not unit.is_active:
			continue
		s["unit_name"] = unit.unit_name if unit else s.food_truck_unit
		s["location"] = (unit.location or "") if unit else ""
		active_sessions.append(s)
	return active_sessions


def _sync_open_sessions_internal(target_date: str, sessions: list[dict] | None = None) -> dict:
	open_sessions = sessions if sessions is not None else _get_open_sessions(target_date)
	if not open_sessions:
		return {"ok": True, "date": target_date, "synced": 0, "slots": []}

	slots_payload = [
		{
			"id": s["slot_id"],
			"slot_time": str(s["slot_time"]),
			"quota": int(s["quota"]),
		}
		for s in open_sessions
	]

	do_data, status_code = _request_do(
		"POST",
		"/slots/init",
		workspace_date=target_date,
		json_body=slots_payload,
	)
	if not do_data.get("ok"):
		frappe.throw(do_data.get("message") or f"Gagal sync slot ke DO (HTTP {status_code})")

	for s in open_sessions:
		if not s.get("synced_to_do"):
			frappe.db.set_value(
				"Food Truck Session",
				s["name"],
				"synced_to_do",
				1,
				update_modified=False,
			)

	return {
		"ok": True,
		"date": target_date,
		"synced": len(slots_payload),
		"slots": slots_payload,
	}


@frappe.whitelist()
def sync_slots_to_do(date: str | None = None) -> dict:
	"""Kirim daftar slot Food Truck Session berstatus Open pada tanggal terkait ke POST /slots/init DO."""
	require_role(ROLE_PETUGAS)
	target_date = str(date or today())
	return _sync_open_sessions_internal(target_date)


@frappe.whitelist()
def get_available_slots(date: str | None = None) -> dict:
	"""Kembalikan daftar sesi Open beserta sisa kuota live dari GET /slots DO."""
	require_login()
	target_date = str(date or today())
	open_sessions = _get_open_sessions(target_date)
	if not open_sessions:
		return {"ok": True, "date": target_date, "slots": []}

	if any(not s.get("synced_to_do") for s in open_sessions):
		_sync_open_sessions_internal(target_date, open_sessions)

	do_data, status_code = _request_do("GET", "/slots", workspace_date=target_date)
	if not do_data.get("ok"):
		frappe.throw(do_data.get("message") or f"Gagal mengambil slot dari DO (HTTP {status_code})")

	do_slots_by_id = {item["id"]: item for item in (do_data.get("slots") or [])}

	# Jika instance DO hari ini belum memiliki slot yang sudah terdaftar di Frappe, seed otomatis
	if any(s["slot_id"] not in do_slots_by_id for s in open_sessions):
		_sync_open_sessions_internal(target_date, open_sessions)
		do_data, _ = _request_do("GET", "/slots", workspace_date=target_date)
		do_slots_by_id = {item["id"]: item for item in (do_data.get("slots") or [])}

	merged_slots = []
	for s in open_sessions:
		live = do_slots_by_id.get(s["slot_id"], {})
		quota = int(live.get("quota") or s["quota"])
		claimed = int(live.get("claimed") or 0)
		remaining = int(live.get("remaining", max(0, quota - claimed)))
		merged_slots.append(
			{
				"session_name": s["name"],
				"session_date": str(s["session_date"]),
				"food_truck_unit": s["food_truck_unit"],
				"unit_name": s["unit_name"],
				"location": s["location"],
				"slot_id": s["slot_id"],
				"slot_time": s["slot_time"],
				"quota": quota,
				"claimed": claimed,
				"remaining": remaining,
				"status": s["status"],
			}
		)

	return {"ok": True, "date": target_date, "slots": merged_slots}


@frappe.whitelist()
def claim_ticket(slot_id: str, request_id: str | None = None) -> dict:
	"""Validasi sesi login mahasiswa & War Window, lalu teruskan klaim ke POST /claim DO."""
	require_role(ROLE_MAHASISWA)
	slot_id = (slot_id or "").strip()
	if not slot_id:
		frappe.throw("slot_id wajib diisi.")

	today_str = str(today())
	session_row = frappe.db.get_value(
		"Food Truck Session",
		{"slot_id": slot_id, "session_date": today_str},
		["name", "food_truck_unit", "slot_time", "slot_id", "quota", "status", "synced_to_do"],
		as_dict=True,
	)

	if not session_row or session_row.status != "Open":
		frappe.throw(
			"Sesi war untuk slot ini belum dibuka atau sudah ditutup.",
			frappe.ValidationError,
		)

	if not session_row.synced_to_do:
		_sync_open_sessions_internal(today_str)

	user_id = get_student_nim()
	req_id = (request_id or "").strip() or f"{today_str}:{user_id}:{slot_id}"

	do_data, status_code = _request_do(
		"POST",
		"/claim",
		workspace_date=today_str,
		json_body={
			"user_id": user_id,
			"slot_id": slot_id,
			"request_id": req_id,
		},
	)

	unit_info = frappe.db.get_value(
		"Food Truck Unit",
		session_row.food_truck_unit,
		["unit_name", "location"],
		as_dict=True,
	)

	return {
		**do_data,
		"http_status": status_code,
		"user_id": user_id,
		"session_name": session_row.name,
		"food_truck_unit": session_row.food_truck_unit,
		"unit_name": unit_info.unit_name if unit_info else session_row.food_truck_unit,
		"location": (unit_info.location or "") if unit_info else "",
		"slot_time": session_row.slot_time,
	}


@frappe.whitelist()
def get_my_ticket(date: str | None = None) -> dict:
	"""Cek tiket aktif mahasiswa pada tanggal terkait via GET /my-claim DO (untuk reload /war-tiket)."""
	require_role(ROLE_MAHASISWA)
	target_date = str(date or today())
	user_id = get_student_nim()

	do_data, status_code = _request_do(
		"GET",
		"/my-claim",
		workspace_date=target_date,
		params={"user_id": user_id},
	)
	if not do_data.get("ok"):
		frappe.throw(do_data.get("message") or f"Gagal mengecek tiket aktif (HTTP {status_code})")

	claim = do_data.get("claim")
	if claim and claim.get("slotId"):
		session_row = frappe.db.get_value(
			"Food Truck Session",
			{"slot_id": claim["slotId"], "session_date": target_date},
			["name", "food_truck_unit", "slot_time"],
			as_dict=True,
		)
		if session_row:
			unit_info = frappe.db.get_value(
				"Food Truck Unit",
				session_row.food_truck_unit,
				["unit_name", "location"],
				as_dict=True,
			)
			claim["sessionName"] = session_row.name
			claim["foodTruckUnit"] = session_row.food_truck_unit
			claim["unitName"] = unit_info.unit_name if unit_info else session_row.food_truck_unit
			claim["location"] = (unit_info.location or "") if unit_info else ""
			claim["slotTime"] = session_row.slot_time

	return {
		"ok": True,
		"date": target_date,
		"user_id": user_id,
		"claim": claim,
	}


@frappe.whitelist()
def redeem_ticket(
	identifier: str | None = None,
	claim_id: str | None = None,
	user_id: str | None = None,
	date: str | None = None,
) -> dict:
	"""Validasi role Petugas Food Truck, lalu teruskan redeem via claim_id (QR) atau user_id (NIM) ke POST /redeem DO."""
	require_role(ROLE_PETUGAS)
	target_date = str(date or today())

	cid = (claim_id or "").strip()
	uid = (user_id or "").strip()
	ident = (identifier or "").strip()

	if not cid and not uid:
		if not ident:
			frappe.throw("Masukkan Claim ID (QR) atau NIM Mahasiswa.")
		if _is_uuid(ident):
			cid = ident
		else:
			uid = ident

	payload = {"claim_id": cid} if cid else {"user_id": uid}
	do_data, status_code = _request_do(
		"POST",
		"/redeem",
		workspace_date=target_date,
		json_body=payload,
	)

	slot_id = do_data.get("slotId")
	if slot_id:
		session_row = frappe.db.get_value(
			"Food Truck Session",
			{"slot_id": slot_id, "session_date": target_date},
			["name", "food_truck_unit", "slot_time"],
			as_dict=True,
		)
		if session_row:
			unit_info = frappe.db.get_value(
				"Food Truck Unit",
				session_row.food_truck_unit,
				["unit_name", "location"],
				as_dict=True,
			)
			do_data["session_name"] = session_row.name
			do_data["food_truck_unit"] = session_row.food_truck_unit
			do_data["unit_name"] = unit_info.unit_name if unit_info else session_row.food_truck_unit
			do_data["location"] = (unit_info.location or "") if unit_info else ""
			do_data["slot_time"] = session_row.slot_time

	return {
		**do_data,
		"http_status": status_code,
		"date": target_date,
	}


def _upsert_ticket_history(claim: dict, session_by_slot: dict, target_date: str) -> str:
	claim_id = (claim.get("id") or claim.get("claimId") or "").strip()
	if not claim_id:
		return ""

	slot_id = claim.get("slot_id") or claim.get("slotId") or ""
	session_info = session_by_slot.get(slot_id)
	food_truck_unit = session_info.food_truck_unit if session_info else (
		slot_id.split("_", 1)[0] if "_" in slot_id else ""
	)
	slot_time = session_info.slot_time if session_info else (
		slot_id.split("_", 1)[1] if "_" in slot_id else ""
	)

	values = {
		"request_id": claim.get("request_id") or claim.get("requestId") or "",
		"food_truck_session": session_info.name if session_info else None,
		"food_truck_unit": food_truck_unit if frappe.db.exists("Food Truck Unit", food_truck_unit) else None,
		"session_date": str(session_info.session_date) if session_info else target_date,
		"slot_id": slot_id,
		"slot_time": slot_time,
		"user_id": str(claim.get("user_id") or claim.get("userId") or ""),
		"queue_pos": int(claim.get("queue_pos") or claim.get("queuePos") or 1),
		"status": claim.get("status") or "EXPIRED",
		"claimed_at": _ms_to_datetime_str(claim.get("claimed_at") or claim.get("claimedAt")),
		"redeemed_at": _ms_to_datetime_str(claim.get("redeemed_at") or claim.get("redeemedAt")),
		"expired_at": _ms_to_datetime_str(claim.get("expires_at") or claim.get("expiresAt")),
	}

	if frappe.db.exists("Food Truck Ticket History", claim_id):
		doc = frappe.get_doc("Food Truck Ticket History", claim_id)
		doc.update(values)
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc(
			{
				"doctype": "Food Truck Ticket History",
				"claim_id": claim_id,
				**values,
			}
		)
		doc.insert(ignore_permissions=True)

	return doc.name


@frappe.whitelist()
def close_session_and_sync(
	session_name: str | None = None,
	date: str | None = None,
	food_truck_unit: str | None = None,
) -> dict:
	"""Tutup sesi di DO (POST /close-session), simpan claims secara idempotent ke Food Truck Ticket History, dan ubah status sesi ke Closed."""
	require_role(ROLE_PETUGAS)

	session_name = (session_name or "").strip()
	food_truck_unit = (food_truck_unit or "").strip()

	if session_name:
		if not frappe.db.exists("Food Truck Session", session_name):
			frappe.throw(f"Food Truck Session '{session_name}' tidak ditemukan.", frappe.DoesNotExistError)
		session_doc = frappe.get_doc("Food Truck Session", session_name)
		target_sessions = [session_doc]
		target_date = str(session_doc.session_date)
	else:
		target_date = str(date or today())
		filters: dict = {"session_date": target_date, "status": ["in", ["Open", "Closed"]]}
		if food_truck_unit:
			filters["food_truck_unit"] = food_truck_unit
		names = frappe.get_all("Food Truck Session", filters=filters, pluck="name")
		target_sessions = [frappe.get_doc("Food Truck Session", name) for name in names]

	if not target_sessions:
		frappe.throw("Tidak ada Food Truck Session yang ditemukan untuk ditutup.")

	# Pastikan slot sudah ter-seed di DO sebelum ditutup
	open_subset = [
		{
			"name": s.name,
			"slot_id": s.slot_id,
			"slot_time": s.slot_time,
			"quota": s.quota,
			"synced_to_do": s.synced_to_do,
		}
		for s in target_sessions
		if not s.synced_to_do
	]
	if open_subset:
		_sync_open_sessions_internal(target_date, open_subset)

	slot_ids = [s.slot_id for s in target_sessions if s.slot_id]
	do_data, status_code = _request_do(
		"POST",
		"/close-session",
		workspace_date=target_date,
		json_body={"slot_ids": slot_ids},
	)
	if not do_data.get("ok"):
		frappe.throw(do_data.get("message") or f"Gagal menutup sesi di DO (HTTP {status_code})")

	session_by_slot = {s.slot_id: s for s in target_sessions}
	claims = do_data.get("claims") or []
	synced_claim_ids = []
	for claim in claims:
		saved_id = _upsert_ticket_history(claim, session_by_slot, target_date)
		if saved_id:
			synced_claim_ids.append(saved_id)

	closed_dt = _ms_to_datetime_str(do_data.get("closedAt")) or now_datetime().strftime("%Y-%m-%d %H:%M:%S")
	closed_sessions = []
	for s in target_sessions:
		s.status = "Closed"
		s.synced_to_do = 1
		if not s.closed_at:
			s.closed_at = closed_dt
		s.save(ignore_permissions=True)
		closed_sessions.append(s.name)

	return {
		"ok": True,
		"date": target_date,
		"closed_sessions": closed_sessions,
		"slot_ids": slot_ids,
		"synced_claims_count": len(synced_claim_ids),
		"synced_claim_ids": synced_claim_ids,
		"slots": do_data.get("slots") or [],
		"claims": claims,
	}

