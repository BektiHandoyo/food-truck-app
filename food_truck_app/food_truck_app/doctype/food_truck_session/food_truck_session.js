// Copyright (c) 2026, BektiHandoyo and contributors
// For license information, please see license.txt

frappe.ui.form.on("Food Truck Session", {
	refresh(frm) {
		if (!frm.is_new() && frm.doc.status === "Open") {
			frm.add_custom_button(__("Sync Slots to DO"), () => {
				frappe.call({
					method: "food_truck_app.api.sync_slots_to_do",
					args: { date: frm.doc.session_date },
					freeze: true,
					freeze_message: __("Syncing slots to Durable Object..."),
					callback(r) {
						if (r.message && r.message.ok) {
							frappe.show_alert({
								message: __("Synced {0} slot(s) to Durable Object", [
									r.message.synced,
								]),
								indicator: "green",
							});
							frm.reload_doc();
						}
					},
				});
			});

			frm.add_custom_button(__("Close Session & Sync History"), () => {
				frappe.confirm(
					__(
						"Tutup sesi ini? Seluruh tiket CLAIMED yang belum di-redeem akan menjadi EXPIRED dan riwayat tiket akan disimpan ke Frappe."
					),
					() => {
						frappe.call({
							method: "food_truck_app.api.close_session_and_sync",
							args: { session_name: frm.doc.name },
							freeze: true,
							freeze_message: __(
								"Closing session and syncing ticket history..."
							),
							callback(r) {
								if (r.message && r.message.ok) {
									frappe.show_alert({
										message: __(
											"Session closed. Synced {0} ticket(s) to Cold Storage.",
											[r.message.synced_claims_count]
										),
										indicator: "green",
									});
									frm.reload_doc();
								}
							},
						});
					}
				);
			});
		}
	},
});
