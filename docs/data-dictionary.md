# Data Dictionary — Continuous KYC Autonomous Auditor

Companion reference to `docs/phase-0-dataset-audit.md`. All row/column counts and value
distributions here were measured directly against the files, not estimated.

---

## `data/kyc_profiles/clients_with_fatf_ofac.csv`

**Purpose:** Client master — the primary entity being monitored. 2,000 rows, one per client.
**Duplicate copy:** identical (MD5-verified) at `data/clients_with_fatf_ofac.csv` — read from the
`kyc_profiles/` path as canonical; do not load both.

| Column | Type | Nullable | Meaning | Notes |
|---|---|---|---|---|
| `client_id` | int | No | **Primary key.** 1–2000, unique. | Join key for `client_account_mapping.csv` and `transactions_with_fatf_ofac.csv`. |
| `client_name` | string | No | Entity/individual display name. | Faker-generated; 101/2000 values are duplicated (real dedup test surface); does **not** correspond to any real name in the sanctions data. |
| `client_type` | categorical | No | NGO / Financial Institution / Corporate / Individual. | Roughly evenly distributed (482–512 each). |
| `sector` | categorical | No | Industry sector, e.g. Import/Export, Financial Services, Defense/Arms, NGO/Charity, Energy/Oil, Casino/Gambling, Crypto Exchange, Real Estate, Consulting, Retail, Tech. | 8+ distinct values. |
| `sector_risk` | categorical | No | Low / Medium / High. | High 920, Medium 585, Low 495. Appears to be a static sector→risk lookup, not client-specific. |
| `country` | string | No | ISO-2 country code. | e.g. CH, SD, VE, SY, FR, CA, RU, AU, AE, US, KP. |
| `pep_flag` | int (0/1) | No | Politically Exposed Person indicator. | 1 for 114/2000 (5.7%). **Upstream label — treat as given input, not derivable from this dataset alone.** |
| `sanctions_flag` | int (0/1) | No | Pre-existing sanctions indicator. | 1 for 55/2000 (2.8%). **Upstream label. 0/55 flagged clients have an exact-name match in the real OFAC SDN list — do not represent this flag as something our screening engine produced.** |
| `fatf_country_flag` | int (0/1) | No | Client domiciled in a FATF grey/black-list jurisdiction. | 1 for 179/2000 (9.0%). |
| `ofac_country_flag` | int (0/1) | No | Client domiciled in an OFAC-sanctioned jurisdiction. | 1 for 601/2000 (30.1%). |
| `sectoral_sanctions_flag` | int (0/1) | No | Client subject to sectoral (not full-blocking) sanctions. | 1 for 575/2000 (28.8%). |
| `ownership_opacity_score` | float | No | 0.0–1.0 continuous opacity score. | Mean 0.111, median 0.0 (75th pct still 0.0) — sparse, right-skewed; max 1.0. |

**Data-quality notes:** zero nulls anywhere; `client_id` is a clean unique key; no fully duplicated
rows. The flag columns appear to be independently generated (no obvious circular derivation), but
their generating process is unknown/upstream — do not present them in a UI as freshly-screened
results.

---

## `data/kyc_profiles/client_account_mapping.csv`

**Purpose:** Bridge table linking the KYC client master to the transactional (SAML-D) world.

| Column | Type | Nullable | Meaning | Notes |
|---|---|---|---|---|
| `client_id` | int | No | FK → `clients_with_fatf_ofac.csv.client_id`. | Subset of the full client roster — only 60 distinct clients appear here. |
| `account` | int | No | Account number. | **Primary key of this table** (unique, no duplicates). FK target for `SAML-D.csv.Sender_account` / `Receiver_account`. |

**Shape:** 120 rows exactly — 60 clients × exactly 2 accounts each (verified: mean = std = 0 on
accounts-per-client). Verified against the full 9,504,852-row SAML-D scan: **120/120 mapped
accounts have at least one transaction as sender; 96/120 also appear as a receiver.** This is the
single most important join in the dataset — it is what gives 60 of the 2,000 clients real
behavioural depth.

---

## `data/kyc_profiles/transactions_with_fatf_ofac.csv`

