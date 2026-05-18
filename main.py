import os
import json
import httpx
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_LEFT, TA_CENTER

load_dotenv()
os.makedirs("temp", exist_ok=True)

app = FastAPI(title="Haber Customer Intelligence API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Clients ────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# VAULT (Haber's custom CRM) — fill these in once you get the API details
VAULT_BASE_URL = os.getenv("VAULT_BASE_URL")       # e.g. https://vault.haber.in/api
VAULT_API_KEY = os.getenv("VAULT_API_KEY")         # whatever auth Vault uses

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ─── Request Models ──────────────────────────────────────────────────────────

class CustomerRequest(BaseModel):
    customer_name: str


# ─── Helper: Call Claude ─────────────────────────────────────────────────────

async def call_claude(prompt: str, system: str = "") -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 2000,
                "system": system or "You are a B2B intelligence analyst for Haber, a water treatment and industrial solutions company. Always tie your analysis back to what it means for Haber as a vendor.",
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        data = response.json()
        return data["content"][0]["text"]


# ─── Helper: Tavily Search ───────────────────────────────────────────────────

async def tavily_search(query: str, max_results: int = 5) -> list:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "max_results": max_results,
                "search_depth": "advanced",
            },
        )
        data = response.json()
        return data.get("results", [])


# ─── Helper: Fetch from Vault ────────────────────────────────────────────────
# NOTE: This is a placeholder. Once you get Vault API details from your tech
# team, replace the URL and headers below with the real ones.

async def fetch_from_vault(endpoint: str) -> dict:
    """
    Replace this with real Vault API details once confirmed.
    endpoint example: /customers/ITC or /contacts/ITC
    """
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{VAULT_BASE_URL}{endpoint}",
            headers={
                "Authorization": f"Bearer {VAULT_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Vault API error: {response.text}")
        return response.json()


# ════════════════════════════════════════════════════════════════════════════
# ENDPOINT 1: /customer-overview
# ════════════════════════════════════════════════════════════════════════════

@app.post("/customer-overview")
async def customer_overview(req: CustomerRequest):
    name = req.customer_name

    # Step 1: Fetch from Vault
    # TODO: Replace with real Vault endpoint once confirmed
    # vault_data = await fetch_from_vault(f"/customers/{name}")
    # For now using placeholder so the rest works
    vault_data = {
        "company": name,
        "industry": "To be fetched from Vault",
        "contract_value": "To be fetched from Vault",
        "renewal_date": "To be fetched from Vault",
        "account_manager": "To be fetched from Vault",
    }

    # Step 2: Search for news
    news_results = await tavily_search(f"{name} news 2025")
    ma_results = await tavily_search(f"{name} merger acquisition expansion 2025")
    headcount_results = await tavily_search(f"{name} leadership appointment hiring layoff 2025")

    all_news = news_results + ma_results + headcount_results
    news_text = "\n".join([f"- {r['title']}: {r.get('content', '')[:300]}" for r in all_news])

    # Step 3: Ask Claude to categorize and interpret
    prompt = f"""
Here is recent news about {name}, one of Haber's enterprise customers:

{news_text}

Haber's CRM data for this customer:
{json.dumps(vault_data, indent=2)}

Please do two things:

1. CATEGORIZE the news into these sections (only include sections that have relevant news):
   - Mergers & Acquisitions
   - Major Trends
   - Minor Trends
   - Key Headcount Changes
   - Risks & Regulatory Changes
   - Expansions & New Ventures

   For each item: write the headline, assign a category tag, note the source if available.

2. WHAT THIS MEANS FOR HABER: Write 3-5 bullet points explaining what these developments mean specifically for Haber as a water treatment vendor to this company. Be specific — mention plants, contracts, compliance angles, upsell opportunities.

Return as JSON with keys: "categorized_news" (object with category arrays) and "haber_implications" (array of strings).
"""

    result = await call_claude(prompt)

    try:
        parsed = json.loads(result)
    except Exception:
        parsed = {"raw": result}

    return {
        "customer": name,
        "vault_data": vault_data,
        "intelligence": parsed,
    }


# ════════════════════════════════════════════════════════════════════════════
# ENDPOINT 2: /stakeholder-map
# ════════════════════════════════════════════════════════════════════════════

