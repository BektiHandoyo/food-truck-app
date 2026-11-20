# Copyright (c) 2026, BektiHandoyo and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class FoodTruckSession(Document):
	def validate(self):
		if self.slot_time:
			self.slot_time = self.slot_time.strip()
		if self.food_truck_unit and self.slot_time:
			self.slot_id = f"{self.food_truck_unit}_{self.slot_time}"
		if (self.quota or 0) <= 0:
			frappe.throw("Quota must be greater than 0")