**Purpose:** Shallow but broad transaction layer — pre-flagged, one file covering all 2,000
clients (not just the 60 mapped ones).
**Duplicate copy:** identical (MD5-verified) at `data/transactions_with_fatf_ofac.csv` — read from
`kyc_profiles/` as canonical.

| Column | Type | Nullable | Meaning | Notes |
|---|---|---|---|---|
| `transaction_id` | int | No | **Primary key.** Unique, no duplicates. | |
| `client_id` | int | No | FK → `clients_with_fatf_ofac.csv.client_id`. | Covers all 2,000 clients; confirmed subset relationship. |
| `amount` | float | No | Transaction amount. | Min $0.01, mean $4,196, median $1,582, max $324,733. Currency not specified in this file. |
| `transaction_type` | categorical | No | Check / Wire / ACH / SWIFT. | Near-even split (~12,500 each). |
| `timestamp` | datetime string | No | Transaction time. | Range **2025-07-02 23:19:11 → 2025-09-30 23:15:37** (~3 months). Does not overlap SAML-D's date range. |
| `client_country` | string | No | ISO-2 country of the client side. | 21 distinct values. |
| `counterparty_country` | string | No | ISO-2 country of the counterparty. | 21 distinct values; includes IR, KP, SY, SD, RU, VE, AF. |
| `ofac_match_flag` | int (0/1) | No | Pre-computed OFAC-related flag on this transaction. | 1,790/50,000 (3.6%). |
| `fatf_country_flag` | int (0/1) | No | Counterparty/route touches a FATF-listed jurisdiction. | 602/50,000 (1.2%). |
| `structuring_pattern_flag` | int (0/1) | No | Structuring typology indicator. | 347/50,000 (0.7%). |
| `rapid_movement_flag` | int (0/1) | No | Rapid fund movement indicator. | 2,460/50,000 (4.9%). |
| `trade_mispricing_flag` | int (0/1) | No | Trade mispricing indicator. | **Only 10/50,000 (0.02%) — too sparse to calibrate a standalone scoring band.** |

**Data-quality notes:** zero nulls anywhere; `transaction_id` unique.

---

## `data/aml_transactions/SAML-D.csv`

**Purpose:** Deep, labelled AML transaction history — the ground-truth behavioural layer. 951 MB,
9,504,852 rows. Only reachable for a given client via `client_account_mapping.csv`.

| Column | Type | Nullable | Meaning | Notes |
|---|---|---|---|---|
| `Time` | time string | No | Time of day. | |
| `Date` | date string | No | Transaction date. | Range **2022-10-07 → 2023-08-23**. Non-overlapping with `transactions_with_fatf_ofac.csv`'s date range — join on account/client only, never on time. |
| `Sender_account` | int | No | Sending account number. | FK target from `client_account_mapping.csv.account`. Verified: all 120 mapped accounts appear as sender at least once. |
| `Receiver_account` | int | No | Receiving account number. | 96/120 mapped accounts also appear as receiver. |
| `Amount` | float | No | Transaction amount. | $3.73 – $12,618,498.40. |
| `Payment_currency` | string | No | Currency sent. | |
| `Received_currency` | string | No | Currency received. | May differ from `Payment_currency` (cross-currency transfers). |
| `Sender_bank_location` | string | No | Sender bank's country/location. | |
| `Receiver_bank_location` | string | No | Receiver bank's country/location. | |
| `Payment_type` | categorical | No | ACH, Credit card, Cheque, Debit card, Cross-border, Cash Withdrawal, Cash Deposit. | 7 distinct values, roughly 2M each for the four largest categories. |
| `Is_laundering` | int (0/1) | No | **Ground-truth label.** | 9,873/9,504,852 = 0.1039% base rate. This is the strongest behavioural signal available anywhere in the dataset, but only for the 60 mapped clients. |
| `Laundering_type` | categorical | No | Typology label. | On `Is_laundering=1` rows: Structuring, Cash_Withdrawal, Deposit-Send, Smurfing, Layered_Fan_In/Out, Stacked Bipartite, Behavioural_Change_1/2, Bipartite, Cycle, Fan_In/Out, Gather-Scatter, Scatter-Gather, Single_large, Over-Invoicing. On `Is_laundering=0` rows, a separate "Normal_*" vocabulary describes normal transaction shape (Normal_Fan_Out, Normal_Cash_Deposits, etc.) — **do not treat `Laundering_type` alone as a laundering indicator; it must always be read together with `Is_laundering`.** |