@app.post("/stakeholder-map")
async def stakeholder_map(req: CustomerRequest):
    name = req.customer_name

    # Step 1: Fetch contacts from Vault
    # TODO: Replace with real Vault endpoint
    # vault_contacts = await fetch_from_vault(f"/contacts/{name}")
    vault_contacts = []  # will be replaced with real Vault data

    # Step 2: Fetch stakeholders stored in Supabase
    supabase_stakeholders = supabase.table("stakeholders") \
        .select("*") \
        .eq("customer_name", name) \
        .execute()
    stakeholders = supabase_stakeholders.data or []

    # Step 3: Search for leadership changes
    leadership_news = await tavily_search(f"{name} CEO CFO CXO appointment resignation leadership change 2025", max_results=5)
    news_text = "\n".join([f"- {r['title']}: {r.get('content', '')[:200]}" for r in leadership_news])

    # Step 4: Ask Claude to flag any role changes
    prompt = f"""
Here are the current stakeholders Haber tracks at {name}:
{json.dumps(stakeholders, indent=2)}

Here is recent news about leadership changes at {name}:
{news_text}

For each stakeholder, check if the news suggests their role may have changed.
Also flag any new senior appointments mentioned in the news that Haber should be aware of.

Return as JSON with keys:
- "stakeholders": array of existing stakeholders, each with a "role_change_flag" boolean and "flag_reason" string if flagged
- "new_appointments": array of new people mentioned in news with name, role, and why Haber should reach out
"""

    result = await call_claude(prompt)

    try:
        parsed = json.loads(result)
    except Exception:
        parsed = {"raw": result}

    return {
        "customer": name,
        "stakeholder_intelligence": parsed,
    }


# ════════════════════════════════════════════════════════════════════════════
# ENDPOINT 3: /application-footprint
# ════════════════════════════════════════════════════════════════════════════

@app.post("/application-footprint")
async def application_footprint(req: CustomerRequest):
    name = req.customer_name

    # Step 1: Fetch plant data from Supabase
    plants_result = supabase.table("plants") \
        .select("*") \
        .eq("customer_name", name) \
        .execute()
    plants = plants_result.data or []

    # Step 2: Search for new plants or expansions
    expansion_news = await tavily_search(f"{name} new plant factory expansion acquisition site 2025", max_results=5)
    news_text = "\n".join([f"- {r['title']}: {r.get('content', '')[:200]}" for r in expansion_news])

    # Step 3: Ask Claude for whitespace analysis
    prompt = f"""
Here is Haber's current deployment footprint at {name}:
{json.dumps(plants, indent=2)}

Here is recent news about {name}'s expansion or new facilities:
{news_text}

Please:
1. Identify which of the news items suggest NEW plants or sites not currently in Haber's footprint
2. Rank the top 3 whitespace opportunities — where Haber should target next and why
3. For each whitespace opportunity, explain the business case for Haber specifically

Return as JSON with keys:
- "current_footprint": the plants array with a summary
- "whitespace_opportunities": array of objects with plant_name, location, reason, priority (High/Medium/Low)
- "whitespace_summary": 2-3 sentence summary for the dashboard card
"""

    result = await call_claude(prompt)

    try:
        parsed = json.loads(result)
    except Exception:
        parsed = {"raw": result}

    return {
        "customer": name,
        "footprint_intelligence": parsed,
        "plants": plants,
    }


# ════════════════════════════════════════════════════════════════════════════
# ENDPOINT 4: /nrr
# ════════════════════════════════════════════════════════════════════════════

@app.post("/nrr")
async def nrr(req: CustomerRequest):
    name = req.customer_name

    # Step 1: Fetch revenue data from Supabase
    revenue_result = supabase.table("revenue") \
        .select("*") \
        .eq("customer_name", name) \
        .execute()
    revenue_data = revenue_result.data or []

    if not revenue_data:
        return {
            "customer": name,
            "error": "No revenue data found in Supabase for this customer. Please add year 1 and current year revenue.",
        }

    # Step 2: Calculate NRR
    year1 = next((r["amount"] for r in revenue_data if r["year_type"] == "year_1"), None)
    current = next((r["amount"] for r in revenue_data if r["year_type"] == "current"), None)

    if not year1 or not current:
        return {"customer": name, "error": "Missing year_1 or current revenue entry in Supabase."}

    nrr_percent = round((current / year1) * 100, 1)
    trend = "growing" if nrr_percent >= 100 else "declining"

    return {
        "customer": name,
        "year_1_revenue": year1,
        "current_revenue": current,
        "nrr_percent": nrr_percent,
        "trend": trend,
        "trend_arrow": "↑" if trend == "growing" else "↓",
    }


# ════════════════════════════════════════════════════════════════════════════
# ENDPOINT 5: /expansion-pipeline
# ════════════════════════════════════════════════════════════════════════════

