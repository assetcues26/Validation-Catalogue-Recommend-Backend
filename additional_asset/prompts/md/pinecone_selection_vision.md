Pick the best master-data row from the candidates. Judge by the vision identity below — never by Pinecone score.

[[VARCTX_START]]
Asset: {detected_asset}
Description: {detected_description}

Candidates:
{candidate_summary}
[[VARCTX_END]]

Return JSON only:
{{"recommendedcategory":"<exact category from chosen row or null>","recommendedsubcategory":"<exact subcategory from chosen row or null>","recommendedmakemodel":"<exact makemodel from chosen row or null>","recommendedmodel":"<exact model from chosen row or null>","recommendation_confidence":0-95,"selection_reasoning":"<25 words>"}}

Decide in two independent levels. Subcategory and makemodel are separate decisions.

LEVEL 1 — SUBCATEGORY (asset type)
- Does any candidate subcategory name the same asset TYPE as the vision identity? (e.g. laptop ↔ LAPTOP or LAPTOPS; split AC ↔ AIR CONDITIONER)
- YES → you MUST keep a fitting subcategory. Do NOT null subcategory because the exact model, size, or variant is unclear.
- NO (every candidate is a different type, e.g. vision laptop vs only phones/chairs) → null ALL fields, recommendation_confidence 0.
- Spelling variants of the same type count as the same type (LAPTOP / LAPTOPS, DESKTOP / DESKTOPS).

LEVEL 2 — MAKEMODEL (only among rows whose subcategory passed Level 1)
Pick the most specific makemodel the vision identity supports, in this order:
  a. Exact or near-exact product line (e.g. "Vivobook S" ↔ "VivoBook S14").
  b. Same brand family line (e.g. "ASUS Vivobook" ↔ any "VivoBook …" row).
  c. Brand-only row (e.g. "Asus" / "ASUS" when vision names ASUS).
  d. If vision names NO brand → prefer a row whose makemodel is empty / NA; else return that fitting subcategory with makemodel null.
  e. If vision names a brand and NO candidate in the fitting subcategory carries that brand → keep subcategory, makemodel null.
- NEVER return a different brand than the one in the vision identity (Lloyd → LG is forbidden; ASUS → HP is forbidden).
- If unsure between two same-brand rows (14 vs 15), prefer the brand-only row (c) or the closest family row (b) — do NOT null everything.
- When makemodel is null, still return the chosen subcategory and that row's category.

Return category, subcategory, makemodel, and model from ONE candidate row. When makemodel is null, use any row with the chosen subcategory for category/subcategory values. Never invent values not in the list. Return the model string only — never an id.

EXAMPLES
- Vision "ASUS Vivobook laptop"; candidates include LAPTOP VivoBook 15, LAPTOP VivoBook S14, LAPTOPS Asus → subcategory LAPTOP or LAPTOPS, makemodel VivoBook S14 or VivoBook family or Asus (never null subcategory).
- Vision "HP Victus laptop"; candidates include LAPTOPS HP, LAPTOP hp → LAPTOPS (or LAPTOP) + HP / hp.
- Vision "Lloyd split AC"; candidates only LG/Voltas/Carrier under AIR CONDITIONER → AIR CONDITIONER + makemodel null.
- Vision "split AC", no brand; candidates all branded → AIR CONDITIONER + makemodel null (or NA row if present).
- Vision laptop; candidates only phones/chairs → all null.

recommendation_confidence: how well the chosen subcategory (and makemodel if set) fits the vision identity — not the Pinecone score. Use 0 only when all fields are null.
