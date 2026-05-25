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

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
VAULT_BASE_URL = os.getenv("VAULT_BASE_URL")
VAULT_API_KEY = os.getenv("VAULT_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

class CustomerRequest(BaseModel):
    customer_name: str

async def call_gemini(prompt: str, system: str = "") -> str:
    import asyncio
    models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "gemma2-9b-it"]
    for model in models:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system or "You are a B2B intelligence analyst for Haber, a water treatment company."},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 2000,
                    "temperature": 0.7,
                },
            )
            data = response.json()
            if "choices" in data:
                return data["choices"][0]["message"]["content"]
            if "rate_limit" in str(data.get("error", {}).get("code", "")):
                await asyncio.sleep(3)
                continue
            raise HTTPException(status_code=500, detail=f"Groq error: {json.dumps(data)}")
    raise HTTPException(status_code=500, detail="All Groq models rate limited. Try again in 1 minute.")

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

async def cached_gemini(cache_key: str, prompt: str, system: str = "") -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    full_key = f"{cache_key}_{today}"
    try:
        result = supabase.table("ai_cache").select("response").eq("cache_key", full_key).execute()
        if result.data:
            return result.data[0]["response"]
    except Exception:
        pass
    response = await call_gemini(prompt, system)
    try:
        supabase.table("ai_cache").upsert({"cache_key": full_key, "response": response}).execute()
    except Exception:
        pass
    return response

@app.post("/customer-overview")
async def customer_overview(req: CustomerRequest):
    name = req.customer_name
    vault_data = {
        "company": name,
        "industry": "To be fetched from Vault",
        "contract_value": "To be fetched from Vault",
        "renewal_date": "To be fetched from Vault",
        "account_manager": "To be fetched from Vault",
    }
    news_results = await tavily_search(f"{name} Limited India news 2026")
    ma_results = await tavily_search(f"{name} Limited India merger acquisition expansion 2026")
    headcount_results = await tavily_search(f"{name} Limited India leadership appointment hiring 2026")
    all_news = news_results + ma_results + headcount_results
    news_text = "\n".join([f"- {r['title']}: {r.get('content', '')[:300]}" for r in all_news])
    system = "You are a B2B intelligence analyst for Haber, a water treatment and industrial solutions company. Always tie your analysis back to what it means for Haber as a vendor."
    prompt = f"""
Here is recent news about {name}, one of Haber's enterprise customers:
{news_text}

Haber's CRM data for this customer:
{json.dumps(vault_data, indent=2)}

Please do two things:
1. CATEGORIZE the news into these sections (only include sections with relevant news):
   - Mergers & Acquisitions
   - Major Trends
   - Minor Trends
   - Key Headcount Changes
   - Risks & Regulatory Changes
   - Expansions & New Ventures
   For each item: write the headline, assign a category tag, note the source if available.

2. WHAT THIS MEANS FOR HABER: Write 3-5 bullet points explaining what these developments mean specifically for Haber as a water treatment vendor to this company.

Return as JSON with keys: "categorized_news" (object with category arrays) and "haber_implications" (array of strings).
Only return valid JSON, no extra text or markdown.
"""
    result = await cached_gemini(f"{name}_overview", prompt, system)
    result = result.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        parsed = json.loads(result)
    except Exception:
        parsed = {"raw": result}
    return {"customer": name, "vault_data": vault_data, "intelligence": parsed}

@app.post("/stakeholder-map")
async def stakeholder_map(req: CustomerRequest):
    name = req.customer_name
    supabase_stakeholders = supabase.table("stakeholders").select("*").eq("customer_name", name).execute()
    stakeholders = supabase_stakeholders.data or []
    leadership_news = await tavily_search(f"{name} Limited India CEO CFO CXO appointment resignation 2026", max_results=5)
    news_text = "\n".join([f"- {r['title']}: {r.get('content', '')[:200]}" for r in leadership_news])
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
Only return valid JSON, no extra text or markdown.
"""
    result = await cached_gemini(f"{name}_stakeholder", prompt)
    result = result.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        parsed = json.loads(result)
    except Exception:
        parsed = {"raw": result}
    return {"customer": name, "stakeholder_intelligence": parsed}

@app.post("/application-footprint")
async def application_footprint(req: CustomerRequest):
    name = req.customer_name
    plants_result = supabase.table("plants").select("*").eq("customer_name", name).execute()
    plants = plants_result.data or []
    expansion_news = await tavily_search(f"{name} Limited India new plant factory expansion 2026", max_results=5)
    news_text = "\n".join([f"- {r['title']}: {r.get('content', '')[:200]}" for r in expansion_news])
    prompt = f"""
