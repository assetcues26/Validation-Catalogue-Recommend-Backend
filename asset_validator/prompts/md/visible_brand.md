Look at this photograph. Report ONLY what is printed on one specific object in it.

[[VARCTX_START]]
THE OBJECT TO INSPECT: {target_asset}
[[VARCTX_END]]

QUESTION: is a MANUFACTURER's brand or model marking legibly readable on THAT object's body?

A workplace photo almost always holds several things — a phone or laptop on a desk, a
monitor on a table, equipment on a rack. Branding on any of those OTHER objects is not an
answer: a Cisco phone standing on an unbranded desk means the desk has no brand. Inspect
only the object named above.

Read it from the pixels. You are told what the object is so you can find it in the frame,
and nothing else — no declared make, no model, no description. If you cannot READ a maker's
marking on that object in this image, the answer is null. Never guess a brand from the shape
of the object, from what such equipment is usually made by, or from the setting it stands in.

COUNTS as a manufacturer marking:
- a maker's nameplate, rating plate or data plate on the housing
- a brand name or logo moulded, printed, etched or badged on the asset body
- a model or SKU line printed alongside that brand

Does NOT count — return null if this is all you can see:
- an asset-tracking sticker belonging to the owner: a QR or barcode label, "PROPERTY OF ...",
  an inventory number, a company logo on that label. These identify the OWNER, not the maker.
- painted or stencilled operational text: capacities, pressures, flow rates, "CAPACITY 500 LTS"
- safety and instruction notices, warning triangles, electrical ratings with no maker name
- signage on the wall, floor, rack or building behind or around the asset
- a brand on some OTHER object in the frame that is not the asset being photographed

Also return null when the asset is wrapped in plastic or covering, shrouded, turned away,
too far away, too dark, or simply carries no marking at all.

Return JSON only:
{"brand": "<exactly the text you can read, or null>", "reason": "<12 words or fewer>"}