**Data-quality notes:** zero nulls across all 9,504,852 rows (full chunked scan, not sampled).

---

## `data/sanctions/ofac_sdn.csv` (Tier 1 — real, full-scale)

**Purpose:** OFAC Specially Designated Nationals list. **No header row in the file.** 19,157 rows,
12 columns.

| Position | Recovered name | Notes |
|---|---|---|
| 0 | `ent_num` | Entity ID, links to `ofac_alt.csv` and `ofac_add.csv`. |
| 1 | `SDN_Name` | Primary listed name, often `LAST, First` for individuals. |
| 2 | `SDN_Type` | `individual` / `vessel` / `aircraft` / blank (`-0-`, mostly entities/companies — 9,810 rows). |
| 3 | `Program` | Sanctions program code, e.g. `RUSSIA-EO14024` (5,677 rows), `SDGT` (2,167), `SDNTK` (1,369), `IRAN-EO13902` (790). |
| 4 | `Title` | Individual's title/role, if applicable. |
| 5 | `Call_Sign` | Vessel call sign, if applicable. |
| 6 | `Vess_type` | Vessel type, if applicable. |
| 7 | `Tonnage` | Vessel tonnage, if applicable. |
| 8 | `GRT` | Gross registered tonnage, if applicable. |
| 9 | `Vess_flag` | Vessel flag country, if applicable. |
| 10 | `Vess_owner` | Vessel owner, if applicable. |
| 11 | `Remarks` | Free text — DOB, nationality, aliases, passport numbers when present. Mostly `-0-` (empty) for Tier-1 rows. |

**Schema recovery method:** column names were **not** assumed from general OFAC format knowledge
alone — they were cross-checked against `sample_ofac_sdn.csv`, which has an identical 12-column
layout with a header row present (see Tier-2 entry below). Re-validate this mapping on first real
ingestion rather than trusting this document blindly.

**Verified:** 0 of the project's narrative entities (Golden Crescent, Hosseini, Abadi, Damascus
Trading, Al-Rashid, and all 2,000 client names) appear as an exact match in this file.

---

## `data/sanctions/ofac_alt.csv` (Tier 1)

**Purpose:** Aliases (aka/fka) for `ofac_sdn.csv` entities. No header row. 20,338 rows, 5 columns.

| Position | Recovered name | Notes |
|---|---|---|
| 0 | `ent_num` | FK → `ofac_sdn.csv` column 0. |
| 1 | `alt_num` | Sequence number of this alias for the entity. |
| 2 | `alt_type` | `aka` / `fka` / etc. |
| 3 | `alt_name` | The alias string. |
| 4 | `alt_remarks` | Usually empty (`-0-`). |

Schema recovered the same way, cross-checked against `sample_ofac_alt.csv`'s identical 5-column
header.

---

## `data/sanctions/ofac_add.csv` (Tier 1)

**Purpose:** Addresses for `ofac_sdn.csv` entities. No header row. 24,930 rows, 6 columns.

| Position | Recovered name | Notes |
|---|---|---|
| 0 | `ent_num` | FK → `ofac_sdn.csv` column 0. |
| 1 | `add_num` | Sequence number of this address for the entity. |
| 2 | `address` | Street address, often `-0-`. |
| 3 | `city_state_zip` | City/state/postal, e.g. `London EC3N 1DY`. |
| 4 | `country` | Country name (spelled out, not ISO code). |
| 5 | `add_remarks` | Usually empty. |

Schema inferred from standard OFAC flat-file convention and directly observed content (no matching
sample file exists for this one — lower confidence than `ofac_sdn`/`ofac_alt` recovery; verify on
ingestion).

---

## `data/sanctions/opensanctions_targets.csv` (Tier 1 — real, full-scale)

**Purpose:** Global sanctions/watchlist/PEP aggregation (OpenSanctions consolidated export). Header
present. 1,319,152 rows, 16 columns.