@app.post("/expansion-pipeline")
async def expansion_pipeline(req: CustomerRequest):
    name = req.customer_name

    # Step 1: Fetch existing pipeline from Supabase
    pipeline_result = supabase.table("expansion_pipeline") \
        .select("*") \
        .eq("customer_name", name) \
        .execute()
    existing_pipeline = pipeline_result.data or []

    # Step 2: Fetch plants for whitespace context
    plants_result = supabase.table("plants") \
        .select("*") \
        .eq("customer_name", name) \
        .execute()
    plants = plants_result.data or []

    # Step 3: Search for expansion signals
    expansion_news = await tavily_search(f"{name} expansion new plant acquisition investment 2025", max_results=5)
    news_text = "\n".join([f"- {r['title']}: {r.get('content', '')[:200]}" for r in expansion_news])

    # Step 4: Ask Claude to suggest new opportunities
    prompt = f"""
Haber's existing expansion pipeline for {name}:
{json.dumps(existing_pipeline, indent=2)}

Current plant footprint (where Haber is and isn't deployed):
{json.dumps(plants, indent=2)}

Recent expansion news about {name}:
{news_text}

Based on the whitespace and news, suggest 2-3 NEW expansion opportunities for Haber that are NOT already in the pipeline.
For each, explain: what the opportunity is, estimated deal potential, why now is the right time, and what Haber should do first.

Return as JSON with key "suggested_opportunities": array of objects with:
- opportunity_name
- estimated_value_lakhs
- reason
- first_action
- priority (High/Medium/Low)
"""

    result = await call_claude(prompt)

    try:
        parsed = json.loads(result)
    except Exception:
        parsed = {"raw": result}

    return {
        "customer": name,
        "existing_pipeline": existing_pipeline,
        "ai_suggestions": parsed,
    }


# ════════════════════════════════════════════════════════════════════════════
# ENDPOINT 6: /cadence-status
# ════════════════════════════════════════════════════════════════════════════

@app.post("/cadence-status")
async def cadence_status(req: CustomerRequest):
    name = req.customer_name
    current_month = datetime.now().strftime("%Y-%m")

    # Fetch cadence log from Supabase
    cadence_result = supabase.table("cadence_log") \
        .select("*") \
        .eq("customer_name", name) \
        .eq("month", current_month) \
        .execute()
    cadence_data = cadence_result.data or []

    # Define all required tasks
    monthly_tasks = [
        "stakeholder_map_reviewed",
        "two_stakeholder_engagements_logged",
        "application_outcomes_captured",
        "technical_templates_updated",
        "site_readiness_assessed",
        "new_use_cases_identified",
    ]
    bimonthly_tasks = ["governance_review_held"]
    quarterly_tasks = ["aep_updated", "expansion_opportunities_progressed", "enterprise_value_review_presented"]

    # Check which are done
    completed = {entry["task"]: entry["completed"] for entry in cadence_data}

    monthly_status = [{"task": t, "completed": completed.get(t, False)} for t in monthly_tasks]
    bimonthly_status = [{"task": t, "completed": completed.get(t, False)} for t in bimonthly_tasks]
    quarterly_status = [{"task": t, "completed": completed.get(t, False)} for t in quarterly_tasks]

    # Calculate health score based on monthly tasks only
    completed_count = sum(1 for t in monthly_status if t["completed"])
    health_score = round((completed_count / len(monthly_tasks)) * 100)

    return {
        "customer": name,
        "month": current_month,
        "monthly_tasks": monthly_status,
        "bimonthly_tasks": bimonthly_status,
        "quarterly_tasks": quarterly_status,
        "cadence_health_score": health_score,
        "health_label": f"{health_score}% — {completed_count} of {len(monthly_tasks)} monthly tasks completed",
    }


# ════════════════════════════════════════════════════════════════════════════
# ENDPOINT 7: /generate-report
# ════════════════════════════════════════════════════════════════════════════

