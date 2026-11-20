# Copyright (c) 2026, BektiHandoyo and contributors
# For license information, please see license.txt

import frappe

ROLE_MAHASISWA = "Mahasiswa"
ROLE_PETUGAS = "Petugas Food Truck"
ROLE_SYSTEM_MANAGER = "System Manager"


def require_login() -> str:
	"""Pastikan user sudah login (bukan Guest) dan kembalikan frappe.session.user."""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw("Silakan login terlebih dahulu.", frappe.PermissionError)
	return user


def require_role(*allowed_roles: str) -> str:
	"""Pastikan user login dan memiliki salah satu dari allowed_roles (atau System Manager)."""
	user = require_login()
	if user == "Administrator":
		return user

	user_roles = set(frappe.get_roles(user))
	acceptable = set(allowed_roles) | {ROLE_SYSTEM_MANAGER}
	if not (user_roles & acceptable):
		frappe.throw(
			f"Akses ditolak. Membutuhkan role: {', '.join(allowed_roles)}",
			frappe.PermissionError,
		)
	return user


def get_student_nim(user: str | None = None) -> str:
	"""Ambil identitas NIM mahasiswa dari User.nim -> User.username -> User.name."""
	target_user = user or require_login()
	user_row = frappe.db.get_value(
		"User",
		target_user,
		["nim", "username", "name"],
		as_dict=True,
	)
	if not user_row:
		return target_user

	for key in ("nim", "username", "name"):
		val = (user_row.get(key) or "").strip()
		if val:
			return val
	return target_user