Here is Haber's current deployment footprint at {name}:
{json.dumps(plants, indent=2)}

Here is recent news about {name}'s expansion or new facilities:
{news_text}

Please:
1. Identify which news items suggest NEW plants or sites not currently in Haber's footprint
2. Rank the top 3 whitespace opportunities
3. For each whitespace opportunity, explain the business case for Haber specifically

Return as JSON with keys:
- "current_footprint": the plants array with a summary
- "whitespace_opportunities": array of objects with plant_name, location, reason, priority (High/Medium/Low)
- "whitespace_summary": 2-3 sentence summary
Only return valid JSON, no extra text or markdown.
"""
    result = await cached_gemini(f"{name}_footprint", prompt)
    result = result.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        parsed = json.loads(result)
    except Exception:
        parsed = {"raw": result}
    return {"customer": name, "footprint_intelligence": parsed, "plants": plants}

@app.post("/nrr")
async def nrr(req: CustomerRequest):
    name = req.customer_name
    revenue_result = supabase.table("revenue").select("*").eq("customer_name", name).execute()
    revenue_data = revenue_result.data or []
    if not revenue_data:
        return {"customer": name, "error": "No revenue data found in Supabase for this customer."}
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
        "trend_arrow": "up" if trend == "growing" else "down",
    }

@app.post("/expansion-pipeline")
async def expansion_pipeline(req: CustomerRequest):
    name = req.customer_name
    pipeline_result = supabase.table("expansion_pipeline").select("*").eq("customer_name", name).execute()
    existing_pipeline = pipeline_result.data or []
    plants_result = supabase.table("plants").select("*").eq("customer_name", name).execute()
    plants = plants_result.data or []
    expansion_news = await tavily_search(f"{name} Limited India expansion new plant acquisition 2026", max_results=5)
    news_text = "\n".join([f"- {r['title']}: {r.get('content', '')[:200]}" for r in expansion_news])
    prompt = f"""
Haber's existing expansion pipeline for {name}:
{json.dumps(existing_pipeline, indent=2)}

Current plant footprint:
{json.dumps(plants, indent=2)}

Recent expansion news about {name}:
{news_text}

Suggest 2-3 NEW expansion opportunities for Haber not already in the pipeline.
Return as JSON with key "suggested_opportunities": array of objects with:
- opportunity_name, estimated_value_lakhs, reason, first_action, priority (High/Medium/Low)
Only return valid JSON, no extra text or markdown.
"""
    result = await cached_gemini(f"{name}_pipeline", prompt)
    result = result.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        parsed = json.loads(result)
    except Exception:
        parsed = {"raw": result}
    return {"customer": name, "existing_pipeline": existing_pipeline, "ai_suggestions": parsed}

@app.post("/cadence-status")
async def cadence_status(req: CustomerRequest):
    name = req.customer_name
    current_month = datetime.now().strftime("%Y-%m")
    cadence_result = supabase.table("cadence_log").select("*").eq("customer_name", name).eq("month", current_month).execute()
    cadence_data = cadence_result.data or []
    monthly_tasks = ["stakeholder_map_reviewed", "two_stakeholder_engagements_logged", "application_outcomes_captured", "technical_templates_updated", "site_readiness_assessed", "new_use_cases_identified"]
    bimonthly_tasks = ["governance_review_held"]
    quarterly_tasks = ["aep_updated", "expansion_opportunities_progressed", "enterprise_value_review_presented"]
    completed = {entry["task"]: entry["completed"] for entry in cadence_data}
    monthly_status = [{"task": t, "completed": completed.get(t, False)} for t in monthly_tasks]
    bimonthly_status = [{"task": t, "completed": completed.get(t, False)} for t in bimonthly_tasks]
    quarterly_status = [{"task": t, "completed": completed.get(t, False)} for t in quarterly_tasks]
    completed_count = sum(1 for t in monthly_status if t["completed"])
    health_score = round((completed_count / len(monthly_tasks)) * 100)
    return {
        "customer": name,
        "month": current_month,
        "monthly_tasks": monthly_status,
        "bimonthly_tasks": bimonthly_status,
        "quarterly_tasks": quarterly_status,
        "cadence_health_score": health_score,
        "health_label": f"{health_score}% - {completed_count} of {len(monthly_tasks)} monthly tasks completed",
    }

@app.post("/generate-report")
async def generate_report(req: CustomerRequest):
    name = req.customer_name
    overview_req = CustomerRequest(customer_name=name)
    overview = await customer_overview(overview_req)
    footprint = await application_footprint(overview_req)
    pipeline = await expansion_pipeline(overview_req)
    cadence = await cadence_status(overview_req)
    nrr_data = await nrr(overview_req)
    stakeholders = await stakeholder_map(overview_req)

    system = "You are a senior B2B intelligence analyst for Haber. Be specific, direct, and action-oriented. Return only valid JSON. Today is 21 May 2026. All timeframes like THIS WEEK or THIS MONTH must be relative to this date."
    report_prompt = f"""
