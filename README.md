# Haber Customer Intelligence — Backend

## Setup

### 1. Install dependencies
```
pip install -r requirements.txt
```

### 2. Set up your .env file
Copy `.env.template` to `.env` and fill in all your API keys.
```
cp .env.template .env
```

### 3. Set up Supabase tables
Create these tables in your Supabase project:

**plants**
- id (int, primary key)
- customer_name (text)
- plant_name (text)
- location (text)
- haber_present (boolean)
- applications_running (text)
- readiness_status (text) — "Deployed" / "Under Assessment" / "Whitespace"

**stakeholders**
- id (int, primary key)
- customer_name (text)
- name (text)
- designation (text)
- team_type (text) — "CXO" / "Plant Head" / "IT" / "OT" / "Business Excellence"
- influence_level (text) — "High" / "Medium" / "Low"
- last_contacted (date)
- status (text) — "Active" / "Needs Re-engagement" / "New - Not Yet Contacted"

**cadence_log**
- id (int, primary key)
- customer_name (text)
- month (text) — format: "2025-05"
- task (text)
- completed (boolean)

**aep**
- id (int, primary key)
- customer_name (text)
- account_objective (text)
- operational_problems (text)
- marketing_notes (text)
- last_updated (date)

**expansion_pipeline**
- id (int, primary key)
- customer_name (text)
- opportunity_name (text)
- estimated_value_lakhs (float)
- source (text) — "AI Flagged" / "CAM Identified" / "Delivery Team"
- stage (text) — "Identified" / "In Discussion" / "Proposal Sent" / "Closed Won"
- date_identified (date)

**revenue**
- id (int, primary key)
- customer_name (text)
- year_type (text) — "year_1" or "current"
- amount (float) — in lakhs

### 4. Run locally
```
uvicorn main:app --reload
```
Server starts at http://localhost:8000

### 5. Test an endpoint
Open your browser and go to:
```
http://localhost:8000/docs
```
This shows all 7 endpoints with a test interface.

### 6. Deploy to Railway
- Push this folder to GitHub
- Connect Railway to your GitHub repo
- Add all .env keys in Railway → Variables
- Railway auto-deploys and gives you a live URL

## Endpoints

| Endpoint | What it does |
|---|---|
| POST /customer-overview | News intelligence + Haber implications |
| POST /stakeholder-map | Stakeholder list + role change alerts |
| POST /application-footprint | Plant footprint + whitespace analysis |
| POST /nrr | NRR calculation and trend |
| POST /expansion-pipeline | Existing pipeline + AI suggestions |
| POST /cadence-status | Monthly cadence checklist + health score |
| POST /generate-report | Full PDF report download |

All endpoints accept: `{ "customer_name": "ITC" }`

## Vault Integration
The Vault connector is currently a placeholder in `main.py`.
Once you get the API details from your tech team, search for
`fetch_from_vault` in main.py and update the URL and headers.
Then uncomment the vault_data lines in each endpoint.