| Column | Type | Nullable | Meaning | Null rate |
|---|---|---|---|---|
| `id` | string | No | Unique entity ID (e.g. `NK-223CQDBzp8MRkdJMDiqXn3`). | 0% |
| `schema` | categorical | No | Entity type: Person (1,051,373), Company (123,640), LegalEntity (87,941), Security (20,460), CryptoWallet (13,877), Organization (12,620), Vessel (8,870), Airplane (344), Address (19), PublicBody (8). | 0% |
| `name` | string | No | Primary name. | 0% |
| `aliases` | string | Yes | Semicolon-delimited alternate names. | 73.8% |
| `birth_date` | string | Yes | Date of birth (persons). | 62.7% |
| `countries` | string | Yes | Semicolon-delimited ISO-2 country codes. | 8.2% |
| `addresses` | string | Yes | Free-text address(es). | 78.3% |
| `identifiers` | string | Yes | Passport/registry/other ID numbers. | 44.5% |
| `sanctions` | string | Yes | Free-text sanctions program description(s). | 75.6% |
| `phones` | string | Yes | | 99.2% |
| `emails` | string | Yes | | 95.5% |
| `program_ids` | string | Yes | Structured program identifiers. | 87.5% — **absent from `sample_opensanctions.csv` entirely; treat as optional in any shared loader.** |
| `dataset` | string | No | Semicolon-delimited source dataset tags, e.g. `us_ofac_sdn`, `un_sc_sanctions`, `eu_sanctions_map`, `gb_coh_disqualified`, `ru_rupep`. | 0% |
| `first_seen` | datetime string | No | When first added to OpenSanctions. | 0% |
| `last_seen` | datetime string | No | Last confirmed present. | 0% |
| `last_change` | datetime string | No | Last modified. | 0% |

These three date fields are the only genuinely dated history available anywhere in the sanctions
data and are usable for real timeline construction (§ system design doc).

---

## `data/sanctions/sample_ofac_sdn.csv`, `sample_ofac_alt.csv`, `sample_opensanctions.csv` (Tier 2 — curated demo fixture)

**Purpose:** A small (17–21 row), deliberately-curated sanctions fixture. **Not a random sample —
verified to deliberately interlock with the UBO graph and adverse-media article fixtures** (see
`phase-0-dataset-audit.md` §4.5 for the full cross-reference table). Same column layout as the
Tier-1 files above (with header rows present, which is what allowed schema recovery for the
headerless Tier-1 files).

**Confirmed cross-references:**

| Tier-2 entity | Confirmed match elsewhere |
|---|---|
| `AL-RASHID, Mohammad` (DOB 15 Mar 1975, UAE) | `ubo/showcase_structure.json` — UBO-IND-004, same DOB year and nationality |
| `GOLDEN CRESCENT SHIPPING LTD` (Dubai/Panama, Cargo, IMO 9100234) | `articles/adversarial_article.txt` |
| `HOSSEINI, Ali Reza` (DOB 05 Dec 1971, Iran) | `articles/adversarial_article.txt` |
| `ABADI, Farid Hassan` (DOB 25 Jul 1977, Syria) | `articles/adverse_hit_article.txt` |
| `DAMASCUS TRADING HOUSE LLC` (Damascus, Syria) | `articles/adverse_hit_article.txt` |

**Additional entities with no other fixture cross-reference found** (checked by full-repo grep):
Viktor Ivanovich Petrov, Tehran Industrial Metals Co, Kim Jong-Su, Elena Sergeevna Ivanova,
Euroasia Energy Holdings AG, Tran Duc Nguyen, Oriental Pearl Finance Group, Dmitri Alexandrovich
Sokolov, Rahul Sharma, Caspian Sea Oil Services FZCO, Northern Logistics GmbH, Chen Wei Lin —
these are Tier-2-only distractor/volume entities. `sample_opensanctions.csv` additionally includes
5 entries not present in `sample_ofac_sdn.csv`: Ahmed bin Khalid Al Thani (PEP), Maria Gonzalez
Fernandez, Red Star Minerals OOO, Hassan Nasrallah, Sunrise Development Holdings BVI.

**Verified negative controls:** "Nordvale Dairy Cooperative" (clean article) and "Greenfield
Technologies"/"Sarah Chen Wei" (clean UBO graph) do **not** appear anywhere in Tier 2.

