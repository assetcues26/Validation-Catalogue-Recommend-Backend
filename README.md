# Validation and Catalogue Recommend

FastAPI service that **wires existing logic** (does not rewrite prompts/rules):

1. **Additional Asset FastAPI** (`additional_asset/`) — image prep, unified Phase 1 generation, Pinecone catalogue join  
2. **validation-ai-backend** (`asset_validator/`) — name/description match + tag/barcode match  

Orchestration-only code lives in `validation_catalogue/`.

---

## Pipeline

```
POST /api/validation_catalogue
  Stage A  Generation          ← additional_asset (through unified Phase 1)
  Stage B  Matching (optional) ← asset_validator name + tag stacks
  Stage C  Catalogue / Pinecone← additional_asset (+ identity when name match Y)
```

| Stage | When | Source |
|--------|------|--------|
| A Generation | Always | AA unified Phase 1 + zxing + condition/tag merge |
| B Name match | AssetName and/or Description present | validation Phase 1.5 + `_settle_name_check` |
| B Tag match | TagNumber present | validation tag compare (consumes Stage A decode) |
| C Pinecone | `INCLUDE_PINECONE_LOOKUP=true` | AA query/select/join; identity path if name match **Y** |

If **neither** name nor description nor tag is provided → Stage B is skipped.

### Pinecone when `NameDescriptionMatch == Y`

| User provided | Search text |
|---------------|-------------|
| Description only | user Description + LLM DetectedAsset + truncated ImageAnalysis |
| AssetName only | user AssetName + LLM Description + truncated ImageAnalysis |
| Both | user AssetName + user Description + truncated ImageAnalysis |

Vision type filter is **skipped** on Y. Selection prompt + pick/join stay Additional Asset.

When match is **N** or matching was skipped → AA vision path (`DetectedAsset + Description + truncated analysis`), filter **on**.

`ImageAnalysis` from Phase 1 is **not** hard-capped in the unified call (prompt soft-target ~60 words). Downstream Pinecone / name-match inject truncate via `PHASE2_IMAGE_ANALYSIS_MAX_CHARS` (default 350).

---

## Quick start

```powershell
cd "Validation and Catalogue Recommend"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env — set GEMINI_API_KEY and PINECONE_API_KEY
uvicorn main:app --host 0.0.0.0 --port 8001 --reload --log-level info
```

| Endpoint | Method |
|----------|--------|
| `/api/health` | GET |
| `/api/validation_catalogue` | POST |
| `/docs` | Swagger UI |

### Postman (multipart)

| Key | Type | Required |
|-----|------|----------|
| `AssetImage` | File | Yes |
| `BarcodeImage` | File | Yes |
| `CustomerId` | Text | Yes |
| `AssetName` | Text | No |
| `Description` | Text | No |
| `TagNumber` | Text | No |
| `CategoryId` / `AssetClassId` | Text | No |

---

## Package layout

| Path | Role |
|------|------|
| `main.py` | HTTP + payload guards |
| `validation_catalogue/core/models.py` | Request/response contract |
| `validation_catalogue/core/payload_guards.py` | Size / type / magic-byte limits |
| `validation_catalogue/services/pipeline.py` | Orchestration only |
| `validation_catalogue/services/generation.py` | Stage A → AA |
| `validation_catalogue/services/matching.py` | Stage B → validation |
| `validation_catalogue/services/catalogue.py` | Stage C → AA + identity gate |
| `additional_asset/` | Vendored Additional Asset package |
| `asset_validator/` | Vendored validation-ai-backend package |

---

## Security (before GitHub push)

### Never commit secrets

- `.env` is gitignored. Use `.env.example` (empty keys) only.
- Do **not** commit `debug/*.jsonl` (may contain asset text / PII).
- Rotate any key that was ever pasted into chat, screenshots, or a committed `.env`.

### Payload safeguards (HTTP layer)

| Guard | Default | Env override |
|--------|---------|--------------|
| Max request `Content-Length` | 25 MB | `MAX_REQUEST_BODY_BYTES` |
| Max per-image bytes | 12 MB | `MAX_IMAGE_BYTES` |
| Min image bytes | 32 | `MIN_IMAGE_BYTES` |
| Image magic (JPEG/PNG/GIF/WEBP/BMP) | required | — |
| AssetName length | 500 | `MAX_ASSET_NAME_CHARS` |
| Description length | 2000 | `MAX_DESCRIPTION_CHARS` |
| TagNumber length | 128 | `MAX_TAG_NUMBER_CHARS` |
| NUL bytes in text | stripped | — |
| JSON field count | ≤ 64 | — |
| Positive IDs only | `customerid` required; optional ids | — |
| Error responses | short detail, no raw body echo | — |
| CORS | `*` → credentials **off**; restrict origins in prod | `CORS_ALLOW_ORIGINS` |

These guards reject bad input **before** Stage A/B/C. Pipeline business logic is unchanged for valid requests.

### Production recommendations

1. Set `CORS_ALLOW_ORIGINS` to your front-end origin(s), not `*`.  
2. Put the API behind TLS + auth (API gateway / Azure AD / reverse proxy). This repo does not implement caller auth.  
3. Prefer `DEBUG_LOG_TO_FILE=false` in production, or scrub/rotate `debug/`.  
4. Run with a process manager and body size limits at the reverse proxy as a second line of defense.  
5. Do not expose `/docs` publicly if that conflicts with your policy (disable via FastAPI settings if needed).

---

## Environment

Copy `.env.example` → `.env`. Required:

- `GEMINI_API_KEY`
- `PINECONE_API_KEY` + `PINECONE_INDEX_NAME` (when Pinecone enabled)

Useful flags:

| Variable | Default | Effect |
|----------|---------|--------|
| `PHASE1_CONTENT_ROUTE_ENABLED` | `false` | Content-route Phase 1 vs legacy |
| `BLIND_VIEW_ENABLED` | `true` | Legacy slot routing (2 Gemini calls, parallel) |
| `INCLUDE_PINECONE_LOOKUP` | `true` | Stage C on/off |
| `PHASE2_IMAGE_ANALYSIS_MAX_CHARS` | `350` | Truncate analysis for Pinecone / name inject |
| `DEBUG_LOG_TO_FILE` | `true` | Write `debug/<request_id>.jsonl` |
| `LOG_LEVEL` | `INFO` | Console verbosity |

---

## Debug logging

```powershell
uvicorn main:app --host 0.0.0.0 --port 8001 --reload --log-level info
```

- Console: StepLogger phases + Gemini 503/capacity **ERROR** lines  
- File: `debug/<request_id>.jsonl`  
- `llm_usage.by_phase` merges AA phases **and** validation `name_match_phase2`

---

## Response shape (public)

Includes user echoes, LLM generation fields, match fields, joined catalogue ids/names, `MatchingRan`, `PineconeMode`, timings, `llm_usage`.

Does **not** expose raw Recommended* selection fields or redundant `AssetName` alias (use `UserAssetName` + `DetectedAsset`).  
`TagNumber` public object: `decodedtagnumber`, `detectedtagnumber`, `tagnumbersource`, `barcodereadability`, `barcodeposition`.

---

## GitHub checklist

- [ ] Confirm `.env` is **not** staged (`git status` / `git check-ignore -v .env`)  
- [ ] Confirm `debug/` and `.venv/` are ignored  
- [ ] Secrets only in CI/hosting env vars or a private secret store  
- [ ] Set production `CORS_ALLOW_ORIGINS`  
- [ ] README + `.env.example` committed; real `.env` never committed  

```powershell
# From this folder after git init
git status
git check-ignore -v .env .venv debug
```
