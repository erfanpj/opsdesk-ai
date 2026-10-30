---
doc_id: orders
doc_version: 1.4
effective_date: 2026-02-15
category: order_status
---
## HB-ORD-01 | Where is my order
keywords: where is my order, tracking, not arrived, hasn't arrived, has not arrived, delivery date, when will, shipped, still waiting
action: send_tracking_update
Agents may send the current tracking link and the carrier's latest delivery estimate. Orders usually arrive within 3 to 5 working days inside Germany.

## HB-ORD-02 | Lost parcels
keywords: lost, never arrived, marked delivered, delivered but
action: escalate_to_human
If tracking shows a parcel as delivered but the customer did not receive it, escalate to the logistics team, who open a carrier investigation.