**Data-quality defect found (source file, not touched):** in `sample_opensanctions.csv`, the row
for `os-003401` (Dmitri Alexandrovich Sokolov) is missing one field delimiter — it has 4 empty
fields between `countries` and the next populated value where the 15-column header requires 5
(`addresses, identifiers, sanctions, phones, emails`). The result is a column shift for that one
row only: `phones` receives the `dataset` value, `emails` receives `first_seen`, `dataset`
receives `last_seen`, `first_seen` receives `last_change`, and `last_seen`/`last_change` parse as
null. Confirmed with a direct read of the raw line (see `scripts/profile_datasets.py` output).
Since Sokolov has no other-fixture cross-reference (§ audit doc §4.5), this doesn't affect any
demo narrative, but any real ingestion code must not assume this file is uniformly well-formed —
validate field counts per row rather than trusting the header's column count blindly.

---

## `data/articles/*.txt`

**Purpose:** Adverse-media source text fixtures. Plain text, no structured metadata — any
publication date, entity names, or context must be extracted from the prose itself.

| File | Bytes | Named entities | Content summary |
|---|---|---|---|
| `clean_article.txt` | 1,646 | Nordvale Dairy Cooperative, Kirsten Holmgaard | Benign business-expansion story. True negative — no risk content, no sanctions match. |
| `adverse_hit_article.txt` | 1,508 | Farid Hassan Abadi, Damascus Trading House LLC | Money-laundering/sanctions-evasion charges, dated "unsealed on July 8, 2026." True positive — both entities confirmed in Tier-2 sanctions data. |
| `adversarial_article.txt` | 1,792 | Golden Crescent Shipping Ltd, Ali Reza Hosseini, alias "A.R. Holdings" | Sanctions-circumvention investigation narrative. True positive (both named entities confirmed in Tier-2), **and contains a live prompt-injection payload** mid-text instructing a reader/system to zero out the risk score and treat the entity as cleared. The alias "A.R. Holdings" mentioned in prose does **not** appear in the structured alias data (`sample_ofac_alt.csv`) — a realistic gap that would require UBO-style investigation, not name matching, to uncover. |

---

## `data/ubo/simple_structure.json`

**Purpose:** Clean-control UBO ownership graph fixture.

```
entities[]:  { entity_id, name, entity_type, context, nationality, sector? }
ownership_edges[]: { owner_id, owned_id, percentage, description }
```

3 entities (Greenfield Technologies Pte Ltd — SG company; Greenfield Solutions EU GmbH — DE
company; Sarah Chen Wei — SG individual), 2 edges (100% and 65% ownership respectively). No entity
in this graph appears in either sanctions tier — verified negative control.

## `data/ubo/showcase_structure.json`

**Purpose:** Hidden-UBO demo fixture — a sanctioned individual concealed 3 ownership layers deep.

Same schema as above. 4 entities: Clean Corp Ltd (AE company) → Meridian Holdings International
(KY company, offshore holding) → Aegean Ventures Cyprus Ltd (CY company, "shell company with
minimal operations") → Mohammad Al-Rashid (individual, nationality UAE, `dob: "1975"`, described
in-file as "sanctioned"). 3 ownership edges: 80%, 60%, 100% respectively. **Confirmed:** Mohammad
Al-Rashid resolves exactly (DOB year + nationality) against the Tier-2 sanctions fixture entity
`AL-RASHID, Mohammad`. Effective ownership of Clean Corp Ltd by the sanctioned individual:
0.80 × 0.60 × 1.00 = **48%**.

---

## Out-of-scope files (documented, not profiled in depth)

| Path | Size | Note |
|---|---|---|
| `data/gdpr.json`, `data/gdpr_articles.csv` (+ identical copies in `data/gdpr_text/`) | ~630 KB total | GDPR article text corpus — unrelated privacy-compliance project artifact. |
| `data/opp115/` | 108 MB | OPP-115 privacy-policy annotation corpus (has its own `readme.txt`). Unrelated to KYC. |
| `data/privacy_qa/` | 82 MB | PrivacyQA corpus, has its own LICENSE/README. Unrelated to KYC. |
| `data/gcapi.dll` | 388 KB | Windows PE32 binary — not a data file at all. |
| `data/raw/`, `data/processed/`, `data/samples/`, `data/encrypted/` | 0 bytes | Empty, `.gitkeep` placeholders only. |

None of these were modified, moved, or deleted.
