# Copyright (c) 2026, BektiHandoyo and contributors
# For license information, please see license.txt

import frappe
import requests
from frappe.utils import today

from food_truck_app.auth import (
	ROLE_MAHASISWA,
	ROLE_PETUGAS,
	get_student_nim,
	require_login,
	require_role,
)

DO_TIMEOUT_SECONDS = 10


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
