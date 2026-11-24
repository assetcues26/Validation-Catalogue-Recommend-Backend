
=== PRIORITY 3: CUSTOM FIELD EXTRACTION (from ASSET + BARCODE images) ===
Each line: JSON key = instruction (NOT the answer). Return what you OBSERVE from the images.
- An empty user value does NOT mean skip — always extract and return the observed value or JSON null.
- When instruction is "observe and return: <field>", extract that attribute visible on the asset/body/stickers.
- Use JSON null when not visible/unknown — NEVER the string "None", "N/A", or "null".
- For label/sticker fields: describe visible brand or asset labels on the asset body; JSON null if none.

{custom_field_lines}

Example: "color" instruction "observe and return: color" → "color": "Silver"
Example: "brand" instruction "visible manufacturer brand" → "brand": "HP"
Required JSON keys: {custom_field_keys}