You are a senior B2B intelligence analyst for Haber, a water treatment and process chemicals company.
Write a Customer Intelligence Report for {name} based on this data:

NEWS INTELLIGENCE:
{json.dumps(overview.get("intelligence", {}), indent=2)}

STAKEHOLDER MAP:
{json.dumps(stakeholders.get("stakeholder_intelligence", {}), indent=2)}

APPLICATION FOOTPRINT:
{json.dumps(footprint.get("footprint_intelligence", {}), indent=2)}

EXPANSION PIPELINE:
{json.dumps(pipeline, indent=2)}

IMPORTANT: Sort the signals array by date with most recent first. January=1, February=2, March=3, April=4, May=5, June=6 etc.

Return ONLY valid JSON with this exact structure:
{{
  "customer_context": "4 sentences about who this company is and their relationship with Haber",
  "signals": [
    {{
      "date": "Month Year",
      "recency": "RECENT or ONGOING",
      "headline": "One line headline",
      "detail": "2-3 sentences with source",
      "source": "Source name",
      "implication": "What this means for Haber",
      "action": "Specific action Haber must take"
    }}
  ],
  "implications_table": [
    {{
      "signal": "Signal name",
      "date": "Date",
      "opportunity": "Opportunity for Haber",
      "risk_if_no_action": "Risk if Haber does nothing"
    }}
  ],
  "risk_flags": [
    {{
      "risk": "Risk name",
      "severity": "HIGH or Medium or Low",
      "action": "What Haber should do"
    }}
  ],
  "recommended_actions": [
    {{
      "priority": "THIS WEEK or THIS MONTH or THIS QUARTER",
      "action": "Specific action",
      "because": "Why tied to a signal",
      
    }}
  ]
}}
"""
    report_json_str = await cached_gemini(f"{name}_report", report_prompt, system)
    report_json_str = report_json_str.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        report_data = json.loads(report_json_str)
    except Exception:
        report_data = {"customer_context": report_json_str[:500], "signals": [], "implications_table": [], "risk_flags": [], "recommended_actions": []}

    filename = f"temp/{name.replace(' ', '_')}_Report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    doc = SimpleDocTemplate(filename, pagesize=A4, rightMargin=0.6*inch, leftMargin=0.6*inch, topMargin=0.6*inch, bottomMargin=0.6*inch)
    styles = getSampleStyleSheet()

    dark_navy = colors.HexColor("#0f1f3d")
    mid_navy = colors.HexColor("#1e3a5f")
    light_grey = colors.HexColor("#f4f6f9")
    amber = colors.HexColor("#b45309")
    red = colors.HexColor("#dc2626")
    green = colors.HexColor("#15803d")
    text_dark = colors.HexColor("#1e293b")
    text_muted = colors.HexColor("#64748b")
    page_width = A4[0] - 1.2*inch

    header_label = ParagraphStyle("HeaderLabel", parent=styles["Normal"], fontSize=7, textColor=colors.white, fontName="Helvetica", spaceAfter=2)
    section_num = ParagraphStyle("SectionNum", parent=styles["Normal"], fontSize=22, textColor=colors.HexColor("#cbd5e1"), fontName="Helvetica-Bold")
    section_title_style = ParagraphStyle("SectionTitle", parent=styles["Normal"], fontSize=13, textColor=mid_navy, fontName="Helvetica-Bold", spaceBefore=4)
    section_sub = ParagraphStyle("SectionSub", parent=styles["Normal"], fontSize=8, textColor=text_muted, fontName="Helvetica", spaceAfter=8)
    body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=9.5, leading=15, textColor=text_dark, fontName="Helvetica", spaceAfter=6)
    signal_date_style = ParagraphStyle("SignalDate", parent=styles["Normal"], fontSize=8, textColor=text_muted, fontName="Helvetica-Bold", spaceAfter=2)
    signal_headline = ParagraphStyle("SignalHeadline", parent=styles["Normal"], fontSize=10.5, textColor=text_dark, fontName="Helvetica-Bold", spaceAfter=4)
    signal_detail = ParagraphStyle("SignalDetail", parent=styles["Normal"], fontSize=9, leading=14, textColor=text_dark, fontName="Helvetica", spaceAfter=6)
    implication_label = ParagraphStyle("ImplicationLabel", parent=styles["Normal"], fontSize=8, textColor=mid_navy, fontName="Helvetica-Bold", spaceAfter=2)
    implication_text = ParagraphStyle("ImplicationText", parent=styles["Normal"], fontSize=9, leading=13, textColor=text_dark, fontName="Helvetica", spaceAfter=4)
    action_label = ParagraphStyle("ActionLabel", parent=styles["Normal"], fontSize=8, textColor=amber, fontName="Helvetica-Bold", spaceAfter=2)
    footer_style = ParagraphStyle("Footer", parent=styles["Normal"], fontSize=7.5, textColor=text_muted, fontName="Helvetica", alignment=TA_CENTER)

    elements = []

    header_data = [[Paragraph("CUSTOMER INTELLIGENCE REPORT", header_label), Paragraph("Intelligence-First  |  News &amp; Signals Only", header_label)]]
    header_table = Table(header_data, colWidths=[page_width*0.6, page_width*0.4])
    header_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), dark_navy), ("TOPPADDING", (0, 0), (-1, -1), 14), ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (0, -1), 14), ("RIGHTPADDING", (-1, 0), (-1, -1), 14), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (-1, 0), (-1, -1), "RIGHT")]))
    elements.append(header_table)

    company_data = [[Paragraph(f"<b>{name}</b>", ParagraphStyle("Co", parent=styles["Normal"], fontSize=15, textColor=colors.white, fontName="Helvetica-Bold")), Paragraph(f"Report Date: {datetime.now().strftime('%B %Y')}", ParagraphStyle("Date", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#94a3b8"), fontName="Helvetica"))]]
    company_table = Table(company_data, colWidths=[page_width*0.7, page_width*0.3])
    company_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), dark_navy), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 14), ("LEFTPADDING", (0, 0), (0, -1), 14), ("RIGHTPADDING", (-1, 0), (-1, -1), 14), ("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("ALIGN", (-1, 0), (-1, -1), "RIGHT")]))
    elements.append(company_table)
    elements.append(Spacer(1, 0.25*inch))

    def section_header(num, title, subtitle):
        data = [[Paragraph(num, section_num), [Paragraph(title, section_title_style), Paragraph(subtitle, section_sub)]]]
        t = Table(data, colWidths=[0.6*inch, page_width - 0.6*inch])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0), ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#e2e8f0"))]))
        return t

    elements.append(section_header("01", "Customer Context", "4 lines to frame the account"))
    elements.append(Spacer(1, 0.1*inch))
    context_box = Table([[Paragraph(report_data.get("customer_context", ""), body)]], colWidths=[page_width])
    context_box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), light_grey), ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10), ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12)]))
    elements.append(context_box)
    elements.append(Spacer(1, 0.25*inch))

    elements.append(section_header("02", "Intelligence Feed", "Real signals with dates. More recent = more actionable."))
    elements.append(Spacer(1, 0.12*inch))

    for signal in report_data.get("signals", []):
        recency = signal.get("recency", "ONGOING")
        recency_color = green if recency == "RECENT" else colors.HexColor("#1d4ed8")
        signal_content = [
            Paragraph(f"{signal.get('date', '')}   <b>{recency}</b>", signal_date_style),
            Paragraph(signal.get("headline", ""), signal_headline),
            Paragraph(signal.get("detail", ""), signal_detail),
            Paragraph(f"Source: {signal.get('source', '')}", ParagraphStyle("Src", parent=styles["Normal"], fontSize=8, textColor=text_muted, fontName="Helvetica-Oblique", spaceAfter=8)),
            Paragraph("IMPLICATION FOR US", implication_label),
            Paragraph(signal.get("implication", ""), implication_text),
            Paragraph("ACTION REQUIRED", action_label),
            Paragraph(signal.get("action", ""), implication_text),
        ]
        card = Table([[signal_content]], colWidths=[page_width])
        card.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), light_grey), ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10), ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14), ("LINEBEFORE", (0, 0), (0, -1), 3, recency_color)]))
        elements.append(card)
        elements.append(Spacer(1, 0.1*inch))

    elements.append(Spacer(1, 0.15*inch))
    elements.append(section_header("03", "What This Means for Us", "Each signal mapped to a business implication"))
    elements.append(Spacer(1, 0.12*inch))

    if report_data.get("implications_table"):
        th_style = ParagraphStyle("TH", parent=styles["Normal"], fontSize=9, textColor=colors.white, fontName="Helvetica-Bold")
        impl_rows = [[Paragraph("<b>Signal</b>", th_style), Paragraph("<b>Date</b>", th_style), Paragraph("<b>Opportunity Window</b>", th_style), Paragraph("<b>Risk if We Don't Act</b>", th_style)]]
        for row in report_data.get("implications_table", []):
            impl_rows.append([Paragraph(row.get("signal", ""), body), Paragraph(row.get("date", ""), body), Paragraph(row.get("opportunity", ""), body), Paragraph(row.get("risk_if_no_action", ""), body)])
        col_w = page_width / 4
        impl_table = Table(impl_rows, colWidths=[col_w*1.2, col_w*0.6, col_w*1.1, col_w*1.1])
        impl_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), mid_navy), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, light_grey]), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        elements.append(impl_table)

    elements.append(Spacer(1, 0.25*inch))
    elements.append(section_header("04", "Risk Flags", "News-derived risks only"))
    elements.append(Spacer(1, 0.12*inch))

    if report_data.get("risk_flags"):
        th_style = ParagraphStyle("TH", parent=styles["Normal"], fontSize=9, textColor=colors.white, fontName="Helvetica-Bold")
        risk_rows = [[Paragraph("<b>Risk</b>", th_style), Paragraph("<b>Severity</b>", th_style), Paragraph("<b>What We Should Do</b>", th_style)]]
        for row in report_data.get("risk_flags", []):
            sev = row.get("severity", "Medium")
            sev_color = red if sev == "HIGH" else amber if sev == "Medium" else green
            risk_rows.append([Paragraph(f"<b>{row.get('risk', '')}</b>", body), Paragraph(f"<b>{sev}</b>", ParagraphStyle("Sev", parent=styles["Normal"], fontSize=9, textColor=sev_color, fontName="Helvetica-Bold")), Paragraph(row.get("action", ""), body)])
        risk_table = Table(risk_rows, colWidths=[page_width*0.35, page_width*0.15, page_width*0.5])
        risk_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), mid_navy), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, light_grey]), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        elements.append(risk_table)

    elements.append(Spacer(1, 0.25*inch))
    elements.append(section_header("05", "Recommended Actions", "Every action tied to a specific signal with a deadline"))
    elements.append(Spacer(1, 0.12*inch))

    if report_data.get("recommended_actions"):
        th_style = ParagraphStyle("TH", parent=styles["Normal"], fontSize=9, textColor=colors.white, fontName="Helvetica-Bold")
        action_rows = [[Paragraph("<b>Priority</b>", th_style), Paragraph("<b>Action</b>", th_style), Paragraph("<b>Because</b>", th_style)]]
        for row in report_data.get("recommended_actions", []):
            pri = row.get("priority", "THIS MONTH")
            pri_color = red if "WEEK" in pri else amber if "MONTH" in pri else mid_navy
            action_rows.append([
                Paragraph(f"<b>{pri}</b>", ParagraphStyle("Pri", parent=styles["Normal"], fontSize=9, textColor=pri_color, fontName="Helvetica-Bold")),
                Paragraph(row.get("action", ""), body),
                Paragraph(f"<i>{row.get('because', '')}</i>", ParagraphStyle("Bec", parent=styles["Normal"], fontSize=8.5, leading=13, textColor=text_muted, fontName="Helvetica-Oblique")),

            ])
        action_table = Table(action_rows, colWidths=[page_width*0.15, page_width*0.40, page_width*0.45])
        action_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), mid_navy), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, light_grey]), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        elements.append(action_table)

    elements.append(Spacer(1, 0.3*inch))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e2e8f0")))
    elements.append(Spacer(1, 0.08*inch))
    elements.append(Paragraph(f"All signals verified from cited sources  |  Confidential  |  Internal Use Only  |  Generated {datetime.now().strftime('%d %B %Y %H:%M')}", footer_style))

    doc.build(elements)
    return FileResponse(path=filename, filename=f"{name}_Intelligence_Report.pdf", media_type="application/pdf")


@app.post("/chat")
async def chat(req: dict):
    customer_name = req.get("customer_name", "")
    question = req.get("question", "")
    
    # Fetch context from Supabase
    plants = supabase.table("plants").select("*").eq("customer_name", customer_name).execute().data or []
    stakeholders = supabase.table("stakeholders").select("*").eq("customer_name", customer_name).execute().data or []
    pipeline = supabase.table("expansion_pipeline").select("*").eq("customer_name", customer_name).execute().data or []
    
    # Fetch cached news intelligence
    today = datetime.now().strftime("%Y-%m-%d")
    cache = supabase.table("ai_cache").select("response").eq("cache_key", f"{customer_name}_overview_{today}").execute().data
    news_context = cache[0]["response"] if cache else "No recent news cached yet."
    
    system = f"""You are an AI assistant for Haber, a water treatment company. 
