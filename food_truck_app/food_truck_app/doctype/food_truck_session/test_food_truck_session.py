# Copyright (c) 2026, BektiHandoyo and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

from food_truck_app.api import (
	claim_ticket,
	get_available_slots,
	get_my_ticket,
	sync_slots_to_do,
)
from food_truck_app.auth import (
	ROLE_MAHASISWA,
	ROLE_PETUGAS,
	get_student_nim,
	require_login,
	require_role,
)


class TestFoodTruckSession(FrappeTestCase):
	def test_core_doctypes_and_settings(self):
		# 1. Food Truck Settings (Single)
		settings = frappe.get_single("Food Truck Settings")
		settings.do_base_url = "http://127.0.0.1:8080"
		settings.do_api_secret = "dev-food-truck-secret"
		settings.save()

		self.assertEqual(
			settings.get_password("do_api_secret"),
			"dev-food-truck-secret",
		)

		# 2. Food Truck Unit
		if not frappe.db.exists("Food Truck Unit", "FT-TEST-01"):
			unit = frappe.get_doc(
				{
					"doctype": "Food Truck Unit",
					"unit_code": "FT-TEST-01",
					"unit_name": "Food Truck Test 01",
					"location": "Gedung A",
					"is_active": 1,
				}
			).insert()
		else:
			unit = frappe.get_doc("Food Truck Unit", "FT-TEST-01")

		self.assertEqual(unit.name, "FT-TEST-01")

		# 3. Food Truck Session
		session = frappe.get_doc(
			{
				"doctype": "Food Truck Session",
				"session_date": "2026-11-20",
				"food_truck_unit": unit.name,
				"slot_time": "12:00",
				"quota": 25,
				"status": "Open",
			}
		).insert()

		self.assertEqual(session.slot_id, "FT-TEST-01_12:00")

		# 4. Food Truck Ticket History
		if not frappe.db.exists("Food Truck Ticket History", "claim-test-uuid-001"):
			history = frappe.get_doc(
				{
					"doctype": "Food Truck Ticket History",
					"claim_id": "claim-test-uuid-001",
					"request_id": "req-test-001",
					"food_truck_session": session.name,
					"food_truck_unit": unit.name,
					"session_date": "2026-11-20",
					"slot_id": session.slot_id,
					"slot_time": session.slot_time,
					"user_id": "240001",
					"queue_pos": 1,
					"status": "REDEEMED",
				}
			).insert()
			self.assertEqual(history.name, "claim-test-uuid-001")

	def test_workspace_roles_and_auth_helpers(self):
		# 1. Workspace & Roles dari fixtures
		self.assertTrue(frappe.db.exists("Workspace", "Food Truck"))
		self.assertTrue(frappe.db.exists("Role", ROLE_MAHASISWA))
		self.assertTrue(frappe.db.exists("Role", ROLE_PETUGAS))
		self.assertTrue(frappe.db.exists("Custom Field", "User-nim"))

		# 2. Buat user Mahasiswa dengan NIM
		mhs_email = "mhs240099@food.truck"
		if not frappe.db.exists("User", mhs_email):
			mhs = frappe.get_doc(
				{
					"doctype": "User",
					"email": mhs_email,
					"first_name": "Mahasiswa Test",
					"username": "240099",
					"nim": "240099",
					"send_welcome_email": 0,
					"roles": [{"role": ROLE_MAHASISWA}],
				}
			).insert(ignore_permissions=True)
		else:
			mhs = frappe.get_doc("User", mhs_email)

		self.assertEqual(get_student_nim(mhs.name), "240099")

		# 3. Validasi require_login dan require_role
		prev_user = frappe.session.user
		try:
			frappe.set_user("Guest")
			with self.assertRaises(frappe.PermissionError):
				require_login()

			frappe.set_user(mhs_email)
			self.assertEqual(require_role(ROLE_MAHASISWA), mhs_email)
			with self.assertRaises(frappe.PermissionError):
				require_role(ROLE_PETUGAS)
		finally:
			frappe.set_user(prev_user)

	def test_student_api_proxy_with_do(self):
		# Konfigurasi Food Truck Settings ke port celld dev (18787)
		settings = frappe.get_single("Food Truck Settings")
		settings.do_base_url = "http://127.0.0.1:18787"
		settings.do_api_secret = "dev-food-truck-secret"
		settings.save()

		today_str = str(today())

		# Buat Unit & Sesi Open hari ini + Sesi Draft hari ini
		if not frappe.db.exists("Food Truck Unit", "FT-API-01"):
			frappe.get_doc(
				{
					"doctype": "Food Truck Unit",
					"unit_code": "FT-API-01",
					"unit_name": "Gerobak Nusantara 1",
					"location": "Kantin Timur",
					"is_active": 1,
				}
			).insert()

		frappe.db.delete(
			"Food Truck Session",
			{"food_truck_unit": "FT-API-01", "session_date": today_str},
		)

		open_session = frappe.get_doc(
			{
				"doctype": "Food Truck Session",
				"session_date": today_str,
				"food_truck_unit": "FT-API-01",
				"slot_time": "12:30",
				"quota": 3,
				"status": "Open",
			}
		).insert()

		draft_session = frappe.get_doc(
			{
				"doctype": "Food Truck Session",
				"session_date": today_str,
				"food_truck_unit": "FT-API-01",
				"slot_time": "15:00",
				"quota": 3,
				"status": "Draft",
			}
		).insert()

		# Buat user Mahasiswa untuk klaim
		mhs_email = "mhs240101@food.truck"
		if not frappe.db.exists("User", mhs_email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": mhs_email,
					"first_name": "Budi Mahasiswa",
					"username": "240101",
					"nim": "240101",
					"send_welcome_email": 0,
					"roles": [{"role": ROLE_MAHASISWA}],
				}
			).insert(ignore_permissions=True)

		prev_user = frappe.session.user
		try:
			# 1. Guest ditolak pada semua endpoint
			frappe.set_user("Guest")
			with self.assertRaises(frappe.PermissionError):
				get_available_slots(today_str)
			with self.assertRaises(frappe.PermissionError):
				claim_ticket(open_session.slot_id)
			with self.assertRaises(frappe.PermissionError):
				get_my_ticket(today_str)

			# 2. Admin / Petugas sync slot ke DO
			frappe.set_user("Administrator")
			sync_res = sync_slots_to_do(today_str)
			self.assertTrue(sync_res["ok"])
			self.assertGreaterEqual(sync_res["synced"], 1)

			# 3. Mahasiswa cek slot & klaim tiket
			frappe.set_user(mhs_email)
			slots_res = get_available_slots(today_str)
			self.assertTrue(slots_res["ok"])
			api_slot = next(s for s in slots_res["slots"] if s["slot_id"] == open_session.slot_id)
			self.assertEqual(api_slot["quota"], 3)

			# War Window check: slot berstatus Draft wajib ditolak
			with self.assertRaises(frappe.ValidationError):
				claim_ticket(draft_session.slot_id)

			# Klaim slot Open
			claim_res = claim_ticket(open_session.slot_id, request_id="test-api-claim-240101")
			self.assertTrue(claim_res["ok"])
			self.assertEqual(claim_res["status"], "CLAIMED")
			self.assertEqual(claim_res["user_id"], "240101")

			# 4. Reload halaman (/war-tiket) -> get_my_ticket mengembalikan tiket aktif
			my_ticket = get_my_ticket(today_str)
			self.assertTrue(my_ticket["ok"])
			self.assertIsNotNone(my_ticket["claim"])
			self.assertEqual(my_ticket["claim"]["claimId"], claim_res["claimId"])
			self.assertEqual(my_ticket["claim"]["unitName"], "Gerobak Nusantara 1")
		finally:
			frappe.set_user(prev_user)

