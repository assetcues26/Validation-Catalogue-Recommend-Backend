Pick the SINGLE best master-data match.

[[VARCTX_START]]
Asset: {detected_asset}
Analysis: {image_analysis}
Name: {user_name} | Desc: {user_desc}

User master data:
- Category: {user_category}
- Sub Category: {user_subcategory}
- Make/Model: {user_makemodel}
- Model: {user_model}

Candidates:
{candidate_summary}
[[VARCTX_END]]

Return JSON:
{{"recommendedcategory":"<exact category from same candidate row or null>","recommendedsubcategory":"<exact subcategory from same row or null>","recommendedmakemodel":"<exact makemodel from same row or null>","recommendedmodel":"<exact model from same row or null>","categorymatch":"matched or failed or null","categorymatchpercent":0-100 or null,"subcategorymatch":"matched or failed or null","subcategorymatchpercent":0-100 or null,"makemodelmatch":"matched or failed or null","makemodelmatchpercent":0-100 or null,"modelmatch":"matched or failed or null","modelmatchpercent":0-100 or null,"recommendation_confidence":50-95,"selection_reasoning":"<25 words>"}}

STEP 1 — Pick ONE candidate row. Return category, subcategory, makemodel, and model ONLY from that row — do not mix fields across rows or invent values.
Return the model string only — never modelid.

STEP 2 — Compare user Category, Sub Category, Make/Model, and Model against the recommended values from that same row (independently for each field):

- categorymatch / categorymatchpercent: compare user category vs recommended category
- subcategorymatch / subcategorymatchpercent: compare user sub category vs recommended subcategory
- makemodelmatch / makemodelmatchpercent: compare user Make/Model vs recommended makemodel only (not Model)
- modelmatch / modelmatchpercent: compare user Model vs recommended model only (not Make/Model)

Match percent rules:
- matched → 85-100 (exact/near-exact higher; semantic alias lower)
- failed → 10-40 (clear mismatch → 10-20)
- null when either side is empty

STRICT MATCH RULES (high accuracy):
- Category: same domain only ("IT Equipment" ≈ "IT Assets" OK; "laptop" vs "IT Assets" may match only if same domain; "Furniture" vs "IT" → failed)
- Subcategory: same asset type required ("Laptop" ≈ "Notebook" OK; "Business tv" vs "Laptop" → failed)
- Make/Model: same product line/family; compare user Make/Model to recommended makemodel string
- Model: same product line/generation; compare user Model to recommended model string
- When uncertain → failed (never guess matched)
- Do NOT mark matched because Pinecone score is high

NEGATIVE EXAMPLES (must return failed with low percent):
- User category "Furniture", recommended "IT Equipment" → categorymatch failed, categorymatchpercent 10-20
- User subcategory "Business tv", recommended "Laptop" → subcategorymatch failed, subcategorymatchpercent 10-20
- User subcategory "Printer", recommended "Laptop" → subcategorymatch failed
- User Make/Model "Dell Latitude", recommended "ThinkPad L490" → makemodelmatch failed, makemodelmatchpercent 10-20
- User model "iPhone 12", recommended "iPhone 14" → modelmatch failed

If no match: null recommendations, null for all match fields and percents, recommendation_confidence 0.
Note: recommendation_confidence is Pinecone fit only — not the match percents above.