You help Corporate Account Managers understand their customers and make smart decisions.
You are currently helping with the account: {customer_name}.

Here is what you know about this customer:

PLANTS & DEPLOYMENT:
{json.dumps(plants, indent=2)}

STAKEHOLDERS:
{json.dumps(stakeholders, indent=2)}

EXPANSION PIPELINE:
{json.dumps(pipeline, indent=2)}

RECENT NEWS & INTELLIGENCE:
{news_context}

Answer questions in a clear, professional, and actionable way. 
Always tie your answers back to what it means for Haber specifically.
Keep answers concise — 3-5 sentences max unless a detailed answer is needed.
Never make up information. If you don't know something, say so."""

    answer = await call_gemini(question, system)
    return {"answer": answer, "customer": customer_name}


from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler()

async def send_weekly_reports():
    slack_webhook = os.getenv("SLACK_WEBHOOK_URL")
    if not slack_webhook:
        print("No Slack webhook configured")
        return
    try:
        customers = ["ITC", "JK Papers"]
        import httpx as _httpx
        for customer in customers:
            message = {
                "text": f":bar_chart: Weekly Customer Intelligence Report — {customer}",
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": f"Weekly Intelligence Report — {customer}"}
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f":calendar: *{datetime.now().strftime('%A, %d %B %Y')}*\n\nYour weekly customer intelligence report for *{customer}* is ready. View the latest news signals, implications for Haber, expansion opportunities, and recommended actions."
                        }
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": ":newspaper: *Whats Happening*\nLatest M&A, trends, headcount changes"},
                            {"type": "mrkdwn", "text": ":bulb: *What It Means*\nImplications for Haber specifically"},
                            {"type": "mrkdwn", "text": ":dart: *Opportunities*\nExpansion pipeline and whitespace"},
                            {"type": "mrkdwn", "text": ":warning: *Risk Flags*\nThings to watch out for"}
                        ]
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "View Dashboard"},
                                "url": "https://haber-cam.lovable.app",
                                "style": "primary"
                            }
                        ]
                    },
                    {"type": "divider"},
                    {
                        "type": "context",
                        "elements": [
                            {"type": "mrkdwn", "text": "_Sent automatically every Monday 10 AM IST by Haber Intelligence Platform_"}
                        ]
                    }
                ]
            }
            async with _httpx.AsyncClient() as client:
                await client.post(slack_webhook, json=message)
            print(f"Weekly Slack report sent for {customer}")
    except Exception as e:
        print(f"Error in weekly scheduler: {e}")

@app.on_event("startup")
async def start_scheduler():
    scheduler.add_job(
        send_weekly_reports,
        CronTrigger(day_of_week="mon", hour=4, minute=30, timezone="UTC"),
        id="weekly_reports",
        replace_existing=True
    )
    scheduler.start()
    print("Scheduler started — weekly reports every Monday 10 AM IST")

@app.on_event("shutdown")
async def stop_scheduler():
    scheduler.shutdown()

@app.get("/")
def root():
    return {"status": "Haber Intelligence API is running"}