@app.post("/generate-report")
async def generate_report(req: CustomerRequest):
    name = req.customer_name

    # Step 1: Gather all data by calling internal logic
    overview_req = CustomerRequest(customer_name=name)
    overview = await customer_overview(overview_req)
    footprint = await application_footprint(overview_req)
    pipeline = await expansion_pipeline(overview_req)
    cadence = await cadence_status(overview_req)
    nrr_data = await nrr(overview_req)
    stakeholders = await stakeholder_map(overview_req)

    # Step 2: Ask Claude to write the full report
    report_prompt = f"""
Write a comprehensive Customer Intelligence Report for {name} for Haber's Corporate Account Manager.

Use this data:

OVERVIEW & NEWS INTELLIGENCE:
{json.dumps(overview.get("intelligence", {}), indent=2)}

NRR & FINANCIALS:
{json.dumps(nrr_data, indent=2)}

STAKEHOLDER MAP:
{json.dumps(stakeholders.get("stakeholder_intelligence", {}), indent=2)}

APPLICATION FOOTPRINT:
{json.dumps(footprint.get("footprint_intelligence", {}), indent=2)}

EXPANSION PIPELINE:
{json.dumps(pipeline, indent=2)}

CADENCE HEALTH:
{json.dumps(cadence, indent=2)}

Write the report with these exact sections:
1. Executive Summary (3-4 sentences capturing the most important things happening)
2. Account Health & NRR (financials snapshot and what the trend means)
3. Stakeholder Update (who to engage, who has changed, who is new)
4. What's Happening in Their World (key developments across M&A, trends, headcount, risks, expansions)
5. What This Means for Haber (specific implications and opportunities)
6. Application Footprint & Whitespace (where Haber is and where it should go next)
7. Expansion Opportunities (pipeline summary and top new suggestions)
8. Risk Flags (anything that could threaten the relationship or renewal)
9. Cadence Health (what's been done this month and what's overdue)
10. Recommended Next Actions for the CAM (top 3 specific actions to take this week)

Be specific, professional, and actionable. Write for a senior account manager who needs to act on this.
"""

    report_text = await call_claude(report_prompt, system="You are a senior B2B intelligence analyst writing a formal account report for Haber, a water treatment company. Be specific, professional, and concise.")

    # Step 3: Generate PDF using ReportLab
    filename = f"temp/{name.replace(' ', '_')}_Report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"

    doc = SimpleDocTemplate(filename, pagesize=A4,
                            rightMargin=0.75*inch, leftMargin=0.75*inch,
                            topMargin=0.75*inch, bottomMargin=0.75*inch)

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle("Title", parent=styles["Title"],
                                  fontSize=20, textColor=colors.HexColor("#1e3a5f"),
                                  spaceAfter=6)
    subtitle_style = ParagraphStyle("Subtitle", parent=styles["Normal"],
                                     fontSize=11, textColor=colors.HexColor("#64748b"),
                                     spaceAfter=20)
    heading_style = ParagraphStyle("Heading", parent=styles["Heading2"],
                                    fontSize=13, textColor=colors.HexColor("#1e3a5f"),
                                    spaceBefore=16, spaceAfter=6,
                                    borderPad=4)
    body_style = ParagraphStyle("Body", parent=styles["Normal"],
                                 fontSize=10, leading=16,
                                 textColor=colors.HexColor("#334155"),
                                 spaceAfter=8)

    elements = []

    # Header
    elements.append(Paragraph(f"Customer Intelligence Report", title_style))
    elements.append(Paragraph(f"{name} — Generated {datetime.now().strftime('%d %B %Y')}", subtitle_style))
    elements.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#1e3a5f")))
    elements.append(Spacer(1, 0.2*inch))

    # NRR snapshot table
    if "nrr_percent" in nrr_data:
        nrr_table_data = [
            ["Year 1 Revenue", "Current Revenue", "NRR", "Trend"],
            [
                f"₹{nrr_data.get('year_1_revenue', 'N/A')}L",
                f"₹{nrr_data.get('current_revenue', 'N/A')}L",
                f"{nrr_data.get('nrr_percent', 'N/A')}%",
                nrr_data.get('trend_arrow', '—'),
            ]
        ]
        t = Table(nrr_table_data, colWidths=[1.5*inch, 1.5*inch, 1.2*inch, 1.2*inch])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f0f4f8"), colors.white]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 0.2*inch))

    # Report sections from Claude
    sections = report_text.split("\n")
    for line in sections:
        line = line.strip()
        if not line:
            elements.append(Spacer(1, 0.05*inch))
            continue
        # Detect section headings (numbered or all caps or starts with #)
        if (line[0].isdigit() and ". " in line[:4]) or line.startswith("#"):
            clean = line.lstrip("#").lstrip("0123456789. ").strip()
            elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e2e8f0")))
            elements.append(Paragraph(clean, heading_style))
        else:
            elements.append(Paragraph(line, body_style))

    # Footer note
    elements.append(Spacer(1, 0.3*inch))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0")))
    elements.append(Paragraph(
        f"Generated by Haber Customer Intelligence Dashboard • {datetime.now().strftime('%d %B %Y %H:%M')} • Confidential",
        ParagraphStyle("Footer", parent=styles["Normal"], fontSize=8,
                       textColor=colors.HexColor("#94a3b8"), alignment=TA_CENTER, spaceBefore=8)
    ))

    doc.build(elements)

    return FileResponse(
        path=filename,
        filename=f"{name}_Intelligence_Report.pdf",
        media_type="application/pdf"
    )


# ─── Health Check ────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "Haber Intelligence API is running"}
